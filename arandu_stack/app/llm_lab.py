"""LLM Model Manager and Lab for Arandu Assistant.

Coordinates model catalog, atomic GGUF downloads, memory residency policies,
safe model switching with rollback, and cooperative headroom management for STT/TTS.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional

from aiohttp import ClientSession, ClientTimeout

from .llm_catalog import (
    DEFAULT_MODEL_ID,
    FALLBACK_LEGACY_MODEL_ID,
    MODELS,
    describe_model,
    get_model_file_path,
    get_model_info,
    is_installed,
    validate_model_id,
    verify_model_file,
)
from .llm_downloads import LLMDownloadManager
from .model_memory import ModelMemoryManager, ResidencyPolicy

_LOGGER = logging.getLogger(__name__)


class LLMModelManager:
    """Orquestrador principal do gerenciamento de modelos LLM e residência."""

    def __init__(
        self,
        data_dir: Path | str,
        upstream_port: int = 11570,
        total_memory_budget_mb: int = 2048,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir = self.data_dir / "modules/llm/models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.upstream_port = upstream_port
        self.downloader = LLMDownloadManager(self.models_dir)
        self.memory = ModelMemoryManager(
            data_dir=self.data_dir,
            total_memory_budget_mb=total_memory_budget_mb,
        )

        self.lock = asyncio.Lock()
        self.install_tasks: Dict[str, asyncio.Task] = {}
        self.configured_model_file = self.data_dir / "llm-configured-model.json"
        self.last_good_file = self.data_dir / "llm-last-good.json"

        self._active_model_id: Optional[str] = self.load_configured_model()

    def load_configured_model(self) -> str:
        """Carrega o modelo atualmente configurado ou seleciona o padrão."""
        if self.configured_model_file.exists():
            try:
                data = json.loads(self.configured_model_file.read_text(encoding='utf-8'))
                m = data.get('model_id')
                if m in MODELS:
                    return m
            except (OSError, ValueError):
                pass
        return DEFAULT_MODEL_ID

    def save_configured_model(self, model_id: str) -> None:
        """Persiste o identificador do modelo ativo."""
        validate_model_id(model_id)
        data = {'model_id': model_id, 'updated_at': time.time()}
        tmp = self.configured_model_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        tmp.replace(self.configured_model_file)
        self._active_model_id = model_id

    @property
    def active_model(self) -> str:
        return self._active_model_id or DEFAULT_MODEL_ID

    async def check_upstream_health(self) -> Optional[Dict[str, Any]]:
        """Consulta o endpoint HTTP do serviço LLM para verificar modelo carregado e prontidão."""
        url = f"http://127.0.0.1:{self.upstream_port}/status"
        try:
            async with ClientSession(timeout=ClientTimeout(total=2), trust_env=False) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status == 200:
                        return await response.json()
        except (OSError, TimeoutError):
            pass
        return None

    async def list_models(self) -> Dict[str, Any]:
        """Lista todos os modelos do catálogo com status de instalação, memória e residência."""
        rows: List[Dict[str, Any]] = []
        upstream = await self.check_upstream_health()
        active_upstream_model = upstream.get('model') if upstream else None

        for model_id in MODELS:
            row = describe_model(self.models_dir, model_id)
            progress = self.downloader.read_progress(model_id)

            if progress.get('state') in ('downloading', 'verifying'):
                row['state'] = progress['state']
                row['progress'] = progress

            row['policy'] = self.memory.get_policy(model_id).value
            row['estimated_memory_mb'] = self.memory.estimate_model_footprint(model_id)
            row['active'] = (model_id == self.active_model)
            row['upstream_loaded'] = (model_id == active_upstream_model)

            rows.append(row)

        return {
            'models': rows,
            'configured_model': self.active_model,
            'upstream_active': active_upstream_model,
            'memory': self.memory.describe(),
        }

    def start_install(
        self,
        model_id: str,
        custom_url: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Inicia tarefa de download em background sem bloquear o chamador."""
        validate_model_id(model_id)
        if model_id in self.install_tasks and not self.install_tasks[model_id].done():
            raise ValueError(f"Download do modelo {model_id} já está em andamento")

        async def _run():
            try:
                await self.downloader.download_model(
                    model_id,
                    custom_url=custom_url,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                _LOGGER.error("Falha no download de %s: %s", model_id, exc)

        task = asyncio.create_task(_run())
        self.install_tasks[model_id] = task
        return {'model': model_id, 'state': 'downloading', 'started': True}

    def cancel_install(self, model_id: str) -> Dict[str, Any]:
        """Cancela uma operação de download em andamento."""
        validate_model_id(model_id)
        cancelled = self.downloader.cancel_download(model_id)
        if model_id in self.install_tasks:
            self.install_tasks[model_id].cancel()
        return {'model': model_id, 'cancelled': cancelled}

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        """Remove os arquivos de um modelo instalado."""
        validate_model_id(model_id)
        # Descarrega da memória se registrado
        self.memory.register_unloaded(model_id)
        return self.downloader.delete_model(model_id, protected_model_id=self.active_model)

    def set_residency_policy(self, model_id: str, policy: str) -> Dict[str, Any]:
        """Configura a política de residência ('resident', 'lazy', 'standby')."""
        validate_model_id(model_id)
        self.memory.set_policy(model_id, policy)
        return {'model': model_id, 'policy': self.memory.get_policy(model_id).value}

    async def switch_model(
        self,
        model_id: str,
        confirm: bool = True,
        restart_callback: Optional[Callable[[str], Any]] = None,
    ) -> Dict[str, Any]:
        """Troca segura de modelo com checagem de espaço, evicção e rollback automático."""
        validate_model_id(model_id)
        if not confirm:
            raise ValueError("Confirmação explícita requerida para troca de modelo de produção")

        if not is_installed(self.models_dir, model_id):
            raise ValueError(f"Modelo {model_id} não está instalado. Baixe o modelo antes de selecioná-lo.")

        # Validação estrita dos pesos antes da troca
        model_path = get_model_file_path(self.models_dir, model_id)
        verify_model_file(model_path, model_id)

        async with self.lock:
            previous_model_id = self.active_model
            # Salvar backup last-good
            self.last_good_file.write_text(
                json.dumps({'model_id': previous_model_id, 'saved_at': time.time()}),
                encoding='utf-8',
            )

            # Evicção cooperativa de memória
            needed_mb = self.memory.estimate_model_footprint(model_id)
            evict_candidates = self.memory.get_eviction_candidates(needed_mb, target_model_id=model_id)
            for evict_m in evict_candidates:
                _LOGGER.info("Evictando modelo %s para liberar memória para %s", evict_m, model_id)
                self.memory.register_unloaded(evict_m)

            # Efetuar troca
            self.save_configured_model(model_id)

            # Se fornecido callback de reinicialização de processo, executa com monitoramento de prontidão
            if restart_callback:
                try:
                    res = restart_callback(model_id)
                    if asyncio.iscoroutine(res):
                        await res

                    # Espera prontidão
                    deadline = time.monotonic() + 45
                    ready = False
                    while time.monotonic() < deadline:
                        status = await self.check_upstream_health()
                        if status and status.get('model') == model_id and status.get('ready', True):
                            ready = True
                            break
                        await asyncio.sleep(0.5)

                    if not ready:
                        raise TimeoutError(f"O modelo {model_id} não confirmou prontidão no tempo limite")

                except Exception as exc:
                    _LOGGER.error("Falha ao ativar modelo %s; executando rollback para %s: %s", model_id, previous_model_id, exc)
                    self.save_configured_model(previous_model_id)
                    if restart_callback:
                        try:
                            fallback_res = restart_callback(previous_model_id)
                            if asyncio.iscoroutine(fallback_res):
                                await fallback_res
                        except Exception as fallback_exc:
                            _LOGGER.warning("Erro no callback de rollback para %s: %s", previous_model_id, fallback_exc)
                    raise ValueError(f"Falha na ativação do modelo {model_id}. Rollback executado para {previous_model_id}: {exc}") from exc

            self.memory.register_loaded(model_id)
            return {'model': model_id, 'active': True, 'switched': True}

    async def preload_resident_models(
        self,
        loader_callback: Optional[Callable[[str], Any]] = None,
    ) -> List[str]:
        """Pré-carrega todos os modelos instalados cuja política seja RESIDENT."""
        loaded: List[str] = []
        for model_id, policy in self.memory.policies.items():
            if policy == ResidencyPolicy.RESIDENT and is_installed(self.models_dir, model_id):
                footprint = self.memory.estimate_model_footprint(model_id)
                if self.memory.check_headroom(footprint):
                    _LOGGER.info("Pré-carregando modelo residente %s (%d MB)", model_id, footprint)
                    if loader_callback:
                        try:
                            res = loader_callback(model_id)
                            if asyncio.iscoroutine(res):
                                await res
                            self.memory.register_loaded(model_id)
                            loaded.append(model_id)
                        except Exception as exc:
                            _LOGGER.warning("Falha no pré-carregamento de %s: %s", model_id, exc)
                    else:
                        self.memory.register_loaded(model_id)
                        loaded.append(model_id)
                else:
                    _LOGGER.warning("Sem headroom de memória para pré-carregar %s", model_id)
        return loaded

    async def close(self) -> None:
        """Encerra graciosamente tarefas de download em andamento."""
        for model_id, task in list(self.install_tasks.items()):
            if not task.done():
                self.downloader.cancel_download(model_id)
                task.cancel()
        await asyncio.gather(*self.install_tasks.values(), return_exceptions=True)
        self.install_tasks.clear()

"""LLM GGUF Download and Lifecycle Manager for Arandu Assistant.

Handles atomic staging downloads, progressive SHA-256 validation,
disk space checking, install progress tracking, and safe file deletion.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, Optional
import urllib.request

from .llm_catalog import (
    MODELS,
    describe_model,
    get_model_file_path,
    get_model_info,
    is_installed,
    validate_model_id,
    verify_model_file,
)


class DownloadCancelledError(Exception):
    """Lançada quando a operação de download for explicitamente cancelada."""


class InsufficientDiskSpaceError(ValueError):
    """Lançada quando não houver espaço suficiente para o modelo e margem de segurança."""


class LLMDownloadManager:
    """Gerencia downloads atômicos, verificação de integridade e remoção de modelos GGUF."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.busy_lock = asyncio.Lock()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.progress_state: Dict[str, Dict[str, Any]] = {}
        self._cancellation_events: Dict[str, asyncio.Event] = {}

    def get_progress_file(self, model_id: str) -> Path:
        """Caminho do arquivo de progresso em disco."""
        return self.root / f".install-{model_id}.json"

    def read_progress(self, model_id: str) -> Dict[str, Any]:
        """Lê o estado de progresso em memória ou a partir do arquivo de progresso."""
        if model_id in self.progress_state:
            return dict(self.progress_state[model_id])
        pfile = self.get_progress_file(model_id)
        if pfile.exists():
            try:
                return json.loads(pfile.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        installed = is_installed(self.root, model_id)
        return {
            'model': model_id,
            'state': 'installed' if installed else 'available',
            'downloaded_bytes': MODELS[model_id]['size'] if installed else 0,
            'total_bytes': MODELS[model_id]['size'],
            'percent': 100.0 if installed else 0.0,
        }

    def _update_progress(
        self,
        model_id: str,
        state: str,
        downloaded: int = 0,
        total: int = 0,
        error: Optional[str] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Atualiza o estado em memória e grava no arquivo de progresso atomicamente."""
        percent = round((downloaded / total * 100.0), 2) if total > 0 else 0.0
        data: Dict[str, Any] = {
            'model': model_id,
            'state': state,
            'downloaded_bytes': downloaded,
            'total_bytes': total,
            'percent': percent,
            'updated_at': time.time(),
        }
        if error:
            data['error'] = error
        self.progress_state[model_id] = data

        pfile = self.get_progress_file(model_id)
        try:
            tmp = pfile.with_suffix('.tmp')
            tmp.write_text(json.dumps(data), encoding='utf-8')
            tmp.replace(pfile)
        except OSError:
            pass

        if callback:
            try:
                callback(data)
            except Exception:
                pass

    def check_disk_space(self, model_id: str, margin_mb: int = 64) -> None:
        """Verifica se há espaço livre no disco antes de iniciar o download."""
        info = get_model_info(model_id)
        required = info['size'] + margin_mb * 1024 * 1024
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as exc:
            raise ValueError(f"Não foi possível consultar espaço em disco: {exc}") from exc

        if free < required:
            free_mb = free // (1024 * 1024)
            required_mb = required // (1024 * 1024)
            raise InsufficientDiskSpaceError(
                f"Espaço em disco insuficiente para {model_id}: livre {free_mb} MB, necessário {required_mb} MB"
            )

    async def download_model(
        self,
        model_id: str,
        timeout: int = 900,
        chunk_size: int = 1024 * 1024,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        custom_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Baixa o modelo de forma assíncrona com staging, verificação e promoção atômica."""
        validate_model_id(model_id)
        info = get_model_info(model_id)

        # Se já estiver instalado e íntegro, retorna imediatamente
        if is_installed(self.root, model_id):
            try:
                verify_model_file(get_model_file_path(self.root, model_id), model_id)
                self._update_progress(model_id, 'installed', info['size'], info['size'], callback=progress_callback)
                return {'model': model_id, 'installed': True, 'state': 'installed'}
            except (ValueError, FileNotFoundError):
                # Se arquivo existente estiver corrompido, rebaixa
                pass

        self.check_disk_space(model_id)

        cancel_event = self._cancellation_events.get(model_id) or asyncio.Event()
        self._cancellation_events[model_id] = cancel_event
        if cancel_event.is_set():
            raise DownloadCancelledError(f"Download do modelo {model_id} cancelado pelo usuário")

        self._update_progress(model_id, 'downloading', 0, info['size'], callback=progress_callback)

        url = custom_url or f"https://huggingface.co/{info['repo']}/resolve/{info['revision']}/{info['file']}"

        def _blocking_download() -> Path:
            with tempfile.TemporaryDirectory(prefix=f"llm-staging-{model_id}-", dir=self.root) as tmp_dir:
                tmp_path = Path(tmp_dir) / info['file']
                hasher = hashlib.sha256()
                req = urllib.request.Request(url, headers={'User-Agent': 'Arandu-Stack-LLM-Manager/1.0'})

                downloaded = 0
                deadline = time.monotonic() + timeout

                with urllib.request.urlopen(req, timeout=30) as resp, tmp_path.open('wb') as f:
                    while True:
                        if cancel_event.is_set():
                            raise DownloadCancelledError(f"Download do modelo {model_id} cancelado pelo usuário")
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Download do modelo {model_id} excedeu o timeout de {timeout}s")

                        block = resp.read(chunk_size)
                        if not block:
                            break
                        f.write(block)
                        hasher.update(block)
                        downloaded += len(block)
                        if downloaded > info['size']:
                            raise ValueError(f"Download excedeu o tamanho previsto ({info['size']} bytes)")

                        self._update_progress(model_id, 'downloading', downloaded, info['size'], callback=progress_callback)

                    f.flush()
                    os.fsync(f.fileno())

                # Verificação
                self._update_progress(model_id, 'verifying', downloaded, info['size'], callback=progress_callback)
                if downloaded != info['size']:
                    raise ValueError(f"Tamanho divergente: obtido {downloaded}, esperado {info['size']}")

                digest = hasher.hexdigest()
                if digest != info['sha256']:
                    raise ValueError(f"SHA-256 divergente: obtido {digest}, esperado {info['sha256']}")

                # Validar cabeçalho GGUF
                with tmp_path.open('rb') as f:
                    header = f.read(4)
                    if header != b'GGUF':
                        raise ValueError(f"Arquivo baixado não contém cabeçalho GGUF: {header!r}")

                # Salvar metadados do modelo
                meta = {
                    'id': model_id,
                    'name': info['name'],
                    'repo': info['repo'],
                    'revision': info['revision'],
                    'file': info['file'],
                    'size': info['size'],
                    'sha256': info['sha256'],
                    'installed_at': time.time(),
                }
                meta_file = Path(tmp_dir) / 'arandu-model.json'
                meta_file.write_text(json.dumps(meta, indent=2), encoding='utf-8')

                # Promoção atômica para pasta final
                dest_dir = self.root / model_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / info['file']

                os.replace(tmp_path, dest_file)
                os.replace(meta_file, dest_dir / 'arandu-model.json')
                return dest_file

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _blocking_download)
            self._update_progress(model_id, 'installed', info['size'], info['size'], callback=progress_callback)
            return {'model': model_id, 'installed': True, 'state': 'installed'}
        except Exception as exc:
            state = 'cancelled' if isinstance(exc, DownloadCancelledError) else 'error'
            error_msg = str(exc)
            self._update_progress(model_id, state, 0, info['size'], error=error_msg, callback=progress_callback)
            raise
        finally:
            self._cancellation_events.pop(model_id, None)

    def cancel_download(self, model_id: str) -> bool:
        """Sinaliza cancelamento para a tarefa de download em andamento."""
        if model_id not in self._cancellation_events:
            self._cancellation_events[model_id] = asyncio.Event()
        self._cancellation_events[model_id].set()
        return True

    def delete_model(self, model_id: str, protected_model_id: Optional[str] = None) -> Dict[str, Any]:
        """Remove os arquivos e diretório do modelo com segurança."""
        validate_model_id(model_id)
        if protected_model_id and model_id == protected_model_id:
            raise ValueError(f"Não é permitido remover o modelo ativo em produção ({model_id})")

        dest_dir = self.root / model_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)

        self.get_progress_file(model_id).unlink(missing_ok=True)
        self.progress_state.pop(model_id, None)
        return {'model': model_id, 'deleted': True, 'state': 'available'}

"""Model Memory and Residency Manager for Arandu Assistant.

Implements persistent model residency policies (RESIDENT, LAZY, STANDBY),
memory budgeting, KV-cache footprint estimation, cooperative headroom allocation
with STT/TTS pipelines, and graceful eviction.
"""
from __future__ import annotations

import enum
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set

from .llm_catalog import MODELS, get_model_info, validate_model_id


class ResidencyPolicy(str, enum.Enum):
    """Políticas de residência em memória para modelos LLM."""
    RESIDENT = "resident"  # Sempre carregado na RAM, zero cold-start
    LAZY = "lazy"          # Carregado sob demanda na primeira inferência
    STANDBY = "standby"    # Apenas metadados, pesos descarregados


class ModelMemoryManager:
    """Controla orçamento de memória RAM, residência persistente e evicção cooperativa."""

    def __init__(
        self,
        data_dir: Path | str,
        total_memory_budget_mb: int = 2048,
        stt_reserved_mb: int = 512,
        tts_reserved_mb: int = 256,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.total_budget_mb = total_memory_budget_mb
        self.stt_reserved_mb = stt_reserved_mb
        self.tts_reserved_mb = tts_reserved_mb

        # Dicionário de políticas por modelo: model_id -> ResidencyPolicy
        self.policies: Dict[str, ResidencyPolicy] = {}
        # Estado atual de carregamento: model_id -> {'loaded': bool, 'loaded_at': float, 'last_used': float, 'rss_mb': int}
        self.loaded_models: Dict[str, Dict[str, Any]] = {}

        self.config_file = self.data_dir / "model-residency.json"
        self.load_policies()

    def load_policies(self) -> None:
        """Carrega políticas salvas em disco ou inicializa padrões."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding='utf-8'))
                for model_id, policy_str in data.get('policies', {}).items():
                    if model_id in MODELS:
                        try:
                            self.policies[model_id] = ResidencyPolicy(policy_str)
                        except ValueError:
                            pass
                self.total_budget_mb = data.get('total_budget_mb', self.total_budget_mb)
                self.stt_reserved_mb = data.get('stt_reserved_mb', self.stt_reserved_mb)
                self.tts_reserved_mb = data.get('tts_reserved_mb', self.tts_reserved_mb)
                return
            except (OSError, ValueError):
                pass

        # Padrões se não houver arquivo salvo:
        # LFM2.5 (recomendado) ou Tucano (legado) como RESIDENT por padrão
        self.policies['lfm2.5-1.2b'] = ResidencyPolicy.RESIDENT
        self.policies['lfm3-1.7b'] = ResidencyPolicy.LAZY
        self.policies['qwen3-1.7b'] = ResidencyPolicy.LAZY
        self.policies['tucano'] = ResidencyPolicy.STANDBY
        self.save_policies()

    def save_policies(self) -> None:
        """Persiste as políticas de residência e configurações de memória."""
        data = {
            'policies': {m: p.value for m, p in self.policies.items()},
            'total_budget_mb': self.total_budget_mb,
            'stt_reserved_mb': self.stt_reserved_mb,
            'tts_reserved_mb': self.tts_reserved_mb,
            'updated_at': time.time(),
        }
        try:
            tmp = self.config_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
            tmp.replace(self.config_file)
        except OSError:
            pass

    def get_policy(self, model_id: str) -> ResidencyPolicy:
        """Retorna a política de residência configurada para o modelo."""
        validate_model_id(model_id)
        return self.policies.get(model_id, ResidencyPolicy.LAZY)

    def set_policy(self, model_id: str, policy: ResidencyPolicy | str) -> None:
        """Atualiza e persiste a política de residência de um modelo."""
        validate_model_id(model_id)
        if isinstance(policy, str):
            policy = ResidencyPolicy(policy)
        self.policies[model_id] = policy
        self.save_policies()

    def estimate_model_footprint(self, model_id: str, context_size: Optional[int] = None) -> int:
        """Calcula a estimativa de consumo de memória RAM (MB) do modelo + KV-Cache."""
        validate_model_id(model_id)
        info = get_model_info(model_id)
        ctx = context_size or info.get('default_context', 4096)

        # Tamanho base dos pesos em MB
        weights_mb = info['size'] // (1024 * 1024)

        # Estimativa de KV cache e overhead de runtime llama.cpp:
        # LFM tem arquitetura híbrida com KV cache drasticamente menor
        if info.get('family') == 'liquid':
            # GQA + convoluções curtas consom ~30% do KV cache de transformers puros
            kv_cache_mb = int((ctx / 4096) * 64)
            runtime_overhead_mb = 96
        else:
            # Qwen / Transformers convencionais
            kv_cache_mb = int((ctx / 4096) * 160)
            runtime_overhead_mb = 128

        total_est = weights_mb + kv_cache_mb + runtime_overhead_mb
        # Limita ao teto informado no catálogo se maior
        return max(total_est, info.get('memory_limit_mb', total_est))

    def get_llm_available_budget(self) -> int:
        """Retorna a memória líquida disponível para LLMs após reservas de STT e TTS."""
        net = self.total_budget_mb - (self.stt_reserved_mb + self.tts_reserved_mb)
        return max(256, net)

    def get_current_llm_usage(self) -> int:
        """Soma a memória dos modelos LLM atualmente marcados como carregados."""
        total = 0
        for model_id, state in self.loaded_models.items():
            if state.get('loaded'):
                total += state.get('rss_mb', self.estimate_model_footprint(model_id))
        return total

    def check_headroom(self, required_mb: int) -> bool:
        """Verifica se há margem suficiente dentro do orçamento para alocar a memória requerida."""
        available = self.get_llm_available_budget()
        current = self.get_current_llm_usage()
        return (current + required_mb) <= available

    def register_loaded(self, model_id: str, pid: Optional[int] = None, rss_mb: Optional[int] = None) -> None:
        """Registra que um modelo foi carregado na memória."""
        validate_model_id(model_id)
        real_rss = rss_mb or self.get_process_rss_mb(pid) if pid else self.estimate_model_footprint(model_id)
        now = time.time()
        self.loaded_models[model_id] = {
            'loaded': True,
            'pid': pid,
            'rss_mb': real_rss,
            'loaded_at': now,
            'last_used': now,
        }

    def register_unloaded(self, model_id: str) -> None:
        """Registra que um modelo foi descarregado da memória."""
        if model_id in self.loaded_models:
            self.loaded_models[model_id]['loaded'] = False
            self.loaded_models[model_id]['rss_mb'] = 0

    def touch_model(self, model_id: str) -> None:
        """Atualiza timestamp de último uso para políticas LRU."""
        if model_id in self.loaded_models:
            self.loaded_models[model_id]['last_used'] = time.time()

    def get_eviction_candidates(self, needed_mb: int, target_model_id: str) -> List[str]:
        """Identifica quais modelos descarregar para acomodar o modelo alvo.

        Prioridade de retenção:
        1. RESIDENT: Nunca evictado a menos que o próprio target seja outro RESIDENT e não haja espaço.
        2. LAZY: Evictado por ordem LRU (menos recentemente usado primeiro).
        3. STANDBY: Sempre evictado se estiver carregado.
        """
        available = self.get_llm_available_budget()
        current = self.get_current_llm_usage()
        free_space = available - current

        if free_space >= needed_mb:
            return []

        needed_to_free = needed_mb - free_space
        candidates: List[str] = []
        freed = 0

        # Lista de modelos atualmente carregados excluindo o alvo
        loaded = [
            m for m, s in self.loaded_models.items()
            if s.get('loaded') and m != target_model_id
        ]

        # 1. Primeiro tenta descarregar modelos STANDBY
        for m in loaded:
            if self.get_policy(m) == ResidencyPolicy.STANDBY:
                candidates.append(m)
                freed += self.loaded_models[m].get('rss_mb', self.estimate_model_footprint(m))
                if freed >= needed_to_free:
                    return candidates

        # 2. Em seguida, descarrega modelos LAZY ordenados por last_used crescente (LRU)
        lazy_models = [m for m in loaded if self.get_policy(m) == ResidencyPolicy.LAZY and m not in candidates]
        lazy_models.sort(key=lambda m: self.loaded_models[m].get('last_used', 0))

        for m in lazy_models:
            candidates.append(m)
            freed += self.loaded_models[m].get('rss_mb', self.estimate_model_footprint(m))
            if freed >= needed_to_free:
                return candidates

        # Se ainda não for suficiente e o target for prioritário, descarrega outros RESIDENT
        if self.get_policy(target_model_id) == ResidencyPolicy.RESIDENT:
            resident_models = [m for m in loaded if self.get_policy(m) == ResidencyPolicy.RESIDENT and m not in candidates]
            resident_models.sort(key=lambda m: self.loaded_models[m].get('last_used', 0))
            for m in resident_models:
                candidates.append(m)
                freed += self.loaded_models[m].get('rss_mb', self.estimate_model_footprint(m))
                if freed >= needed_to_free:
                    return candidates

        return candidates

    def get_process_rss_mb(self, pid: Optional[int]) -> int:
        """Lê o RSS real em MB do processo."""
        if not pid:
            return 0
        try:
            status_path = Path(f"/proc/{pid}/status")
            if status_path.exists():
                for line in status_path.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return int(parts[1]) // 1024
        except (OSError, ValueError, IndexError):
            pass
        return 0

    def describe(self) -> Dict[str, Any]:
        """Retorna o panorama completo de alocação de memória e estado de residência."""
        llm_usage = self.get_current_llm_usage()
        llm_budget = self.get_llm_available_budget()
        return {
            'total_budget_mb': self.total_budget_mb,
            'stt_reserved_mb': self.stt_reserved_mb,
            'tts_reserved_mb': self.tts_reserved_mb,
            'llm_budget_mb': llm_budget,
            'llm_used_mb': llm_usage,
            'llm_free_mb': max(0, llm_budget - llm_usage),
            'loaded_models': {
                m: {
                    'loaded': s['loaded'],
                    'rss_mb': s.get('rss_mb', 0),
                    'policy': self.get_policy(m).value,
                    'last_used': s.get('last_used'),
                }
                for m, s in self.loaded_models.items()
            },
            'policies': {m: self.get_policy(m).value for m in MODELS},
        }

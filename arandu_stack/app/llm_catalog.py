"""LLM Model Catalog for Arandu Assistant.

Provides verified public GGUF model registry for liquid foundation models (LFM),
Qwen3 and legacy Tucano, with strict hash pinning, metadata and license descriptors.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

# Catálogo oficial dos modelos suportados pela Fase 4 e legados
MODELS: Dict[str, Dict[str, Any]] = {
    'lfm2.5-1.2b': {
        'id': 'lfm2.5-1.2b',
        'name': 'LFM2.5-1.2B-Instruct QAD-Q4_0',
        'family': 'liquid',
        'parameters': '1.2B',
        'kind': 'llm',
        'license': 'LFM Open License',
        'repo': 'LiquidAI/LFM2.5-1.2B-Instruct-GGUF',
        'revision': '8ed288026e23958ad9dfa92d53ed773a8eee7125',
        'file': 'LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf',
        'size': 695755488,
        'sha256': 'bb741ebb106d543e9de114b843a3d3d73d51c74b5801e69da2abde821a0cb3e1',
        'runtime': 'llama.cpp / CPU',
        'thinking': False,
        'experimental': False,
        'recommended': True,
        'memory_limit_mb': 1024,
        'default_context': 4096,
        'max_context': 32768,
        'quantization': 'QAD-Q4_0',
        'description': 'Modelo híbrido (convolução + GQA) ultraeficiente com Quantization-Aware Distillation.',
    },
    'lfm3-1.7b': {
        'id': 'lfm3-1.7b',
        'name': 'LFM3-1.7B QAD-Q4_0',
        'family': 'liquid',
        'parameters': '1.7B',
        'kind': 'llm',
        'license': 'LFM Open License',
        'repo': 'LiquidAI/LFM3-1.7B-GGUF',
        'revision': 'cd0c74c575b5c416f3011b5b4c298077f26ca8c2',
        'file': 'LFM3-1.7B-QAD-Q4_0.gguf',
        'size': 1049615680,
        'sha256': '43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452',
        'runtime': 'llama.cpp / CPU',
        'thinking': False,
        'experimental': False,
        'recommended': False,
        'memory_limit_mb': 1536,
        'default_context': 4096,
        'max_context': 262144,
        'quantization': 'QAD-Q4_0',
        'description': 'Arquitetura tri-híbrida Liquid AI otimizada para chamadas de ferramentas e agentes.',
    },
    'qwen3-1.7b': {
        'id': 'qwen3-1.7b',
        'name': 'Qwen3-1.7B Q4_K_M',
        'family': 'qwen',
        'parameters': '1.7B',
        'kind': 'llm',
        'license': 'Apache-2.0',
        'repo': 'bartowski/Qwen_Qwen3-1.7B-GGUF',
        'revision': 'dcb19155b962dbb6389f4691a982043a8e651022',
        'file': 'Qwen_Qwen3-1.7B-Q4_K_M.gguf',
        'size': 1282439584,
        'sha256': '72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb',
        'runtime': 'llama.cpp / CPU',
        'thinking': True,
        'experimental': True,
        'recommended': False,
        'memory_limit_mb': 1800,
        'default_context': 4096,
        'max_context': 32768,
        'quantization': 'Q4_K_M',
        'description': 'Qwen3 1.7B com raciocínio analítico estruturado e quantização k-quant balanceada.',
    },
    'tucano': {
        'id': 'tucano',
        'name': 'Tucano 2 Qwen 0.5B Instruct Q4_K_M',
        'family': 'tucano',
        'parameters': '0.5B',
        'kind': 'llm',
        'license': 'Apache-2.0',
        'repo': '2DTi/tucano2-0.5b-instruct-gguf',
        'revision': 'cbb5318b66a49e20967623e946365bcc8594c29a',
        'file': 'tucano2-0.5b-instruct-Q4_K_M.gguf',
        'size': 305603296,
        'sha256': '49f16e9d27db128a39a3d976271a297f1ef183b763c62ca9a0c0ba7c72c55c91',
        'runtime': 'llama.cpp / CPU',
        'thinking': False,
        'experimental': False,
        'recommended': False,
        'legacy': True,
        'memory_limit_mb': 512,
        'default_context': 2048,
        'max_context': 8192,
        'quantization': 'Q4_K_M',
        'description': 'Modelo compacto legado de 0.5B pré-treinado para português brasileiro.',
    },
}

DEFAULT_MODEL_ID = 'lfm2.5-1.2b'
FALLBACK_LEGACY_MODEL_ID = 'tucano'


def validate_model_id(model_id: str) -> None:
    """Valida se o identificador do modelo existe no catálogo."""
    if not isinstance(model_id, str) or model_id not in MODELS:
        raise ValueError(f"Modelo LLM '{model_id}' não reconhecido. Opções: {list(MODELS.keys())}")


def get_model_info(model_id: str) -> Dict[str, Any]:
    """Retorna os metadados imutáveis do modelo."""
    validate_model_id(model_id)
    return dict(MODELS[model_id])


def get_model_file_path(root: Path | str, model_id: str) -> Path:
    """Retorna o caminho previsto do arquivo GGUF instalado."""
    info = get_model_info(model_id)
    return Path(root) / model_id / info['file']


def is_installed(root: Path | str, model_id: str) -> bool:
    """Verifica se o arquivo do modelo está presente com o tamanho correto."""
    validate_model_id(model_id)
    info = MODELS[model_id]
    path = Path(root) / model_id / info['file']
    return path.is_file() and path.stat().st_size == info['size']


def verify_model_file(path: Path | str, model_id: str) -> Path:
    """Valida o cabeçalho GGUF, tamanho de arquivo e hash SHA-256."""
    validate_model_id(model_id)
    info = MODELS[model_id]
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"Arquivo do modelo não encontrado: {path}")

    size = path.stat().st_size
    if size != info['size']:
        raise ValueError(
            f"Tamanho GGUF divergente para {model_id}: esperado {info['size']} bytes, obtido {size} bytes"
        )

    hasher = hashlib.sha256()
    with path.open('rb') as f:
        header = f.read(4)
        if header != b'GGUF':
            raise ValueError(f"Cabeçalho inválido no arquivo: esperado 'GGUF', obtido {header!r}")
        f.seek(0)
        for block in iter(lambda: f.read(1024 * 1024), b''):
            hasher.update(block)

    digest = hasher.hexdigest()
    if digest != info['sha256']:
        raise ValueError(
            f"Integridade SHA-256 violada para {model_id}: esperado {info['sha256']}, calculado {digest}"
        )

    return path


def describe_model(root: Path | str, model_id: str) -> Dict[str, Any]:
    """Retorna a descrição detalhada do estado do modelo no disco."""
    info = get_model_info(model_id)
    installed = is_installed(root, model_id)
    target_path = Path(root) / model_id / info['file']

    result = {
        'id': model_id,
        'name': info['name'],
        'family': info['family'],
        'parameters': info['parameters'],
        'license': info['license'],
        'size': info['size'],
        'sha256': info['sha256'],
        'runtime': info['runtime'],
        'thinking': info['thinking'],
        'recommended': info.get('recommended', False),
        'legacy': info.get('legacy', False),
        'memory_limit_mb': info['memory_limit_mb'],
        'installed': installed,
        'state': 'installed' if installed else 'available',
        'file': info['file'],
        'repo': info['repo'],
        'revision': info['revision'],
        'path': str(target_path) if installed else None,
        'description': info['description'],
    }
    return result

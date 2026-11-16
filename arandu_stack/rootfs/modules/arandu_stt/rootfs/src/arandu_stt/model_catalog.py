"""Allowlisted local STT models and verified, atomic installation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from model_download import ADAPTED_VERSION, ADAPTED_HASHES, ADAPTED_URL, ensure_model, sha256

CATALOG = {
    'fastconformer': {'name': 'Arandu FastConformer', 'runtime': 'sherpa-onnx',
        'repo': 'csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8', 'license': 'CC-BY-NC-4.0'},
    **{f'whisper_{size}': {'name': f'Whisper {size.title()}', 'runtime': 'faster-whisper',
        'repo': f'Systran/faster-whisper-{size}', 'license': 'MIT'} for size in ('tiny', 'base', 'small')},
}
CATALOG.update({'parakeet_ptbr_30s': {'name': 'Parakeet TDT V3 pt-BR 30s',
                       'repo': 'alefiury/parakeet-tdt-v3-ptBR-30s-onnx',
                       'runtime': 'onnx-asr',
                       'architecture': 'FastConformer TDT',
                       'compute': 'fp32',
                       'license': 'CC-BY-4.0',
                       'revision': 'ba4da45f60395f13b12da197d3448e66f6ec79c7',
                       'files': ['config.json',
                                 'decoder_joint-model.onnx',
                                 'encoder-model.onnx',
                                 'encoder-model.onnx.data',
                                 'nemo128.onnx',
                                 'vocab.txt'],
                       'sha256': {'decoder_joint-model.onnx': '5c29e4daf27e5e150bd4f1e98ad5cfdc777b653b8aa144e6451b9ff375091ff7',
                                  'encoder-model.onnx': '4820e7005ed1c5587496162be950f3966ab349956076a8230ca8e5b8d8d20dab',
                                  'encoder-model.onnx.data': 'f128db4a467ee210262bac470e6a6b94637d510e76c7268b6c781322213fbeaa',
                                  'nemo128.onnx': 'a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f'},
                       'download_bytes': 2550017803,
                       'installed_bytes': 2550017803},
 'parakeet_tagarela': {'name': 'Parakeet TAGARELA pt-BR',
                       'repo': 'alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx',
                       'runtime': 'onnx-asr',
                       'architecture': 'FastConformer TDT',
                       'compute': 'fp32',
                       'license': 'CC-BY-4.0',
                       'revision': '47586335c0b00ba273205f0ed3ef5a2cd651c143',
                       'files': ['config.json',
                                 'decoder_joint-model.onnx',
                                 'encoder-model.onnx',
                                 'encoder-model.onnx.data',
                                 'nemo128.onnx',
                                 'vocab.txt'],
                       'sha256': {'decoder_joint-model.onnx': '7ffb7866c27dbd2d983ec82e2780dbb2669f451d33ea0491ac7a81fce5f84ba9',
                                  'encoder-model.onnx': '5e6470dc3c6f0bcb5f99dbf3d64285ac52a0e991b3bddcf634e34bbe60d9d6f0',
                                  'encoder-model.onnx.data': '21bee798dceaaff37a31ef2b56910eaed90b71ee2be143d8bda3b7be3f549dd7',
                                  'nemo128.onnx': 'a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f'},
                       'download_bytes': 2550017801,
                       'installed_bytes': 2550017801},
 'orukeet_int8': {'name': 'Orukeet INT8',
                  'repo': 'oruk/orukeet',
                  'runtime': 'sherpa-onnx',
                  'architecture': 'FastConformer TDT transducer',
                  'compute': 'int8',
                  'license': 'CC-BY-SA-4.0',
                  'revision': '55a984d46f68323301837194ce647c702f55facc',
                  'archive': 'onnx/sherpa-onnx-orukeet-v0.1.0-int8.tar.bz2',
                  'archive_sha256': 'f9191f30178cc9122ce2f023bf9fefafc822028307b0efa4caff645ba3fe8d0a',
                  'files': ['encoder.int8.onnx',
                            'decoder.int8.onnx',
                            'joiner.int8.onnx',
                            'tokens.txt',
                            'bpe.vocab',
                            'LICENSE-WEIGHTS',
                            'NOTICE.md'],
                  'sha256': {'encoder.int8.onnx': '7b55f2a504a20a8e462899f5befd45f4a1784948d76ed0127902d9cf39405487', 'decoder.int8.onnx': 'c185c2afb4c77c94bb1314807ecb3dc1623057a3dc540b83e10301af9bf4cfca', 'joiner.int8.onnx': '1a7e90abf7172d926dd7e2edac2a5d5035c24dfb641a15e131d57b6a5f63cdd3', 'tokens.txt': 'd58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d', 'bpe.vocab': '41d5e71b3591642eff088151efd7acd4e750124cc0054c8ba9fa3245187a4804', 'LICENSE-WEIGHTS': '23ee78c8bae49cf08ea2f0c84945c66b987ebe4520881fb51b3dad4fb43d07c2', 'NOTICE.md': '440361d963edd9621e744f251332b47f2c4de2e2594ecfe42b215e3f6223fa44'},
                  'download_bytes': 486807585,
                  'installed_bytes': 671619800},
 'granite_turboctc_q4': {'name': 'Granite Speech 5 TurboCTC Q4_K_M',
                         'repo': 'vladers/granite-speech-5.0-470m-turboctc-GGUF',
                         'runtime': 'transcribe.cpp',
                         'architecture': 'Granite TurboCTC',
                         'compute': 'Q4_K_M',
                         'license': 'Apache-2.0',
                         'languages': ['en', 'fr', 'de', 'es', 'pt', 'it', 'ja'],
                         'revision': '88845afaa7beadca4b9ec545df0dd7332349ab88',
                         'files': ['granite-speech-5.0-470m-turboctc-Q4_K_M.gguf', 'LICENSE', 'NOTICE'],
                         'sha256': {'granite-speech-5.0-470m-turboctc-Q4_K_M.gguf': '959646de88d43100a82459da1c0c9d3a2b1a7536b54338b9c4fc9535bdb30970'},
                         'download_bytes': 279183655,
                         'installed_bytes': 279183655},
 'parakeet_ultra_q8': {'name': 'Parakeet Ultra Q8_0 (recomendado)',
                       'repo': 'handy-computer/parakeet-ultra-gguf',
                       'runtime': 'transcribe.cpp',
                       'architecture': 'Parakeet Ultra CTC/TDT',
                       'compute': 'Q8_0',
                       'license': 'CC-BY-4.0',
                       'languages': ['pt', 'en', 'es', 'fr', 'de'],
                       'revision': 'a1539be1b849ea7463b88343e5277b373d0c4ea9',
                       'files': ['parakeet-ultra-Q8_0.gguf', 'LICENSE'],
                       'sha256': {'parakeet-ultra-Q8_0.gguf': '007a59761e9258779f189df396b50d12b83bb5b79e66df4b955c230d2f2a0a59'},
                       'memory_limit_mb': 1600,
                       'estimated_ram_mb': 950,
                       'download_bytes': 740381825,
                       'installed_bytes': 740381825},
 'parakeet_ultra_q4': {'name': 'Parakeet Ultra Q4_K_M (variante compacta)',
                       'repo': 'handy-computer/parakeet-ultra-gguf',
                       'runtime': 'transcribe.cpp',
                       'architecture': 'Parakeet Ultra CTC/TDT',
                       'compute': 'Q4_K_M',
                       'license': 'CC-BY-4.0',
                       'languages': ['pt', 'en', 'es', 'fr', 'de'],
                       'revision': 'a1539be1b849ea7463b88343e5277b373d0c4ea9',
                       'files': ['parakeet-ultra-Q4_K_M.gguf', 'LICENSE'],
                       'sha256': {'parakeet-ultra-Q4_K_M.gguf': 'c69b7a5f9071a7afd8a1818ef7ae542c386c4b01ed0cac380c19a99947646bf9'},
                       'memory_limit_mb': 1200,
                       'estimated_ram_mb': 650,
                       'download_bytes': 486298753,
                       'installed_bytes': 486298753},
 'phonon_2_5': {'name': 'Phonon 2.5 (CPU compacto)',
                'repo': 'FermionResearch/Phonon-2.5',
                'runtime': 'fermion-research',
                'architecture': 'Phonon 2.5 FSQ',
                'compute': 'fsq',
                'license': 'CC-BY-4.0',
                'languages': ['auto', 'pt'],
                'revision': '2af4bb8ccceb66a8ce06c4081bb968f1802c1e58',
                'files': ['phonon-2.5.fsq'],
                'sha256': {'phonon-2.5.fsq': '49da2dbe8301433caf7486368afa98c8d1a0e0477fbbc465895713ff6b510a66'},
                'memory_limit_mb': 1024,
                'estimated_ram_mb': 420,
                'download_bytes': 156181652,
                'installed_bytes': 156181652},
 'nemotron_unified_0_6b': {'name': 'NVIDIA Nemotron Unified ASR 0.6B (experimental)',
                           'repo': 'nvidia/nemotron-labs-unified-asr-0.6b',
                           'runtime': 'nvidia-nemo-isolated',
                           'architecture': 'FastConformer-RNNT',
                           'compute': 'fp32',
                           'license': 'OpenMDW-1.1',
                           'language': 'pt',
                           'language_fixed': True,
                           'language_label': 'Português — fixo',
                           'languages': ['pt', 'auto'],
                           'revision': '023ce907267a09a308a5cb69239447cb3d0c1597',
                           'files': ['nemotron-labs-unified-asr-0.6b.nemo'],
                           'sha256': {'nemotron-labs-unified-asr-0.6b.nemo': '1718cc1b44fb8597b2356328d706a8d89f31c389ca6f2256c2e3f743c0246936'},
                           'file_sizes': {'nemotron-labs-unified-asr-0.6b.nemo': 2507735040},
                           'memory_limit_mb': 4608,
                           'estimated_ram_mb': 3600,
                           'experimental': True,
                           'download_bytes': 2507735040,
                           'installed_bytes': 2507735040},
               'whisper_cpp_small': {'name': 'Whisper Small — whisper.cpp',
                                     'repo': 'ggerganov/whisper.cpp',
                                     'runtime': 'whisper.cpp',
                                     'architecture': 'Whisper Multilingual Transformer',
                                     'compute': 'fp16-ggml',
                                     'license': 'MIT',
                                     'revision': '5359861c739e955e79d9a303bcbc70fb988958b1',
                                     'files': ['ggml-small.bin'],
                                     'sha256': {'ggml-small.bin': '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'},
                                     'file_sizes': {'ggml-small.bin': 487601967},
                                     'download_bytes': 487601967,
                                     'installed_bytes': 487601967,
                                     'memory_limit_mb': 1800,
                                     'estimated_ram_mb': 850,
                                     'language': 'pt',
                                     'language_fixed': True,
                                     'language_label': 'Português — fixo'},
               'whisper_cpp_medium': {'name': 'Whisper Medium — whisper.cpp',
                                      'repo': 'ggerganov/whisper.cpp',
                                      'runtime': 'whisper.cpp',
                                      'architecture': 'Whisper Multilingual Transformer',
                                      'compute': 'fp16-ggml',
                                      'license': 'MIT',
                                      'revision': '5359861c739e955e79d9a303bcbc70fb988958b1',
                                      'files': ['ggml-medium.bin'],
                                      'sha256': {'ggml-medium.bin': '6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208'},
                                      'file_sizes': {'ggml-medium.bin': 1533763059},
                                      'download_bytes': 1533763059,
                                      'installed_bytes': 1533763059,
                                      'memory_limit_mb': 3200,
                                      'estimated_ram_mb': 2100,
                                      'language': 'pt',
                                      'language_fixed': True,
                                      'language_label': 'Português — fixo'}})
for _id, _entry in CATALOG.items():
    _entry.setdefault("architecture", "CTC" if _id == "fastconformer" else "Whisper encoder-decoder")
    _entry.setdefault("compute", "int8")
    _entry.setdefault("source_url", "https://huggingface.co/" + _entry["repo"])
    _entry.setdefault("memory_limit_mb", 4096 if _id.startswith("parakeet_") else 3072)

for _id, _metadata in {'fastconformer': {'download_bytes': 131280499, 'installed_bytes': 131280499, 'download_revision': 'f5df0c1e1db4a779d106f4722886557f3466cc42'}, 'whisper_tiny': {'download_bytes': 78203619, 'installed_bytes': 78203619, 'download_revision': 'd90ca5fe260221311c53c58e660288d3deb8d356'}, 'whisper_base': {'download_bytes': 147882941, 'installed_bytes': 147882941, 'download_revision': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'}, 'whisper_small': {'download_bytes': 486212372, 'installed_bytes': 486212372, 'download_revision': '536b0662742c02347bc0e980a01041f333bce120'}}.items():
    CATALOG[_id].update(_metadata)

WHISPER_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


def validate(model, variant='adapted'):
    if type(model) is not str or model not in CATALOG or variant not in ('adapted', 'original'):
        raise ValueError('Modelo STT ou variante inválidos')


def location(root, model, variant='adapted'):
    validate(model, variant)
    return Path(root) / (('int8' if variant == 'original' else ADAPTED_VERSION) if model == 'fastconformer' else model)


def files(model, variant='adapted'):
    validate(model, variant)
    if 'files' in CATALOG[model]:
        return tuple(CATALOG[model]['files'])
    return (('model.int8.onnx' if variant == 'original' else 'model.onnx'), 'tokens.txt') if model == 'fastconformer' else WHISPER_FILES


def verify(root, model, variant='adapted'):
    """No network access. Legacy FastConformer manifests remain readable."""
    directory = location(root, model, variant)
    previous = directory.with_name(directory.name + '.previous')
    if not directory.exists() and previous.exists():
        previous.rename(directory)
    required = files(model, variant)
    if any(not (directory / name).is_file() or not (directory / name).stat().st_size for name in required):
        raise FileNotFoundError('Modelo não instalado: ' + model)
    path = directory / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not manifest and model == 'fastconformer' and variant == 'original':
        if (directory / required[0]).stat().st_size < 10_000_000 or (directory/'tokens.txt').stat().st_size <= 100:
            raise ValueError('Modelo legado incompleto')
        manifest = {'revision': 'legacy-local-unknown', 'sha256': {f: sha256(directory/f) for f in required}, 'repo': CATALOG[model]['repo']}
    hashes = manifest.get('sha256') or ({name: ADAPTED_HASHES[name] for name in required} if model == 'fastconformer' and variant == 'adapted' else {
        required[0]: manifest.get('model_sha256'), 'tokens.txt': manifest.get('tokens_sha256')})
    if model != 'fastconformer' and not re.fullmatch(r'[0-9a-f]{40}', manifest.get('revision', '')):
        raise ValueError('Manifesto Whisper sem revisão imutável')
    pinned = CATALOG[model].get('revision')
    if pinned and manifest.get('revision') != pinned:
        raise ValueError('Revisão instalada difere do catálogo')
    expected_sizes = CATALOG[model].get('file_sizes') or {}
    for name in required:
        if name in expected_sizes and (directory / name).stat().st_size != expected_sizes[name]:
            raise ValueError('Tamanho do arquivo inválido: ' + name)
        if CATALOG[model].get('sha256', {}).get(name) not in (None, hashes.get(name)):
            raise ValueError('Hash difere da origem pinada: ' + name)
        if not hashes.get(name) or sha256(directory / name) != hashes[name]:
            raise ValueError('Integridade do modelo inválida: ' + name)
    if model == 'fastconformer' and not path.exists():
        manifest = {**manifest, 'id': model, 'version': ADAPTED_VERSION if variant == 'adapted' else None,
                    'sha256': {f: sha256(directory/f) for f in required}, 'upstream_license': 'CC-BY-NC-4.0'}
        staged = path.with_suffix('.tmp')
        staged.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        staged.replace(path)
    return manifest


DEFAULT_STT_PYTHON = '/opt/venvs/stt/bin/python'
DEFAULT_PHONON_PYTHON = '/opt/venvs/phonon/bin/python'
DEFAULT_NEMOTRON_PYTHON = '/opt/venvs/nemotron/bin/python'
DEFAULT_TRANSCRIBE_LIB = '/usr/local/lib/libtranscribe.so'
DEFAULT_WHISPER_SERVER_PATHS = (
    '/usr/local/bin/whisper-server',
    '/usr/bin/whisper-server',
)

RUNTIME_STATES = (
    'runtime_missing',
    'dependency_missing',
    'dependency_conflict',
    'runtime_ready',
    'timeout',
)

_RUNTIME_PROBE_LOCK = threading.Lock()
_RUNTIME_STATUS_CACHE: dict[tuple, tuple[float, dict]] = {}

_PROBE_BODY = "\n".join([
    "try:",
    "    for mod_name, attrs in checks:",
    "        mod = importlib.import_module(mod_name)",
    "        for attr in attrs:",
    "            obj = mod",
    "            for part in attr.split('.'):",
    "                obj = getattr(obj, part)",
    "    print(json.dumps({'ok': True}))",
    "except ModuleNotFoundError as exc:",
    "    print(json.dumps({'ok': False, 'kind': 'dependency_missing', 'detail': str(exc)}))",
    "    sys.exit(10)",
    "except (ImportError, AttributeError, AssertionError, ValueError, RuntimeError, OSError) as exc:",
    "    print(json.dumps({'ok': False, 'kind': 'dependency_conflict', 'detail': f'{type(exc).__name__}: {exc}'}))",
    "    sys.exit(11)",
])


def _runtime_probe_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get('ARANDU_RUNTIME_PROBE_TTL_S', '15.0')))
    except ValueError:
        return 15.0


def _runtime_probe_timeout(runtime_name: str) -> float:
    raw = os.environ.get('ARANDU_RUNTIME_PROBE_TIMEOUT_S')
    if raw:
        try:
            return max(0.2, float(raw))
        except ValueError:
            pass
    return 25.0 if runtime_name in ('nvidia-nemo-isolated', 'fermion-research') else 10.0


def invalidate_runtime_cache(model: str | None = None) -> None:
    """Clear cached runtime probe entries after runtime install, update or removal."""
    with _RUNTIME_PROBE_LOCK:
        if model is None:
            _RUNTIME_STATUS_CACHE.clear()
        else:
            stale = [k for k in _RUNTIME_STATUS_CACHE if k and k[0] == model]
            for k in stale:
                _RUNTIME_STATUS_CACHE.pop(k, None)


def _stat_mtime_ns(path_str: str | None) -> int | None:
    if not path_str:
        return None
    try:
        return Path(path_str).stat().st_mtime_ns
    except OSError:
        return None


def _resolve_stt_python() -> tuple[str | None, str | None]:
    if 'ARANDU_STT_PYTHON' in os.environ:
        val = os.environ['ARANDU_STT_PYTHON'].strip()
        if not val or not Path(val).is_file():
            return None, 'MODEL_RUNTIME_UNAVAILABLE: /opt/venvs/stt ausente'
        return str(Path(val)), None
    if Path(DEFAULT_STT_PYTHON).is_file():
        return DEFAULT_STT_PYTHON, None
    return sys.executable, None


def _resolve_phonon_python() -> tuple[str | None, str | None]:
    if 'ARANDU_PHONON_PYTHON' in os.environ:
        val = os.environ['ARANDU_PHONON_PYTHON'].strip()
        if not val or not Path(val).is_file():
            return None, 'MODEL_RUNTIME_UNAVAILABLE: /opt/venvs/phonon ausente'
        return str(Path(val)), None
    if Path(DEFAULT_PHONON_PYTHON).is_file():
        return DEFAULT_PHONON_PYTHON, None
    return None, 'MODEL_RUNTIME_UNAVAILABLE: /opt/venvs/phonon ausente'


def _resolve_nemotron_python() -> tuple[str | None, str | None]:
    if 'ARANDU_NEMOTRON_PYTHON' in os.environ:
        val = os.environ['ARANDU_NEMOTRON_PYTHON'].strip()
        if not val or not Path(val).is_file():
            return None, 'MODEL_RUNTIME_UNAVAILABLE: /opt/venvs/nemotron ausente'
        return str(Path(val)), None
    if Path(DEFAULT_NEMOTRON_PYTHON).is_file():
        return DEFAULT_NEMOTRON_PYTHON, None
    return None, 'MODEL_RUNTIME_UNAVAILABLE: /opt/venvs/nemotron ausente'


def _resolve_whisper_server_binary() -> str | None:
    if 'ARANDU_WHISPER_SERVER_BIN' in os.environ:
        val = os.environ['ARANDU_WHISPER_SERVER_BIN'].strip()
        if not val:
            return None
        cand = Path(val)
        if cand.is_file() and (os.name == 'nt' or os.access(cand, os.X_OK)):
            return str(cand)
        return None
    for candidate in DEFAULT_WHISPER_SERVER_PATHS:
        cand = Path(candidate)
        if cand.is_file() and (os.name == 'nt' or os.access(cand, os.X_OK)):
            return str(cand)
    return shutil.which('whisper-server')


def _resolve_transcribe_library() -> str | None:
    if 'TRANSCRIBE_LIBRARY' in os.environ:
        val = os.environ['TRANSCRIBE_LIBRARY'].strip()
        return val if val and Path(val).is_file() else None
    return DEFAULT_TRANSCRIBE_LIB if Path(DEFAULT_TRANSCRIBE_LIB).is_file() else None


def _run_python_probe(python_bin: str, checks: list[tuple[str, tuple[str, ...]]], *, timeout: float) -> dict:
    """Execute lightweight single-line import verification inside the target interpreter subprocess."""
    script_src = f"import importlib, json, sys\nchecks = {json.dumps(checks)}\n{_PROBE_BODY}\n"
    code = f"exec(bytes.fromhex('{script_src.encode('utf-8').hex()}').decode('utf-8'))"
    try:
        proc = subprocess.run(
            [python_bin, '-c', code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            'available': False,
            'runtime_status': 'runtime_missing',
            'error_code': 'MODEL_RUNTIME_UNAVAILABLE',
            'note': f'MODEL_RUNTIME_UNAVAILABLE: interpretador ausente ({python_bin})',
            'detail': python_bin,
        }
    except subprocess.TimeoutExpired:
        return {
            'available': False,
            'runtime_status': 'timeout',
            'error_code': 'STT_TIMEOUT',
            'note': f'STT_TIMEOUT: verificação do runtime excedeu {timeout:.1f}s ({python_bin})',
            'detail': f'timeout>{timeout:.1f}s',
        }
    except OSError as exc:
        return {
            'available': False,
            'runtime_status': 'runtime_missing',
            'error_code': 'MODEL_RUNTIME_UNAVAILABLE',
            'note': f'MODEL_RUNTIME_UNAVAILABLE: falha ao executar {python_bin}: {exc}',
            'detail': str(exc),
        }

    stdout = (proc.stdout or '').strip()
    stderr = (proc.stderr or '').strip()
    parsed = None
    if stdout:
        for line in reversed(stdout.splitlines()):
            line_s = line.strip()
            if line_s.startswith('{') and line_s.endswith('}'):
                try:
                    parsed = json.loads(line_s)
                    break
                except ValueError:
                    pass

    if proc.returncode == 0 and isinstance(parsed, dict) and parsed.get('ok'):
        return {
            'available': True,
            'runtime_status': 'runtime_ready',
            'error_code': None,
            'note': None,
            'detail': None,
        }

    detail = (parsed.get('detail') if isinstance(parsed, dict) else None) or stderr or stdout or f'exit={proc.returncode}'
    kind = parsed.get('kind') if isinstance(parsed, dict) else None
    if kind not in ('dependency_missing', 'dependency_conflict'):
        low = detail.lower()
        if 'modulenotfounderror' in low or 'no module named' in low:
            kind = 'dependency_missing'
        elif proc.returncode == 10:
            kind = 'dependency_missing'
        else:
            kind = 'dependency_conflict'

    label = 'dependência ausente' if kind == 'dependency_missing' else 'conflito de dependências'
    return {
        'available': False,
        'runtime_status': kind,
        'error_code': 'MODEL_RUNTIME_UNAVAILABLE',
        'note': f'MODEL_RUNTIME_UNAVAILABLE: {label}: {detail}',
        'detail': detail,
    }


def _runtime_checks_for_model(model: str) -> tuple[str, str | None, str | None, list[tuple[str, tuple[str, ...]]], str | None]:
    runtime = CATALOG.get(model, {}).get('runtime', 'sherpa-onnx')
    if runtime == 'nvidia-nemo-isolated':
        py, err = _resolve_nemotron_python()
        checks = [('numpy', ()), ('torch', ()), ('soundfile', ()), ('nemo.collections.asr', ('models.ASRModel',))]
        return runtime, py, None, checks, err
    if runtime == 'fermion-research':
        py, err = _resolve_phonon_python()
        checks = [('numpy', ()), ('torch', ()), ('fermion', ('load_speech',))]
        return runtime, py, None, checks, err
    if runtime == 'whisper.cpp':
        wbin = _resolve_whisper_server_binary()
        if not wbin:
            return runtime, None, None, [], 'MODEL_RUNTIME_UNAVAILABLE: binário whisper-server ausente'
        py, err = _resolve_stt_python()
        return runtime, py, wbin, [('numpy', ())], err
    if runtime == 'transcribe.cpp':
        tlib = _resolve_transcribe_library()
        if not tlib:
            return runtime, None, None, [], 'MODEL_RUNTIME_UNAVAILABLE: biblioteca nativa libtranscribe.so ausente'
        py, err = _resolve_stt_python()
        return runtime, py, tlib, [('numpy', ()), ('transcribe_cpp', ())], err
    py, err = _resolve_stt_python()
    if runtime == 'faster-whisper':
        checks = [('numpy', ()), ('faster_whisper', ()), ('ctranslate2', ())]
    elif runtime == 'onnx-asr':
        checks = [('numpy', ()), ('onnx_asr', ()), ('onnxruntime', ())]
    else:
        checks = [('numpy', ()), ('sherpa_onnx', ())]
    return runtime, py, None, checks, err


def probe_runtime_status(model: str, *, force_refresh: bool = False, timeout: float | None = None) -> dict:
    """Probe the isolated runtime environment for `model` via subprocess with short TTL cache."""
    runtime, py_bin, native_artifact, checks, pre_err = _runtime_checks_for_model(model)
    cache_key = (
        model,
        runtime,
        py_bin,
        _stat_mtime_ns(py_bin),
        native_artifact,
        _stat_mtime_ns(native_artifact),
    )
    ttl = _runtime_probe_ttl()
    now = time.monotonic()
    if not force_refresh and ttl > 0.0:
        with _RUNTIME_PROBE_LOCK:
            cached = _RUNTIME_STATUS_CACHE.get(cache_key)
            if cached and (now - cached[0]) < ttl:
                return dict(cached[1])

    if pre_err or not py_bin:
        result = {
            'model': model,
            'runtime': runtime,
            'interpreter': py_bin,
            'native_artifact': native_artifact,
            'available': False,
            'runtime_status': 'runtime_missing',
            'error_code': 'MODEL_RUNTIME_UNAVAILABLE',
            'note': pre_err or f'MODEL_RUNTIME_UNAVAILABLE: runtime {runtime} ausente',
            'detail': pre_err,
        }
    else:
        eff_timeout = float(timeout) if timeout is not None else _runtime_probe_timeout(runtime)
        probed = _run_python_probe(py_bin, checks, timeout=eff_timeout)
        result = {
            'model': model,
            'runtime': runtime,
            'interpreter': py_bin,
            'native_artifact': native_artifact,
            **probed,
        }

    with _RUNTIME_PROBE_LOCK:
        _RUNTIME_STATUS_CACHE[cache_key] = (time.monotonic(), dict(result))
    return result


def _runtime_status(model: str) -> tuple[bool, str | None]:
    info = probe_runtime_status(model)
    return bool(info['available']), info['note']


def _emit_progress(progress, state: str, **payload):
    if not progress:
        return
    try:
        progress(state, **payload)
    except TypeError:
        progress(state)


def describe(root, model, variant='adapted'):
    directory = location(root, model, variant)
    probe = probe_runtime_status(model)
    runtime_ok = bool(probe['available'])
    runtime_note = probe['note']
    result = {
        'id': model,
        **CATALOG[model],
        'variant': variant if model == 'fastconformer' else None,
        'required_files': files(model, variant),
        'ready': False,
        'path': str(directory),
        'state': 'not_installed',
        'installed': False,
        'incomplete': False,
        'language': CATALOG[model].get('language', 'pt'),
        'language_fixed': bool(CATALOG[model].get('language_fixed', True)),
        'language_label': CATALOG[model].get('language_label', 'Português — fixo'),
        'runtime_available': runtime_ok,
        'runtime_note': runtime_note,
        'runtime_status': probe['runtime_status'],
        'runtime_interpreter': probe.get('interpreter'),
        'runtime_detail': probe.get('detail'),
        'weights_status': 'model_not_installed',
        'load_status': 'unloaded',
        'error_code': probe.get('error_code') if not runtime_ok else None,
    }
    if model == 'fastconformer' and variant == 'adapted':
        result.update(download_bytes=456406182, installed_bytes=456406182, source_url=ADAPTED_URL, download_revision=ADAPTED_VERSION)
    expected_sizes = CATALOG[model].get('file_sizes') or {}
    expected_hashes = CATALOG[model].get('sha256') or {}
    install_marker = Path(root) / ('.install-' + model + '.json')
    try:
        # Inventory is cheap; full hashes are checked at installation/load, never every UI poll.
        required = files(model, variant)
        if all((directory / f).is_file() and (directory / f).stat().st_size for f in required):
            size_mismatch = next((f for f in required if f in expected_sizes and (directory / f).stat().st_size != expected_sizes[f]), None)
            if size_mismatch:
                result.update(
                    state='error',
                    incomplete=True,
                    weights_status='model_corrupted',
                    error_code='MODEL_INTEGRITY_FAILED',
                    error=f'Arquivo de modelo truncado ou corrompido ({size_mismatch}); reinstale o modelo',
                )
            else:
                manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
                manifest_hashes = manifest.get('sha256') or {}
                hash_mismatch = next(
                    (f for f in required if f in expected_hashes and manifest_hashes.get(f) not in (None, expected_hashes[f])),
                    None,
                )
                if hash_mismatch:
                    result.update(
                        state='error',
                        incomplete=True,
                        weights_status='model_corrupted',
                        error_code='MODEL_INTEGRITY_FAILED',
                        error=f'Checksum SHA-256 inválido no manifesto ({hash_mismatch}); reinstale o modelo',
                    )
                else:
                    result.update(
                        state='installed',
                        installed=True,
                        weights_status='model_ready',
                        revision=manifest.get('revision') or manifest.get('version'),
                        sha256=manifest.get('sha256'),
                        load_validated=manifest.get('load_validated', False),
                        size_bytes=sum((directory / f).stat().st_size for f in required),
                    )
        elif (directory.exists() and any(directory.iterdir())) or install_marker.is_file():
            result.update(
                state='error',
                incomplete=True,
                weights_status='model_corrupted',
                error_code='MODEL_INTEGRITY_FAILED',
                error='Download ou instalação incompleta; reinstale o modelo',
            )
    except (OSError, ValueError):
        result.update(
            state='error',
            incomplete=True,
            weights_status='model_corrupted',
            error_code='MODEL_INTEGRITY_FAILED',
            error='Manifesto ausente ou inválido; reinstale o modelo',
        )
    return result


def install(root, model, variant='adapted', *, progress=None, validator=None):
    from filelock import FileLock
    validate(model, variant)
    invalidate_runtime_cache(model)
    Path(root).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(root)/'.install.lock'), timeout=0):
        for abandoned in Path(root).glob('.staging-*'):
            if not abandoned.is_symlink() and abandoned.is_dir() and abandoned.resolve().parent == Path(root).resolve():
                shutil.rmtree(abandoned)
        try:
            return _install_locked(root, model, variant, progress=progress, validator=validator)
        finally:
            invalidate_runtime_cache(model)


def _install_locked(root, model, variant='adapted', *, progress=None, validator=None):
    validate(model, variant)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        return verify(root, model, variant)
    except (OSError, ValueError):
        pass
    entry = CATALOG[model]
    if model == 'fastconformer' and variant == 'adapted':
        entry = {**entry, 'download_bytes': 456406182, 'installed_bytes': 456406182}
    needed = entry.get('download_bytes', 500_000_000) + entry.get('installed_bytes', 500_000_000) + 256_000_000
    if shutil.disk_usage(root).free < needed:
        raise ValueError('Espaço insuficiente para download e instalação temporária')
    destination = location(root, model, variant)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as staging:
        stage_root = Path(staging)
        stage = location(stage_root, model, variant)
        stage.mkdir()
        if model == 'fastconformer' and variant == 'adapted':
            manifest = ensure_model(stage, variant=variant)
        else:
            from huggingface_hub import HfApi, snapshot_download
            repo = CATALOG[model]['repo']
            info = HfApi(token=False).model_info(repo_id=repo, files_metadata=True, **({'revision': entry.get('revision') or entry['download_revision']} if 'revision' in entry or 'download_revision' in entry else {}))
            revision = info.sha
            if not revision or not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError('Revisão Hugging Face inválida')
            total_bytes = int(entry.get('download_bytes') or 0)
            stop_watch = threading.Event()
            def _watch_download():
                last_bytes = -1
                while not stop_watch.wait(0.25):
                    current = 0
                    for candidate in stage_root.rglob('*'):
                        if candidate.is_file():
                            try:
                                current += candidate.stat().st_size
                            except OSError:
                                pass
                    if current != last_bytes:
                        last_bytes = current
                        pct = round(min(99.0, current * 100.0 / total_bytes), 1) if total_bytes > 0 else None
                        _emit_progress(progress, 'downloading', bytes_downloaded=current, total_bytes=total_bytes, percent=pct)
            watcher = threading.Thread(target=_watch_download, daemon=True) if progress else None
            if watcher:
                _emit_progress(progress, 'downloading', bytes_downloaded=0, total_bytes=total_bytes, percent=0.0 if total_bytes > 0 else None)
                watcher.start()
            try:
                if entry.get('archive'):
                    from huggingface_hub import hf_hub_download
                    archive = Path(hf_hub_download(repo_id=repo, filename=entry['archive'], revision=revision, token=False, local_dir=str(stage_root/'download')))
                    stop_watch.set()
                    if watcher:
                        watcher.join(timeout=1.0)
                    if sha256(archive) != entry['archive_sha256']:
                        raise ValueError('SHA256 do pacote inválido')
                    _emit_progress(progress, 'verifying', bytes_downloaded=total_bytes, total_bytes=total_bytes, percent=100.0)
                    extract_archive(archive, stage, files(model, variant), entry.get('installed_bytes', 800_000_000) + 1_000_000)
                else:
                    snapshot_download(repo_id=repo, revision=revision, token=False, allow_patterns=list(files(model, variant)), local_dir=str(stage))
            finally:
                stop_watch.set()
                if watcher:
                    watcher.join(timeout=1.0)
            _emit_progress(progress, 'verifying', bytes_downloaded=total_bytes, total_bytes=total_bytes, percent=100.0)
            for item in getattr(info, 'siblings', []) or []:
                if item.rfilename in files(model, variant) and getattr(item, 'lfs', None):
                    expected = getattr(item.lfs, 'sha256', None)
                    if expected and sha256(stage/item.rfilename) != expected:
                        raise ValueError('SHA256 Hugging Face inválido: ' + item.rfilename)
            manifest = {'repo': repo, 'revision': revision, 'license': CATALOG[model]['license']}
        _emit_progress(progress, 'verifying')
        manifest.update(id=model, sha256={f: sha256(stage / f) for f in files(model, variant)})
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify(stage_root, model, variant)
        if validator:
            validator(stage)
            manifest['load_validated'] = True
            (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        # Never disturb an existing installation until the complete replacement has passed verification.
        backup = destination.with_name(destination.name + '.previous')
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    (root / ('.install-' + model + '.json')).unlink(missing_ok=True)
    return manifest


def remove(root, model, variant='adapted'):
    """Remove installed weights for one model without affecting other models."""
    validate(model, variant)
    invalidate_runtime_cache(model)
    directory = location(root, model, variant)
    if directory.exists():
        shutil.rmtree(directory)
    backup = directory.with_name(directory.name + '.previous')
    if backup.exists():
        shutil.rmtree(backup)
    (Path(root) / ('.install-' + model + '.json')).unlink(missing_ok=True)
    invalidate_runtime_cache(model)
    return {'removed': True, 'model': model}


def extract_archive(archive, destination, required, max_bytes):
    """Copy only expected regular files; reject links, traversal and decompression bombs."""
    import tarfile
    from pathlib import PurePosixPath
    seen, total = set(), 0
    with tarfile.open(archive, 'r:bz2') as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name or member.issym() or member.islnk():
                raise ValueError('Caminho inseguro no pacote')
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Entrada não regular no pacote')
            total += member.size
            if total > max_bytes:
                raise ValueError('Pacote excedeu tamanho permitido')
            if path.name not in required:
                continue
            if path.name in seen:
                raise ValueError('Arquivo duplicado no pacote')
            seen.add(path.name)
            with bundle.extractfile(member) as source, (Path(destination)/path.name).open('wb') as target:
                shutil.copyfileobj(source, target)
    if seen != set(required):
        raise ValueError('Pacote incompleto')

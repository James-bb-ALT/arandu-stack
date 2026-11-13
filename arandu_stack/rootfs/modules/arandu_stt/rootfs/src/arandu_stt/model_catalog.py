"""Allowlisted local STT models and verified, atomic installation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile

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
                           'languages': ['pt', 'auto'],
                           'revision': '023ce907267a09a308a5cb69239447cb3d0c1597',
                           'files': ['nemotron-labs-unified-asr-0.6b.nemo'],
                           'sha256': {'nemotron-labs-unified-asr-0.6b.nemo': '1718cc1b44fb8597b2356328d706a8d89f31c389ca6f2256c2e3f743c0246936'},
                           'memory_limit_mb': 4608,
                           'estimated_ram_mb': 3600,
                           'experimental': True,
                           'download_bytes': 2507735040,
                           'installed_bytes': 2507735040}})
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
    for name in required:
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


def describe(root, model, variant='adapted'):
    directory = location(root, model, variant)
    result = {'id': model, **CATALOG[model], 'variant': variant if model == 'fastconformer' else None,
              'required_files': files(model, variant), 'ready': False, 'path': str(directory), 'state': 'not_installed', 'installed': False}
    if model == 'fastconformer' and variant == 'adapted':
        result.update(download_bytes=456406182, installed_bytes=456406182, source_url=ADAPTED_URL, download_revision=ADAPTED_VERSION)
    try:
        # Inventory is cheap; full hashes are checked at installation/load, never every UI poll.
        required = files(model, variant)
        if all((directory / f).is_file() and (directory / f).stat().st_size for f in required):
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            result.update(state='installed', installed=True, revision=manifest.get('revision') or manifest.get('version'),
                          sha256=manifest.get('sha256'), load_validated=manifest.get('load_validated', False), size_bytes=sum((directory / f).stat().st_size for f in required))
    except (OSError, ValueError):
        result.update(state='error', error='Manifesto ausente ou inválido; reinstale o modelo')
    return result


def install(root, model, variant='adapted', *, progress=None, validator=None):
    from filelock import FileLock
    validate(model, variant)
    Path(root).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(root)/'.install.lock'), timeout=0):
        for abandoned in Path(root).glob('.staging-*'):
            if not abandoned.is_symlink() and abandoned.is_dir() and abandoned.resolve().parent == Path(root).resolve():
                shutil.rmtree(abandoned)
        return _install_locked(root, model, variant, progress=progress, validator=validator)


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
            if entry.get('archive'):
                from huggingface_hub import hf_hub_download
                archive = Path(hf_hub_download(repo_id=repo, filename=entry['archive'], revision=revision, token=False, local_dir=str(stage_root/'download')))
                if sha256(archive) != entry['archive_sha256']:
                    raise ValueError('SHA256 do pacote inválido')
                if progress: progress('verifying')
                extract_archive(archive, stage, files(model, variant), entry.get('installed_bytes', 800_000_000) + 1_000_000)
            else:
                snapshot_download(repo_id=repo, revision=revision, token=False, allow_patterns=list(files(model, variant)), local_dir=str(stage))
            if progress: progress('verifying')
            for item in getattr(info, 'siblings', []) or []:
                if item.rfilename in files(model, variant) and getattr(item, 'lfs', None):
                    expected = getattr(item.lfs, 'sha256', None)
                    if expected and sha256(stage/item.rfilename) != expected:
                        raise ValueError('SHA256 Hugging Face inválido: ' + item.rfilename)
            manifest = {'repo': repo, 'revision': revision, 'license': CATALOG[model]['license']}
        if progress: progress('verifying')
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
    return manifest


def remove(root, model, variant='adapted'):
    """Remove installed weights for one model without affecting other models."""
    validate(model, variant)
    directory = location(root, model, variant)
    if directory.exists():
        shutil.rmtree(directory)
    backup = directory.with_name(directory.name + '.previous')
    if backup.exists():
        shutil.rmtree(backup)
    (Path(root) / ('.install-' + model + '.json')).unlink(missing_ok=True)
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

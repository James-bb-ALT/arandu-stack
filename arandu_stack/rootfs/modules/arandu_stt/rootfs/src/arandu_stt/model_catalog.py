"""Allowlisted local STT models and verified, atomic installation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile

from model_download import ADAPTED_VERSION, ADAPTED_HASHES, ensure_model, sha256

CATALOG = {
    'fastconformer': {'name': 'Arandu FastConformer', 'runtime': 'sherpa-onnx',
        'repo': 'csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8', 'license': 'CC-BY-NC-4.0'},
    **{f'whisper_{size}': {'name': f'Whisper {size.title()}', 'runtime': 'faster-whisper',
        'repo': f'Systran/faster-whisper-{size}', 'license': 'MIT'} for size in ('tiny', 'base', 'small')},
}
WHISPER_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


def validate(model, variant='adapted'):
    if type(model) is not str or model not in CATALOG or variant not in ('adapted', 'original'):
        raise ValueError('Modelo STT ou variante inválidos')


def location(root, model, variant='adapted'):
    validate(model, variant)
    return Path(root) / (('int8' if variant == 'original' else ADAPTED_VERSION) if model == 'fastconformer' else model)


def files(model, variant='adapted'):
    validate(model, variant)
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
    for name in required:
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
              'path': str(directory), 'state': 'not_installed', 'installed': False}
    try:
        # Inventory is cheap; full hashes are checked at installation/load, never every UI poll.
        required = files(model, variant)
        if all((directory / f).is_file() and (directory / f).stat().st_size for f in required):
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            result.update(state='installed', installed=True, revision=manifest.get('revision') or manifest.get('version'),
                          sha256=manifest.get('sha256'), size_bytes=sum((directory / f).stat().st_size for f in required))
    except (OSError, ValueError):
        result.update(state='error', error='Manifesto ausente ou inválido; reinstale o modelo')
    return result


def install(root, model, variant='adapted'):
    from filelock import FileLock
    validate(model, variant)
    Path(root).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(root)/'.install.lock'), timeout=0):
        for abandoned in Path(root).glob('.staging-*'):
            if not abandoned.is_symlink() and abandoned.is_dir() and abandoned.resolve().parent == Path(root).resolve():
                shutil.rmtree(abandoned)
        return _install_locked(root, model, variant)


def _install_locked(root, model, variant='adapted'):
    validate(model, variant)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        return verify(root, model, variant)
    except (OSError, ValueError):
        pass
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
            info = HfApi(token=False).model_info(repo_id=repo, files_metadata=True)
            revision = info.sha
            if not revision or not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError('Revisão Hugging Face inválida')
            snapshot_download(repo_id=repo, revision=revision, token=False, allow_patterns=list(files(model, variant)), local_dir=str(stage))
            for item in getattr(info, 'siblings', []) or []:
                if item.rfilename in files(model, variant) and getattr(item, 'lfs', None):
                    expected = getattr(item.lfs, 'sha256', None)
                    if expected and sha256(stage/item.rfilename) != expected:
                        raise ValueError('SHA256 Hugging Face inválido: ' + item.rfilename)
            manifest = {'repo': repo, 'revision': revision, 'license': CATALOG[model]['license']}
        manifest.update(id=model, sha256={f: sha256(stage / f) for f in files(model, variant)})
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify(stage_root, model, variant)
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

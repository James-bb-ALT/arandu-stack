"""Seed shipped Piper voices into persistent storage; preserve user-installed files."""
from pathlib import Path
import hashlib
import json
import shutil

def prepare(directory, source=Path('/opt/assistant-assets/voices')):
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    directory.mkdir(parents=True, exist_ok=True)
    for entry in manifest['files']:
        name = entry['name']
        if Path(name).name != name or not name.endswith(('.onnx', '.onnx.json')):
            raise ValueError('Arquivo de voz inválido')
        target = directory / name
        if target.is_symlink():
            raise ValueError('Links simbólicos não são aceitos para vozes incluídas')
        if target.exists():
            continue
        original = source / name
        hasher = hashlib.sha256()
        with original.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        if hasher.hexdigest() != entry['sha256']:
            raise ValueError('Integridade da voz inválida: ' + name)
        temporary = target.with_suffix(target.suffix + '.tmp')
        shutil.copyfile(original, temporary)
        temporary.replace(target)

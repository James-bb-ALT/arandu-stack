"""Offline distribution policy; HACS itself remains the installation owner."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

DOMAIN = 'local_nlu'
ASSET = 'arandu-nlu-integration.zip'
INTEGRATION_FILES = frozenset('''__init__.py ai_lab.py ai_lab_core.py ai_lab_demo.py ai_lab_demo.json ai_lab_panel.js LICENSE THIRD_PARTY_NOTICES.md
catalog.py client.py config_flow.py const.py conversation.py capabilities.py
contextual_protocol.py contextual_catalog.py contextual_runtime.py contextual_errors.py
device_location.py diagnostics.py identity.py voice_input.py queries.py manifest.json protocol.py runtime.py
strings.json translations/en.json translations/pt-BR.json brand/icon.png'''.split())
HACS_FIELDS = {
    'name': str, 'homeassistant': str, 'hacs': str, 'content_in_root': bool,
    'hide_default_branch': bool, 'zip_release': bool, 'filename': str,
    'country': (str, list), 'persistent_directory': str, 'render_readme': bool,
}
SEMVER = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                    rb'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                    rb'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|'
                    rb'''(?i:\b(?:api_key|access_token|password|secret)["']?\s*[=:]\s*["'][^"'\r\n]+["'])''')


def check_secret(name, data):
    if SECRET.search(data):
        raise ValueError(f'credential/private key in integration file: {name}')


def check_manifest(data, version):
    manifest = json.loads(data)
    if manifest.get('domain') != DOMAIN or manifest.get('version') != version:
        raise ValueError('integration domain/version differs')
    if manifest.get('name') != 'ARANDU NLU' or not manifest.get('config_flow'):
        raise ValueError('integration identity/config flow differs')
    for key in ('documentation', 'issue_tracker'):
        if not isinstance(manifest.get(key), str) or not manifest[key].startswith('https://github.com/jaimevictor/Arandu-NLU'):
            raise ValueError(f'invalid integration {key}')
    if manifest.get('codeowners') != ['@jaimevictor'] or manifest.get('requirements') != []:
        raise ValueError('invalid integration codeowners/requirements')
    return manifest


def integration_files(root):
    directory = root / 'custom_components' / DOMAIN
    # Check parents too: rglob alone can overlook a symlinked root/directory.
    if any(path.is_symlink() for path in (root / 'custom_components', directory)):
        raise ValueError('symlink in integration path')
    files = {}
    for path in sorted(directory.rglob('*')):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ValueError(f'symlink in integration: {name}')
        if '__pycache__' in path.parts and path.suffix == '.pyc':
            continue
        if path.is_file():
            if name not in INTEGRATION_FILES:
                raise ValueError(f'unexpected integration file: {name}')
            data = path.read_bytes()
            check_secret(name, data)
            files[name] = path
    if set(files) != INTEGRATION_FILES:
        raise ValueError('missing integration files')
    return files


def check(root, version, tag=None):
    if not re.fullmatch(SEMVER, version):
        raise ValueError('invalid semantic version')
    if tag is not None and tag != f'v{version}':
        raise ValueError('release tag/version differs')
    metadata = json.loads((root / 'hacs.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict) or set(metadata) - HACS_FIELDS.keys():
        raise ValueError('unsupported HACS fields')
    if any(type(value) not in (kind if isinstance(kind, tuple) else (kind,))
           for key, value in metadata.items() for kind in (HACS_FIELDS[key],)):
        raise ValueError('invalid HACS field type')
    if metadata.get('name') != 'ARANDU NLU' or metadata.get('content_in_root', False):
        raise ValueError('invalid HACS integration identity/layout')
    if metadata.get('hide_default_branch', False) or metadata.get('persistent_directory'):
        raise ValueError('HACS must preserve branch access and use HA-owned storage')
    for key in ('homeassistant', 'hacs'):
        if not re.fullmatch(SEMVER, metadata.get(key, '')):
            raise ValueError(f'invalid HACS minimum {key}')
    if metadata.get('zip_release') and metadata.get('filename') != ASSET:
        raise ValueError('invalid HACS release ZIP filename')
    components = root / 'custom_components'
    if {p.name for p in components.iterdir() if p.is_dir() and p.name != '__pycache__'} != {DOMAIN}:
        raise ValueError('HACS requires exactly one integration directory')
    files = integration_files(root)
    check_manifest(files['manifest.json'].read_bytes(), version)
    return files


def check_zip(path, version):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or set(names) != INTEGRATION_FILES:
            raise ValueError('unexpected/duplicate/missing HACS ZIP files')
        if sum(entry.file_size for entry in entries) > 2_000_000:
            raise ValueError('oversized HACS ZIP')
        for entry in entries:
            name = entry.filename
            parts = PurePosixPath(name).parts
            if (name.startswith('/') or '\\' in name or ':' in name or '..' in parts
                    or entry.is_dir() or entry.flag_bits & 1
                    or not stat.S_ISREG(entry.external_attr >> 16)):
                raise ValueError('unsafe HACS ZIP entry')
            check_secret(name, archive.read(entry))
        if archive.testzip():
            raise ValueError('invalid HACS ZIP CRC')
        check_manifest(archive.read('manifest.json'), version)
    return len(entries)


if __name__ == '__main__':
    from distribution import ROOT, check as distribution_check
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag')
    parser.add_argument('--zip', type=Path)
    args = parser.parse_args()
    version = distribution_check(ROOT)
    check(ROOT, version, args.tag)
    if args.zip:
        check_zip(args.zip, version)
    print(f'HACS structure/version/package PASS: {version}')

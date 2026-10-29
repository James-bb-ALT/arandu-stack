"""Distribution checks and deterministic file lists; no third-party Python packages."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {'.git', 'target', '__pycache__', '.storage', '.tools', '.cargo-home'}


def yaml_file(path):
    return json.loads(subprocess.check_output(
        ['ruby', '-rjson', '-ryaml', '-e',
         'puts JSON.generate(YAML.safe_load(File.read(ARGV[0]), aliases: false))', str(path)],
        text=True))


def product_files(root):
    files = []
    for folder in ('addon', 'custom_components/local_nlu'):
        for path in sorted((root / folder).rglob('*')):
            relative = path.relative_to(root)
            if set(relative.parts) & EXCLUDED or path.suffix in {'.pyc', '.log'}:
                continue
            if path.is_symlink():
                raise ValueError(f'symlink in distribution: {relative}')
            if path.is_file():
                files.append(path)
    return files


def content(path):
    data = path.read_bytes()
    # Checkout-independent text bytes; preserve binary assets byte for byte.
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    return data.replace(b'\r\n', b'\n')


def source_digest(root):
    files = product_files(root) + [root / name for name in (
        'repository.yaml', 'hacs.json', 'tools/distribution.py', 'tools/make-store-repo.py',
        'tools/hacs.py', 'tools/release-package.py', 'tools/mlp-dev.ps1',
        '.github/workflows/hacs.yml', '.github/workflows/release-integration.yml')]
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(content(p)).hexdigest()
              for p in files}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def check_store(root):
    repository = yaml_file(root / 'repository.yaml')
    if not isinstance(repository, dict) or repository.get('name') != 'ARANDU NLU':
        raise ValueError('invalid repository.yaml')
    if repository.get('url') != 'https://github.com/jaimevictor/Arandu-NLU':
        raise ValueError('repository identity changed')
    configs = []
    if (root / '.git').exists():
        # Supervisor sees the Git checkout, not ignored local experiments/artifacts.
        names = subprocess.check_output(['git', '-c', 'safe.directory=*', '-C', str(root),
                                         'ls-files', '--cached', '--others', '--exclude-standard', '-z'], text=True)
        paths = [root / name for name in names.split('\0') if name]
    else:
        paths = []
        for folder, directories, names in os.walk(root):
            directories[:] = sorted(d for d in directories if d not in EXCLUDED
                                    and not d.startswith('.') and d != 'rootfs')
            paths.extend(Path(folder) / name for name in names)
    for path in sorted(set(paths)):
        parts = path.relative_to(root).parts
        if path.name not in {'config.yaml', 'config.yml', 'config.json'} or not path.is_file():
            continue
        if any(p.startswith('.') or p == 'rootfs' for p in parts):
            continue
        config = yaml_file(path)
        if not isinstance(config, dict):
            raise ValueError(f'invalid store manifest: {path}')
        configs.append((path, config))
    slugs = [c.get('slug') for _, c in configs]
    if len(slugs) != len(set(slugs)):
        raise ValueError('duplicate add-on slugs')
    if len(configs) != 1 or configs[0][0] != root / 'addon/config.yaml':
        raise ValueError('store must publish exactly one add-on from addon/')
    config = configs[0][1]
    if config.get('slug') != 'ptbr_nlu' or config.get('name') != 'ARANDU NLU':
        raise ValueError('public add-on identity changed')
    for key in ('version', 'description', 'startup', 'boot'):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f'invalid add-on {key}')
    if config.get('arch') != ['aarch64', 'amd64'] or 'image' in config:
        raise ValueError('expected native aarch64/amd64 source builds')
    platforms = json.loads((root / 'addon/container-inputs.json').read_text())['builder']['platform_manifests']
    if set(config['arch']) != set(platforms):
        raise ValueError('builder architecture metadata differs')
    recipe = (root / 'addon/Dockerfile').read_text()
    for required in ('COPY engine engine', 'COPY vendor vendor', 'COPY .cargo .cargo',
                     '--manifest-path engine/Cargo.toml', '--locked', '--offline',
                     'io.hass.version="${BUILD_VERSION}"', 'io.hass.arch="${BUILD_ARCH}"'):
        if required not in recipe:
            raise ValueError(f'Dockerfile missing {required}')
    for line in recipe.splitlines():
        if line.startswith('COPY ') and '--from=' not in line:
            for source in line.split()[1:-1]:
                if not (root / 'addon' / source).exists():
                    raise ValueError(f'missing Docker COPY source: {source}')
    for name in ('contract', 'grammar', 'mod', 'resolver', 'slots'):
        if not (root / f'addon/engine/src/contextual/{name}.rs').is_file():
            raise ValueError(f'contextual module absent: {name}')
    if 'pub mod contextual;' not in (root / 'addon/engine/src/lib.rs').read_text():
        raise ValueError('contextual module not compiled')
    return config['version']


def check_versions(root, version):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version):
        raise ValueError('product version must be stable semantic version')
    crate = tomllib.loads((root / 'addon/engine/Cargo.toml').read_text())['package']['version']
    manifest = json.loads((root / 'custom_components/local_nlu/manifest.json').read_text())['version']
    versions = [crate, manifest]
    for lock in ('Cargo.lock', 'addon/engine/Cargo.lock'):
        packages = tomllib.loads((root / lock).read_text())['package']
        local = [p['version'] for p in packages if p['name'] == 'local-nlu']
        if len(local) != 1:
            raise ValueError(f'invalid local-nlu entry: {lock}')
        versions += local
    if any(v != version for v in versions):
        raise ValueError(f'product versions differ: addon={version}, components={versions}')
    for document, pattern in (('README.md', r'\*\*Versão ([0-9.]+)'),
                              ('INSTALL.md', r'Versão atual: `([0-9.]+)`')):
        match = re.search(pattern, (root / document).read_text())
        if not match or match[1] != version:
            raise ValueError(f'current version stale in {document}')


def check(root=ROOT):
    from hacs import check as check_hacs
    version = check_store(root)
    check_versions(root, version)
    check_hacs(root, version)
    release = json.loads((root / 'release.json').read_text())
    if release != {'version': version, 'source_digest': source_digest(root)}:
        raise ValueError('distribution changed: bump version and record release.json')
    return version


def record(root=ROOT):
    version = check_store(root)
    check_versions(root, version)
    new = {'version': version, 'source_digest': source_digest(root)}
    path = root / 'release.json'
    if path.exists():
        old = json.loads(path.read_text())
        if old != new and tuple(map(int, version.split('.'))) <= tuple(map(int, old['version'].split('.'))):
            raise ValueError('distribution changed without a newer product version')
    path.write_text(json.dumps(new, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    print(f'Distribution/store/version PASS: {check()}')

#!/usr/bin/env python3
"""Build and verify reproducible, separate add-on and integration ZIPs."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from distribution import ROOT, check, content, product_files, yaml_file
from hacs import ASSET, check_zip, integration_files


def package(root, output):
    version = check(root)
    output.mkdir(parents=True, exist_ok=True)
    names = ['packages.json']
    for component in ('addon', 'integration'):
        name = f'arandu-nlu-{component}-{version}.zip'
        names += [name, name + '.sha256']
    names += [ASSET, ASSET + '.sha256']
    for name in names:
        if (output / name).exists():
            raise FileExistsError(f'output already exists; preserving {output / name}')
    results = []
    for component, prefix in [('addon', 'addon/'), ('integration', 'custom_components/local_nlu/'),
                              ('hacs', 'custom_components/local_nlu/')]:
        files = {p.relative_to(root).as_posix(): content(p) for p in product_files(root)
                 if p.relative_to(root).as_posix().startswith(prefix)}
        if component == 'addon':
            files['repository.yaml'] = content(root / 'repository.yaml')
        if component == 'hacs':
            files = {name: content(path) for name, path in integration_files(root).items()}
        destination = output / (ASSET if component == 'hacs' else f'arandu-nlu-{component}-{version}.zip')
        with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                executable = data.startswith((b'#!/', b'#! /'))
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
        with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(destination) as archive:
            if archive.testzip() or set(archive.namelist()) != set(files):
                raise ValueError('invalid ZIP contents')
            archive.extractall(folder)
            extracted = Path(folder)
            for name, data in files.items():
                if (extracted / name).read_bytes() != data:
                    raise ValueError(f'extracted ZIP differs: {name}')
            manifest = 'manifest.json' if component == 'hacs' else 'custom_components/local_nlu/manifest.json'
            actual_version = (yaml_file(extracted / 'addon/config.yaml')['version'] if component == 'addon'
                              else json.loads((extracted / manifest).read_text())['version'])
            if actual_version != version:
                raise ValueError('ZIP version differs')
        if component == 'hacs':
            check_zip(destination, version)
        sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix('.zip.sha256').write_text(f'{sha}  {destination.name}\n', encoding='ascii')
        results.append({'file': destination.name, 'sha256': sha, 'files': len(files), 'version': version})
    (output / 'packages.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(package(ROOT, args.output or ROOT / 'target/dist' / check(ROOT)), indent=2))

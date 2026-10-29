"""Optional probe in the pinned official HACS image; network/HA I/O are fixtures.

Exercises official validators, registration path, archive consumer and update
backup. This is neither a HACS frontend run nor a residential installation.
"""
import argparse
import asyncio
import base64
import io
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from custom_components.hacs.repositories.base import HacsManifest
from custom_components.hacs.repositories.integration import HacsIntegrationRepository
from custom_components.hacs.utils.validate import HACS_MANIFEST_JSON_SCHEMA, INTEGRATION_MANIFEST_JSON_SCHEMA
from custom_components.hacs.validate.brands import Validator as BrandsValidator
from custom_components.hacs.validate.hacsjson import Validator as HacsValidator
from custom_components.hacs.validate.integration_manifest import Validator as ManifestValidator


async def probe(root, package):
    metadata = json.loads((root / 'hacs.json').read_text())
    manifest = json.loads((root / 'custom_components/local_nlu/manifest.json').read_text())
    HACS_MANIFEST_JSON_SCHEMA(metadata)
    INTEGRATION_MANIFEST_JSON_SCHEMA(manifest)
    with tempfile.TemporaryDirectory() as temporary:
        config = Path(temporary) / 'ha'
        config.mkdir()
        storage = config / '.storage'
        storage.mkdir()
        entry = storage / 'core.config_entries'
        entry.write_text(json.dumps({'domain': 'local_nlu', 'entry_id': 'fixture-entry',
                                     'options': {'contextual_enabled': True}}))
        original = entry.read_bytes()
        downloaded = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            for source in sorted((root / 'custom_components/local_nlu').rglob('*')):
                if source.is_file() and '__pycache__' not in source.parts:
                    zipped.writestr('Arandu-NLU-master/' + source.relative_to(root).as_posix(), source.read_bytes())
            zipped.writestr('Arandu-NLU-master/addon/config.yaml', 'FIXTURE_TECNICA')

        async def executor(function, *args):
            return function(*args)

        async def save(path, value):
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
            return True

        async def download(url, **kwargs):
            downloaded.append(url)
            return package.read_bytes() if '/releases/download/' in url else archive.getvalue()

        async def api(*, repository, path, **kwargs):
            assert repository == 'jaimevictor/Arandu-NLU'
            assert path == 'custom_components/local_nlu/manifest.json'
            encoded = base64.b64encode(json.dumps(manifest).encode()).decode()
            return SimpleNamespace(data=SimpleNamespace(content=encoded))

        logger = logging.getLogger('hacs-probe')
        hacs = SimpleNamespace(core=SimpleNamespace(config_path=str(config)),
                               configuration=SimpleNamespace(appdaemon_path='appdaemon', plugin_path='www/community',
                                                             python_script_path='python_scripts', theme_path='themes'),
                               hass=SimpleNamespace(async_add_executor_job=executor),
                               log=logger, status=SimpleNamespace(startup=False),
                               async_dispatch=lambda *args: None, async_save_file=save,
                               async_download_file=download, async_github_api_method=api,
                               githubapi=SimpleNamespace(repos=SimpleNamespace(contents=SimpleNamespace(get=None))))
        repo = HacsIntegrationRepository(hacs, 'jaimevictor/Arandu-NLU')
        repo.repository_manifest = HacsManifest.from_dict(metadata)
        repo.ref = repo.data.default_branch = 'master'
        repo.common_validate = AsyncMock()  # External GitHub registry reads only.
        directory = root / 'custom_components/local_nlu'
        repo.tree = [SimpleNamespace(full_path='custom_components/local_nlu', filename='local_nlu', is_directory=True)]
        for source in directory.rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                repo.tree.append(SimpleNamespace(full_path=source.relative_to(root).as_posix(),
                                                 filename=source.name, is_directory=False))
        repo.tree.append(SimpleNamespace(full_path='hacs.json', filename='hacs.json', is_directory=False))
        repo.treefiles = [source.full_path for source in repo.tree]
        repo.get_hacs_json_raw = AsyncMock(return_value=metadata)
        repo.get_integration_manifest = AsyncMock(return_value=manifest)
        assert await repo.validate_repository()
        assert repo.data.domain == 'local_nlu' and repo.display_name == 'ARANDU NLU'
        assert repo.content.path.local == str(config / 'custom_components/local_nlu')
        for validator in (HacsValidator, ManifestValidator, BrandsValidator):
            await validator(repo).async_validate()

        destination = Path(repo.content.path.local)
        destination.mkdir(parents=True)
        (destination / 'manifest.json').write_text('{"domain":"local_nlu","version":"0.4.0"}')
        repo.update_repository = AsyncMock()  # External repository polling only.
        await repo.async_install_repository(version='master')
        assert repo.validate.success, repo.validate.errors
        assert entry.read_bytes() == original
        assert json.loads((destination / 'manifest.json').read_text()) == manifest
        assert not (destination / 'addon').exists()
        assert downloaded and 'archive/refs/tags/master' in downloaded[0]

        # Managed reinstall exercises the actual HACS backup replacement path.
        (destination / 'obsolete.py').write_text('FIXTURE_TECNICA')
        repo.data.installed = True
        await repo.async_install_repository(version='master')
        assert repo.validate.success and not (destination / 'obsolete.py').exists()
        assert entry.read_bytes() == original

        # Actual HACS update decision and tagged source install, GitHub release metadata simulated.
        repo.data.installed_version = 'v0.4.2'
        repo.data.installed_commit = 'fixture-old-commit'
        repo.data.last_commit = 'fixture-new-commit'
        repo.data.releases = True
        repo.data.last_version = 'v' + manifest['version']
        repo.data.selected_tag = None
        assert repo.pending_update and repo.version_to_download() == repo.data.last_version
        await repo.async_install_repository(version=repo.data.last_version)
        assert repo.validate.success, repo.validate.errors
        assert 'archive/refs/tags/v' + manifest['version'] in downloaded[-1]
        assert json.loads((destination / 'manifest.json').read_text()) == manifest
        assert (destination / 'identity.py').exists()
        assert entry.read_bytes() == original and not repo.pending_update

        # Fixed release ZIP: exact official extraction target, no nested component.
        repo.repository_manifest = HacsManifest.from_dict({**metadata, 'zip_release': True,
                                                           'filename': package.name})
        await repo.async_install_repository(version='v' + manifest['version'])
        assert repo.validate.success, repo.validate.errors
        assert entry.read_bytes() == original
        assert json.loads((destination / 'manifest.json').read_text()) == manifest
        assert not (destination / 'custom_components').exists()
    print('OFFICIAL HACS schemas/validators/registration/source install/reinstall/upgrade detection/tagged source/release ZIP PASS (fixtures)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--zip', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.root, args.zip))

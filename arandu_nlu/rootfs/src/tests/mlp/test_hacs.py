"""FIXTURE_TECNICA: HACS distribution, hostile archives and migration boundaries."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

try:
    import test_distribution as distribution_fixtures
except ImportError:
    from tests.mlp import test_distribution as distribution_fixtures
ROOT = distribution_fixtures.ROOT
release_package = distribution_fixtures.release_package
sys.path.insert(0, str(ROOT / 'tools'))
import distribution
import hacs


class HacsTests(unittest.TestCase):
    setUp = distribution_fixtures.DistributionTests.setUp

    def set_metadata(self, name, value):
        path = self.root / 'hacs.json'
        data = json.loads(path.read_text())
        data[name] = value
        path.write_text(json.dumps(data))

    def test_metadata_and_tag_pairing(self):
        version = distribution.check(self.root)
        hacs.check(self.root, version, f'v{version}')
        for tag in ('v0.0.1', version, 'v1.0.0;echo unsafe', 'refs/tags/v' + version):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'tag/version'):
                hacs.check(self.root, version, tag)
        self.set_metadata('version', version)
        with self.assertRaisesRegex(ValueError, 'unsupported HACS'):
            hacs.check(self.root, version)

    def test_invalid_metadata_types_layout_and_release_filename(self):
        version = distribution.check(self.root)
        path = self.root / 'hacs.json'
        original = path.read_text()
        for key, value in [('content_in_root', True), ('content_in_root', 'false'),
                           ('name', 'Other integration'), ('homeassistant', 'yesterday'),
                           ('hide_default_branch', True), ('persistent_directory', '../.storage'),
                           ('zip_release', True)]:
            with self.subTest(key=key, value=value):
                self.set_metadata(key, value)
                with self.assertRaises(ValueError):
                    hacs.check(self.root, version)
                path.write_text(original)

    def test_source_install_is_available_before_first_release(self):
        metadata = json.loads((self.root / 'hacs.json').read_text())
        self.assertFalse(metadata.get('zip_release', False))
        self.assertFalse(metadata.get('hide_default_branch', False))
        self.assertFalse(metadata.get('content_in_root', False))
        self.assertGreaterEqual(tuple(map(int, metadata['homeassistant'].split('.'))), (2025, 3, 0))
        self.assertGreaterEqual(tuple(map(int, metadata['hacs'].split('.'))), (2, 0, 5))

    def test_missing_invalid_manifest_and_second_integration(self):
        version = distribution.check(self.root)
        path = self.root / 'custom_components/local_nlu/manifest.json'
        original = path.read_text()
        for key, value in [('domain', 'other'), ('version', '0.0.1'),
                           ('documentation', None), ('codeowners', [])]:
            data = json.loads(original)
            data[key] = value
            path.write_text(json.dumps(data))
            with self.subTest(key=key), self.assertRaises(ValueError):
                hacs.check(self.root, version)
        path.write_text(original)
        (self.root / 'custom_components/other').mkdir()
        with self.assertRaisesRegex(ValueError, 'exactly one integration'):
            hacs.check(self.root, version)
        (self.root / 'custom_components/other').rmdir()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing integration'):
            hacs.check(self.root, version)

    def test_development_files_and_credentials_refused(self):
        version = distribution.check(self.root)
        directory = self.root / 'custom_components/local_nlu'
        for name in ('.env', 'secrets.yaml', 'tests/test.py', 'fixtures/state.json', 'addon/config.yaml'):
            path = directory / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('FIXTURE_TECNICA')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unexpected integration'):
                hacs.check(self.root, version)
            path.unlink()
        source = directory / 'client.py'
        original = source.read_bytes()
        for credential in (b'-----BEGIN PRIVATE KEY-----', b'ghp_' + b'X' * 36,
                           b'eyJ' + b'A' * 20 + b'.' + b'B' * 20 + b'.' + b'C' * 20,
                           b'api_key = "' + b'X' * 32 + b'"', b'password = "fixture"',
                           b'api_key = "abcd1234"', b'access_token = "abcdefghijklmnop"',
                           b'{"secret": "short-fixture"}'):
            source.write_bytes(original + b'\n# ' + credential)
            with self.subTest(credential=credential[:8]), self.assertRaisesRegex(ValueError, 'credential/private key'):
                hacs.check(self.root, version)
        source.write_bytes(original)

    def test_symlink_file_directory_and_root_refused(self):
        version = distribution.check(self.root)
        directory = self.root / 'custom_components/local_nlu'
        target = Path(self.temp.name) / 'outside'
        target.mkdir()
        (target / 'file.py').write_text('FIXTURE_TECNICA')
        for name, destination in [('client.py', target / 'file.py'), ('linked', target)]:
            path = directory / name
            if path.is_file():
                path.unlink()
            path.symlink_to(destination, target_is_directory=destination.is_dir())
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'symlink'):
                hacs.check(self.root, version)
            path.unlink()
            if name == 'client.py':
                shutil.copyfile(ROOT / 'custom_components/local_nlu/client.py', path)
        shutil.rmtree(directory)
        directory.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            hacs.check(self.root, version)

    def package(self):
        output = Path(self.temp.name) / 'packages'
        results = release_package.package(self.root, output)
        return output / hacs.ASSET, results

    def test_flat_zip_reproducible_verified_hash_and_install_location(self):
        version = distribution.check(self.root)
        archive, results = self.package()
        second = Path(self.temp.name) / 'second'
        release_package.package(self.root, second)
        self.assertEqual(archive.read_bytes(), (second / hacs.ASSET).read_bytes())
        self.assertEqual(hacs.check_zip(archive, version), 30)
        self.assertEqual(archive.with_suffix('.zip.sha256').read_text().split()[0],
                         hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual({item['version'] for item in results}, {version})
        with zipfile.ZipFile(archive) as zipped:
            self.assertIn('manifest.json', zipped.namelist())
            self.assertIn('identity.py', zipped.namelist())
            self.assertFalse(any(name.startswith('custom_components/') for name in zipped.namelist()))
            destination = Path(self.temp.name) / 'ha/custom_components/local_nlu'
            zipped.extractall(destination)  # Same target/operation as official HACS ZIP consumer.
        self.assertEqual(json.loads((destination / 'manifest.json').read_text())['domain'], 'local_nlu')

    def mutate_archive(self, source, name=None, mode=None, data=None, duplicate=False):
        destination = Path(self.temp.name) / 'hostile.zip'
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, 'w') as archive:
            for entry in original.infolist():
                value = original.read(entry)
                if entry.filename == 'client.py':
                    if name is not None:
                        entry.filename = name
                    if mode is not None:
                        entry.external_attr = mode << 16
                    if data is not None:
                        value = data
                    if duplicate:
                        archive.writestr(entry, value)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    archive.writestr(entry, value)
        return destination

    def test_zip_path_traversal_symlink_duplicate_and_secret_refused(self):
        version = distribution.check(self.root)
        archive, _ = self.package()
        for name in ('../.storage/core.config_entries', '/config/secret', 'C:/secret',
                     'translations/../../secret', 'custom_components/local_nlu/client.py',
                     'translations\\..\\secret', '.env', 'addon/config.yaml'):
            hostile = self.mutate_archive(archive, name=name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                hacs.check_zip(hostile, version)
        for mutation in ({'mode': stat.S_IFLNK | 0o777}, {'duplicate': True},
                         {'data': b'-----BEGIN PRIVATE KEY-----'}, {'data': b'X' * 2_000_001}):
            hostile = self.mutate_archive(archive, **mutation)
            with self.subTest(mutation=next(iter(mutation))), self.assertRaises(ValueError):
                hacs.check_zip(hostile, version)
        with self.assertRaisesRegex(ValueError, 'domain/version'):
            hacs.check_zip(archive, '9.9.9')

    def test_zip_collision_preserves_existing_asset(self):
        output = Path(self.temp.name) / 'collision'
        output.mkdir()
        sentinel = output / hacs.ASSET
        sentinel.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            release_package.package(self.root, output)
        self.assertEqual(sentinel.read_bytes(), b'original')
        self.assertEqual(list(output.iterdir()), [sentinel])

    def test_manual_entry_options_and_registry_survive_file_replacement(self):
        version = distribution.check(self.root)
        archive, _ = self.package()
        config = Path(self.temp.name) / 'ha'
        storage = config / '.storage'
        storage.mkdir(parents=True)
        records = {
            'core.config_entries': {'domain': 'local_nlu', 'entry_id': 'fixture-entry',
                                    'data': {'endpoint': 'http://fixture-nlu:11555'},
                                    'options': {'contextual_enabled': True, 'session_ttl': 30}},
            'core.entity_registry': {'unique_id': 'fixture-entry-conversation'},
        }
        for name, record in records.items():
            (storage / name).write_text(json.dumps(record))
        before = {path.name: path.read_bytes() for path in storage.iterdir()}
        destination = config / 'custom_components/local_nlu'
        destination.mkdir(parents=True)
        (destination / 'manifest.json').write_text('{"domain":"local_nlu","version":"0.4.0"}')
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
        self.assertEqual(before, {path.name: path.read_bytes() for path in storage.iterdir()})
        self.assertEqual(json.loads((destination / 'manifest.json').read_text())['version'], version)
        # IDs and flow schema belong to runtime and are intentionally unchanged.
        self.assertIn('VERSION = 1', (destination / 'config_flow.py').read_text())
        self.assertIn('{config_entry.entry_id}-conversation', (destination / 'conversation.py').read_text())

    def test_release_workflow_does_not_publish_on_push_or_create_release(self):
        workflow = distribution.yaml_file(self.root / '.github/workflows/release-integration.yml')
        self.assertEqual(workflow['on'], {'release': {'types': ['published']}})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['integration']
        self.assertEqual(job['permissions'], {'contents': 'write'})
        checkout = job['steps'][0]
        self.assertEqual(checkout['env']['CHECKOUT_REF'], '${{ github.event.release.tag_name }}')
        self.assertIn('git fetch --no-tags --depth=1 -- origin "refs/tags/$CHECKOUT_REF"', checkout['run'])
        self.assertIn('git checkout --detach FETCH_HEAD', checkout['run'])
        self.assertNotIn('token', checkout['env'])
        scripts = '\n'.join(step.get('run', '') for step in job['steps'])
        self.assertIn('--tag "$RELEASE_TAG"', scripts)
        self.assertIn('gh release upload "$RELEASE_TAG"', scripts)
        for command in ('gh release create', 'git tag', 'git push', '--clobber'):
            self.assertNotIn(command, scripts)
        validation = distribution.yaml_file(self.root / '.github/workflows/hacs.yml')
        self.assertEqual(validation['permissions'], {'contents': 'read'})
        public_checkout = validation['jobs']['distribution']['steps'][0]
        self.assertEqual(public_checkout['env']['CHECKOUT_REF'], '${{ github.sha }}')
        for step in (checkout, public_checkout):
            self.assertIn("git sparse-checkout set --no-cone '/*' '!/*/' '/.github/' '/addon/' '/custom_components/' '/data/' '/tests/' '/tools/' '!/implementation-clean-room'", step['run'])
            self.assertNotIn('submodule', step['run'])
            self.assertNotIn('extraheader', step['run'])
        official = validation['jobs']['official-hacs']['steps'][0]
        self.assertTrue(official['uses'].startswith('docker://ghcr.io/hacs/action@sha256:'))
        self.assertEqual(official['env']['INPUT_IGNORE'].split(), ['description', 'topics'])

    def test_public_checkout_handles_legacy_gitlink_without_storing_credentials(self):
        origin = Path(self.temp.name) / 'git-origin'
        origin.mkdir()
        def git(path, *arguments):
            return subprocess.check_output(['git', '-C', str(path), *arguments], text=True,
                                           stderr=subprocess.STDOUT).strip()
        git(origin, 'init', '-q')
        git(origin, 'config', 'user.name', 'FIXTURE_TECNICA')
        git(origin, 'config', 'user.email', 'fixture@example.invalid')
        (origin / 'README.md').write_text('FIXTURE_TECNICA')
        (origin / 'addon').mkdir()
        (origin / 'addon/product').write_text('FIXTURE_TECNICA')
        git(origin, 'add', '.')
        git(origin, 'commit', '-qm', 'fixture')
        commit = git(origin, 'rev-parse', 'HEAD')
        git(origin, 'update-index', '--add', '--cacheinfo', f'160000,{commit},implementation-clean-room')
        git(origin, 'commit', '-qm', 'fixture legacy gitlink without URL')
        checkout = Path(self.temp.name) / 'git-checkout'
        checkout.mkdir()
        git(checkout, 'init', '-q')
        git(checkout, 'remote', 'add', 'origin', str(origin))
        revision = git(origin, 'rev-parse', 'HEAD')
        git(checkout, 'fetch', '--no-tags', '--depth=1', '--', 'origin', revision)
        git(checkout, 'sparse-checkout', 'set', '--no-cone', '/*', '!/*/', '/.github/', '/addon/',
            '/custom_components/', '/data/', '/tests/', '/tools/', '!/implementation-clean-room')
        git(checkout, 'checkout', '--detach', 'FETCH_HEAD')
        self.assertTrue((checkout / 'addon/product').is_file())
        self.assertTrue((checkout / 'README.md').is_file())
        self.assertFalse((checkout / 'implementation-clean-room').exists())
        self.assertIn('160000', git(checkout, 'ls-files', '--stage', 'implementation-clean-room'))
        result = subprocess.run(['git', '-C', str(checkout), 'config', '--local', '--get-regexp',
                                 r'credential|extraheader'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        # A published release must never fall back to a same-named branch.
        git(origin, 'branch', 'v0.0.1', commit)
        result = subprocess.run(['git', '-C', str(checkout), 'fetch', '--no-tags', '--depth=1',
                                 '--', 'origin', 'refs/tags/v0.0.1'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        git(origin, 'tag', 'v0.0.1', revision)  # Technical fixture only, not a product tag.
        git(checkout, 'fetch', '--no-tags', '--depth=1', '--', 'origin', 'refs/tags/v0.0.1')
        git(checkout, 'checkout', '--detach', 'FETCH_HEAD')
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), revision)
        self.assertNotEqual(git(checkout, 'rev-parse', 'HEAD'), commit)

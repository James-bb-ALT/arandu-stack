"""Distribution regressions with synthetic mutations, no NLU behavior changes."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import distribution
spec = importlib.util.spec_from_file_location('release_package', ROOT / 'tools/release-package.py')
release_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_package)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        for folder in ('addon', 'custom_components/local_nlu', 'tools', '.github'):
            shutil.copytree(ROOT / folder, self.root / folder,
                            ignore=shutil.ignore_patterns('target', '__pycache__'))
        for name in ('repository.yaml', 'hacs.json', 'release.json', 'README.md', 'INSTALL.md', 'Cargo.lock'):
            shutil.copyfile(ROOT / name, self.root / name)

    def replace(self, name, old, new):
        path = self.root / name
        path.write_text(path.read_text().replace(old, new))

    def test_current_distribution_and_reproducible_packages(self):
        version = distribution.check(self.root)
        first = release_package.package(self.root, Path(self.temp.name) / 'one')
        second = release_package.package(self.root, Path(self.temp.name) / 'two')
        self.assertEqual(first, second)
        self.assertEqual({item['version'] for item in first}, {version})

    def test_package_output_collision_preserves_existing_files(self):
        output = Path(self.temp.name) / 'existing'
        output.mkdir()
        sentinel = output / 'packages.json'
        sentinel.write_text('existing user artifact')
        with self.assertRaises(FileExistsError):
            release_package.package(self.root, output)
        self.assertEqual(sentinel.read_text(), 'existing user artifact')
        self.assertEqual(list(output.iterdir()), [sentinel])

    def test_nested_duplicate_slug_and_other_installable_copy(self):
        directory = self.root / 'unexpected/nested'
        directory.mkdir(parents=True)
        path = directory / 'config.yaml'
        shutil.copyfile(self.root / 'addon/config.yaml', path)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            distribution.check_store(self.root)
        path.write_text(path.read_text().replace('slug: ptbr_nlu', 'slug: another'))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            distribution.check_store(self.root)

    def test_component_and_lock_versions_diverge(self):
        version = distribution.check(self.root)
        for name in ('custom_components/local_nlu/manifest.json', 'addon/engine/Cargo.toml',
                     'addon/engine/Cargo.lock', 'Cargo.lock'):
            path = self.root / name
            original = path.read_text()
            path.write_text(original.replace(version, '9.9.9'))
            with self.assertRaisesRegex(ValueError, 'versions differ'):
                distribution.check_versions(self.root, version)
            path.write_text(original)

    def test_changed_product_requires_new_version(self):
        path = self.root / 'addon/engine/src/lib.rs'
        path.write_text(path.read_text() + '\n// FIXTURE_TECNICA mutation\n')
        with self.assertRaisesRegex(ValueError, 'bump version'):
            distribution.check(self.root)
        with self.assertRaisesRegex(ValueError, 'newer product version'):
            distribution.record(self.root)

    def test_stale_main_docs_but_historical_versions_allowed(self):
        version = distribution.check(self.root)
        self.replace('README.md', f'Versão {version}', 'Versão 0.2.0')
        with self.assertRaisesRegex(ValueError, 'stale'):
            distribution.check_versions(self.root, version)
        self.replace('README.md', 'Versão 0.2.0', f'Versão {version}')
        with (self.root / 'README.md').open('a') as output:
            output.write('\nHistorical release 0.2.0\n')
        distribution.check_versions(self.root, version)

    def test_wrong_build_input_missing_contextual_and_architecture(self):
        recipe = self.root / 'addon/Dockerfile'
        original = recipe.read_text()
        recipe.write_text(original.replace('COPY engine engine', 'COPY old engine'))
        with self.assertRaisesRegex(ValueError, 'Dockerfile'):
            distribution.check_store(self.root)
        recipe.write_text(original)
        module = self.root / 'addon/engine/src/contextual/mod.rs'
        module.unlink()
        with self.assertRaisesRegex(ValueError, 'contextual'):
            distribution.check_store(self.root)

    def test_invalid_repository_and_version(self):
        self.replace('repository.yaml', 'name: ARANDU NLU', 'name: []')
        with self.assertRaisesRegex(ValueError, 'repository'):
            distribution.check_store(self.root)
        with self.assertRaisesRegex(ValueError, 'semantic version'):
            distribution.check_versions(self.root, 'bad')

    def test_architecture_and_missing_build_resource(self):
        self.replace('addon/config.yaml', '  - aarch64', '  - armv7')
        with self.assertRaisesRegex(ValueError, 'native'):
            distribution.check_store(self.root)
        self.replace('addon/config.yaml', '  - armv7', '  - aarch64')
        shutil.rmtree(self.root / 'addon/licenses')
        with self.assertRaisesRegex(ValueError, 'COPY source'):
            distribution.check_store(self.root)

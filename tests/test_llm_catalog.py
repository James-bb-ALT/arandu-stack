"""Unit tests for arandu_stack.app.llm_catalog."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from arandu_stack.app.llm_catalog import (
    DEFAULT_MODEL_ID,
    MODELS,
    describe_model,
    get_model_file_path,
    get_model_info,
    is_installed,
    validate_model_id,
    verify_model_file,
)


class TestLLMCatalog(unittest.TestCase):
    def test_catalog_contains_three_new_models_and_legacy_tucano(self):
        expected_keys = {'lfm2.5-1.2b', 'lfm3-1.7b', 'qwen3-1.7b', 'tucano'}
        self.assertTrue(expected_keys.issubset(set(MODELS.keys())))

    def test_model_metadata_invariants(self):
        for model_id, info in MODELS.items():
            self.assertEqual(info['id'], model_id)
            self.assertIn('name', info)
            self.assertIn('size', info)
            self.assertGreater(info['size'], 0)
            self.assertIn('sha256', info)
            self.assertEqual(len(info['sha256']), 64)
            self.assertIn('repo', info)
            self.assertIn('revision', info)
            self.assertIn('file', info)
            self.assertTrue(info['file'].endswith('.gguf'))
            self.assertIn('memory_limit_mb', info)

    def test_validate_model_id_success_and_failure(self):
        validate_model_id('lfm2.5-1.2b')
        validate_model_id('lfm3-1.7b')
        validate_model_id('qwen3-1.7b')
        validate_model_id('tucano')

        with self.assertRaises(ValueError):
            validate_model_id('non_existent_model')
        with self.assertRaises(ValueError):
            validate_model_id(None)

    def test_describe_model_and_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.assertFalse(is_installed(root, 'lfm2.5-1.2b'))
            desc = describe_model(root, 'lfm2.5-1.2b')
            self.assertFalse(desc['installed'])
            self.assertEqual(desc['state'], 'available')
            self.assertIsNone(desc['path'])

    def test_verify_model_file_validations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            model_dir = root / 'tucano'
            model_dir.mkdir(parents=True)
            info = MODELS['tucano']
            target_file = model_dir / info['file']

            # 1. Arquivo não existente
            with self.assertRaises(FileNotFoundError):
                verify_model_file(target_file, 'tucano')

            # 2. Tamanho incorreto
            target_file.write_bytes(b'GGUF1234')
            with self.assertRaises(ValueError) as ctx:
                verify_model_file(target_file, 'tucano')
            self.assertIn("Tamanho GGUF divergente", str(ctx.exception))

            # 3. Cabeçalho incorreto com tamanho correto
            with target_file.open('wb') as f:
                f.write(b'BADF')
                f.seek(info['size'] - 1)
                f.write(b'\0')
            with self.assertRaises(ValueError) as ctx:
                verify_model_file(target_file, 'tucano')
            self.assertIn("Cabeçalho inválido", str(ctx.exception))

            # 4. Cabeçalho GGUF correto, tamanho correto, mas SHA divergente
            with target_file.open('wb') as f:
                f.write(b'GGUF')
                f.seek(info['size'] - 1)
                f.write(b'\0')
            with self.assertRaises(ValueError) as ctx:
                verify_model_file(target_file, 'tucano')
            self.assertIn("Integridade SHA-256 violada", str(ctx.exception))


if __name__ == '__main__':
    unittest.main()

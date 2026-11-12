"""Unit tests for arandu_stack.app.llm_lab (LLMModelManager)."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from arandu_stack.app.llm_catalog import MODELS
from arandu_stack.app.llm_lab import LLMModelManager
from arandu_stack.app.model_memory import ResidencyPolicy


class TestLLMModelManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.manager = LLMModelManager(
            data_dir=self.data_dir,
            upstream_port=11570,
            total_memory_budget_mb=2048,
        )

        # Criar dados de modelo falso instalados para testes
        self.fake_info = dict(MODELS['tucano'])
        self.fake_size = 512
        header = b'GGUF'
        body = b'Z' * (self.fake_size - len(header))
        self.fake_bytes = header + body
        self.fake_sha256 = hashlib.sha256(self.fake_bytes).hexdigest()

        self.fake_info['size'] = self.fake_size
        self.fake_info['sha256'] = self.fake_sha256

    async def asyncTearDown(self):
        await self.manager.close()
        self.tmp_dir.cleanup()

    async def test_list_models_returns_all_catalog_entries(self):
        result = await self.manager.list_models()
        self.assertIn('models', result)
        self.assertIn('configured_model', result)
        self.assertIn('memory', result)
        model_ids = {m['id'] for m in result['models']}
        self.assertIn('lfm2.5-1.2b', model_ids)
        self.assertIn('lfm3-1.7b', model_ids)
        self.assertIn('qwen3-1.7b', model_ids)
        self.assertIn('tucano', model_ids)

    async def test_switch_model_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError) as ctx:
            await self.manager.switch_model('tucano', confirm=False)
        self.assertIn("Confirmação explícita", str(ctx.exception))

    async def test_switch_model_fails_if_not_installed(self):
        with self.assertRaises(ValueError) as ctx:
            await self.manager.switch_model('lfm2.5-1.2b', confirm=True)
        self.assertIn("não está instalado", str(ctx.exception))

    async def test_switch_model_success_and_last_good_save(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Instalar o arquivo falso no disco
            tucano_dir = self.manager.models_dir / 'tucano'
            tucano_dir.mkdir(parents=True)
            tucano_file = tucano_dir / self.fake_info['file']
            tucano_file.write_bytes(self.fake_bytes)

            mock_restart = AsyncMock()
            # Mockar prontidão HTTP para retornar ok
            with patch.object(self.manager, 'check_upstream_health', return_value={'model': 'tucano', 'ready': True}):
                res = await self.manager.switch_model('tucano', confirm=True, restart_callback=mock_restart)
                self.assertTrue(res['switched'])
                self.assertEqual(self.manager.active_model, 'tucano')
                mock_restart.assert_called_once_with('tucano')

                # Verificar se last-good foi salvo
                self.assertTrue(self.manager.last_good_file.exists())

    async def test_switch_model_failure_triggers_automatic_rollback(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Instalar tucano
            tucano_dir = self.manager.models_dir / 'tucano'
            tucano_dir.mkdir(parents=True)
            (tucano_dir / self.fake_info['file']).write_bytes(self.fake_bytes)

            # Modelo anterior é lfm2.5-1.2b
            initial_model = self.manager.active_model

            # Callback que simula falha ao reiniciar o processo upstream para o novo modelo
            rollback_called = []
            async def failing_restart(model_id):
                if model_id == 'tucano':
                    raise RuntimeError("Erro ao inicializar backend llama.cpp")
                rollback_called.append(model_id)

            with self.assertRaises(ValueError) as ctx:
                await self.manager.switch_model('tucano', confirm=True, restart_callback=failing_restart)

            self.assertIn("Rollback executado", str(ctx.exception))
            # Garantir que o modelo ativo voltou para o inicial e foi chamado no rollback
            self.assertEqual(self.manager.active_model, initial_model)
            self.assertIn(initial_model, rollback_called)

    async def test_preload_resident_models(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Configurar tucano como RESIDENT
            self.manager.set_residency_policy('tucano', ResidencyPolicy.RESIDENT)
            tucano_dir = self.manager.models_dir / 'tucano'
            tucano_dir.mkdir(parents=True)
            (tucano_dir / self.fake_info['file']).write_bytes(self.fake_bytes)

            loaded_tracker = []

            async def loader(m):
                loaded_tracker.append(m)

            loaded = await self.manager.preload_resident_models(loader_callback=loader)
            self.assertIn('tucano', loaded)
            self.assertIn('tucano', loaded_tracker)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for arandu_stack.app.model_memory."""
from pathlib import Path
import tempfile
import time
import unittest

from arandu_stack.app.llm_catalog import MODELS
from arandu_stack.app.model_memory import ModelMemoryManager, ResidencyPolicy


class TestModelMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        # Total de 2048 MB, reserva STT=512, TTS=256 -> Líquido LLM = 1280 MB
        self.mem = ModelMemoryManager(
            data_dir=self.root,
            total_memory_budget_mb=2048,
            stt_reserved_mb=512,
            tts_reserved_mb=256,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_default_policies_and_persistence(self):
        # Políticas padrão
        self.assertEqual(self.mem.get_policy('lfm2.5-1.2b'), ResidencyPolicy.RESIDENT)
        self.assertEqual(self.mem.get_policy('lfm3-1.7b'), ResidencyPolicy.LAZY)
        self.assertEqual(self.mem.get_policy('qwen3-1.7b'), ResidencyPolicy.LAZY)
        self.assertEqual(self.mem.get_policy('tucano'), ResidencyPolicy.STANDBY)

        # Alterar e salvar
        self.mem.set_policy('lfm3-1.7b', ResidencyPolicy.RESIDENT)
        self.assertEqual(self.mem.get_policy('lfm3-1.7b'), ResidencyPolicy.RESIDENT)

        # Restaurar em nova instância
        new_mem = ModelMemoryManager(data_dir=self.root)
        self.assertEqual(new_mem.get_policy('lfm3-1.7b'), ResidencyPolicy.RESIDENT)

    def test_footprint_estimation_for_liquid_vs_transformer(self):
        # Liquid LFM tem KV-cache otimizado
        lfm_fp = self.mem.estimate_model_footprint('lfm2.5-1.2b', context_size=4096)
        self.assertGreater(lfm_fp, 600)  # ~663 MB weights + KV

        # Qwen 1.7B
        qwen_fp = self.mem.estimate_model_footprint('qwen3-1.7b', context_size=4096)
        self.assertGreater(qwen_fp, 1200)

    def test_llm_budget_and_headroom_cooperation(self):
        available = self.mem.get_llm_available_budget()
        self.assertEqual(available, 1280)  # 2048 - 512 - 256

        self.assertTrue(self.mem.check_headroom(500))
        self.assertFalse(self.mem.check_headroom(1500))

    def test_eviction_priority_and_lru(self):
        # Simular 3 modelos carregados
        # Tucano (STANDBY): 350 MB
        self.mem.register_loaded('tucano', rss_mb=350)
        # LFM3 (LAZY): 1100 MB, usado 10 segundos atrás
        self.mem.register_loaded('lfm3-1.7b', rss_mb=1100)
        self.mem.loaded_models['lfm3-1.7b']['last_used'] = time.time() - 10
        # Qwen3 (LAZY): 1300 MB, usado 2 segundos atrás
        self.mem.register_loaded('qwen3-1.7b', rss_mb=1300)
        self.mem.loaded_models['qwen3-1.7b']['last_used'] = time.time() - 2

        # Precisamos de 800 MB para carregar LFM2.5 (RESIDENT)
        # O modelo STANDBY (tucano) deve ser o primeiro candidato
        candidates = self.mem.get_eviction_candidates(needed_mb=800, target_model_id='lfm2.5-1.2b')
        self.assertIn('tucano', candidates)
        # E entre os LAZY, o lfm3-1.7b (mais antigo) deve vir antes do qwen3-1.7b
        if 'lfm3-1.7b' in candidates and 'qwen3-1.7b' in candidates:
            idx_lfm3 = candidates.index('lfm3-1.7b')
            idx_qwen3 = candidates.index('qwen3-1.7b')
            self.assertLess(idx_lfm3, idx_qwen3)

    def test_describe_returns_complete_overview(self):
        desc = self.mem.describe()
        self.assertEqual(desc['total_budget_mb'], 2048)
        self.assertEqual(desc['stt_reserved_mb'], 512)
        self.assertEqual(desc['tts_reserved_mb'], 256)
        self.assertEqual(desc['llm_budget_mb'], 1280)
        self.assertIn('policies', desc)


if __name__ == '__main__':
    unittest.main()

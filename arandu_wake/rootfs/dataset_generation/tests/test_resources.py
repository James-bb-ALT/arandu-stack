from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wakegen.common import load_config
from wakegen.engine import Engine


class ResourceTests(unittest.TestCase):
    def engine(self):
        engine = Engine.__new__(Engine)
        engine.config = load_config()
        engine.config["resources"]["device"] = "auto"
        engine.force_cpu = False
        engine.disk_guard = lambda: None
        engine.status = SimpleNamespace(update=Mock())
        engine.torch = SimpleNamespace(version=SimpleNamespace(cuda="12.8"), cuda=SimpleNamespace(is_available=Mock(return_value=True)))
        return engine

    def test_occupied_gpu_never_initialized(self):
        engine = self.engine()
        with patch("wakegen.engine.gpu_memory", return_value={"free_gb": 0.8}), patch("wakegen.engine.psutil.virtual_memory", return_value=SimpleNamespace(available=10 * 1024 ** 3)):
            self.assertEqual(engine.choose_device("design"), "cpu")
        engine.torch.cuda.is_available.assert_not_called()

    def test_cuda_mode_waits_instead_of_falling_back_to_cpu(self):
        engine = self.engine()
        engine.config["resources"]["device"] = "cuda"
        with patch("wakegen.engine.check_stop"), patch("wakegen.engine.gpu_memory", return_value={"free_gb": 0.8}), \
                patch("wakegen.engine.wait_interruptibly", side_effect=InterruptedError("waiting")):
            with self.assertRaisesRegex(InterruptedError, "waiting"):
                engine.choose_device("design")
        self.assertEqual(engine.status.update.call_args.kwargs["stage"], "waiting_gpu")
        engine.torch.cuda.is_available.assert_not_called()

    def test_cuda_mode_rejects_cpu_only_installation(self):
        engine = self.engine()
        engine.config["resources"]["device"] = "cuda"
        engine.torch.version.cuda = None
        with patch("wakegen.engine.check_stop"), patch("wakegen.engine.gpu_memory", return_value={"free_gb": 9.0}):
            with self.assertRaisesRegex(RuntimeError, "PyTorch com CUDA"):
                engine.choose_device("design")

    def test_cuda_gate_requires_capacity_and_reserve(self):
        engine = self.engine()
        with patch("wakegen.engine.gpu_memory", return_value={"free_gb": 9.0}), patch("wakegen.engine.psutil.virtual_memory", return_value=SimpleNamespace(available=10 * 1024 ** 3)):
            self.assertEqual(engine.choose_device("design"), "cuda:0")
        engine.torch.cuda.is_available.assert_called_once()

    def test_cpu_build_stays_cpu_even_if_gpu_free(self):
        engine = self.engine()
        engine.torch.version.cuda = None
        with patch("wakegen.engine.gpu_memory", return_value={"free_gb": 10.0}), patch("wakegen.engine.psutil.virtual_memory", return_value=SimpleNamespace(available=10 * 1024 ** 3)):
            self.assertEqual(engine.choose_device("base"), "cpu")
        engine.torch.cuda.is_available.assert_not_called()

    def test_oom_fallback_ignores_free_gpu(self):
        engine = self.engine()
        engine.force_cpu = True
        with patch("wakegen.engine.gpu_memory", return_value={"free_gb": 10.0}), patch("wakegen.engine.psutil.virtual_memory", return_value=SimpleNamespace(available=10 * 1024 ** 3)):
            self.assertEqual(engine.choose_device("base"), "cpu")
        engine.torch.cuda.is_available.assert_not_called()

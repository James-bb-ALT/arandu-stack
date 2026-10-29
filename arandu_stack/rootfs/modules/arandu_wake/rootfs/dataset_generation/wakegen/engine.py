"""Um único Qwen carregado por vez; nunca manipula processos/modelos alheios."""
from __future__ import annotations

import gc
import json
import logging
import os
import random
import shutil
import subprocess

import numpy as np
import psutil

from .common import ROOT, check_stop, wait_interruptibly

LOG = logging.getLogger(__name__)


def gpu_memory():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            free, total = [float(value.strip()) / 1024 for value in result.stdout.splitlines()[0].split(",")]
            return {"free_gb": free, "total_gb": total}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return {"free_gb": None, "total_gb": None}


class Engine:
    def __init__(self, config, status):
        os.environ["HF_HOME"] = str(ROOT / "hf-cache")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["OMP_NUM_THREADS"] = str(config["resources"]["cpu_threads"])
        os.environ["MKL_NUM_THREADS"] = str(config["resources"]["cpu_threads"])
        import torch
        self.torch = torch
        torch.set_num_threads(config["resources"]["cpu_threads"])
        torch.set_num_interop_threads(1)
        self.config = config
        self.status = status
        self.model = None
        self.kind = None
        self.prompt = None
        self.prompt_speaker = None
        self.device = None
        self.force_cpu = False
        self.registry = json.loads((ROOT / "state/models.json").read_text(encoding="utf-8"))
        self.asr = None

    def disk_guard(self):
        free = shutil.disk_usage(ROOT).free / (1024 ** 3)
        if free < self.config["resources"]["minimum_disk_free_gb"]:
            raise RuntimeError(f"Espaço de disco insuficiente: {free:.1f} GiB livres; dados preservados")

    def release(self):
        device = self.device
        self.prompt = None
        self.prompt_speaker = None
        self.model = None
        self.kind = None
        self.device = None
        gc.collect()
        if device and device.startswith("cuda"):
            self.torch.cuda.empty_cache()  # Somente o cache deste processo.

    def choose_device(self, kind):
        limits = self.config["resources"]
        while True:
            check_stop()
            self.disk_guard()
            gpu = gpu_memory()
            # is_available só é consultado se o binário tiver CUDA e houver folga medida.
            cuda_compiled = self.torch.version.cuda is not None
            required_gpu = limits["cuda_min_free_gb"][kind] + limits["cuda_headroom_gb"]
            if limits["device"] == "cuda" and not cuda_compiled:
                raise RuntimeError("Modo CUDA exige PyTorch com CUDA; execute scripts/setup.ps1")
            if limits["device"] != "cpu" and not self.force_cpu and cuda_compiled and gpu["free_gb"] is not None and gpu["free_gb"] >= required_gpu:
                if self.torch.cuda.is_available():
                    return "cuda:0"
            if limits["device"] == "cuda":
                self.status.update(stage="waiting_gpu", device="cuda:0", model_kind=kind,
                    detail=f"Aguardando {required_gpu:.2f} GiB livres na GPU para {kind}; sem fallback para CPU",
                    required_gpu_free_gb=required_gpu, gpu=gpu)
                wait_interruptibly(limits["retry_wait_seconds"])
                self.force_cpu = False
                continue
            free_cpu = psutil.virtual_memory().available / (1024 ** 3)
            if free_cpu >= limits["cpu_min_free_gb"][kind]:
                return "cpu"
            self.status.update(stage="waiting_resources", detail=f"Aguardando RAM para {kind}; nenhum processo externo será alterado", available_ram_gb=free_cpu, gpu=gpu)
            wait_interruptibly(limits["retry_wait_seconds"])

    def load(self, kind):
        if self.kind == kind:
            return self.model
        self.release()
        self.device = self.choose_device(kind)
        self.status.update(stage="loading_model", model_kind=kind, device=self.device, gpu=gpu_memory())
        from qwen_tts import Qwen3TTSModel
        LOG.info("Carregando %s em %s", kind, self.device)
        dtype = getattr(self.torch, self.config["resources"]["cpu_dtype"][kind]) if self.device == "cpu" else self.torch.bfloat16
        self.model = Qwen3TTSModel.from_pretrained(
            self.registry[kind]["path"], device_map=self.device, dtype=dtype,
            attn_implementation="sdpa", local_files_only=True,
        )
        self.kind = kind
        supported = {language.casefold() for language in self.model.get_supported_languages()}
        if "portuguese" not in supported:
            raise RuntimeError("Portuguese não suportado pelo modelo instalado")
        return self.model

    def execution_details(self):
        return {"device": self.device, "torch_version": self.torch.__version__, "cpu_threads": self.config["resources"]["cpu_threads"],
                "dtype": str(next(self.model.model.parameters()).dtype) if self.model is not None else None,
                "seed_reproducibility": "best_effort_not_bit_identical_across_hardware"}

    def seed(self, seed):
        random.seed(seed)
        np.random.seed(seed)
        self.torch.manual_seed(seed)

    def kwargs(self, reference=False):
        gen = self.config["generation"]
        return {"do_sample": True, "temperature": gen["temperature"], "top_p": gen["top_p"],
                "repetition_penalty": gen["repetition_penalty"],
                "max_new_tokens": gen["reference_max_new_tokens"] if reference else gen["max_new_tokens"]}

    def design(self, profile, seed):
        model = self.load("design")
        self.seed(seed)
        self.status.update(stage="synthesizing_reference", speaker_id=profile["speaker_id"])
        with self.torch.inference_mode():
            audios, rate = model.generate_voice_design(
                text=self.config["voice_bank"]["reference_text"], language="Portuguese",
                instruct=profile["description"], **self.kwargs(reference=True))
        return np.asarray(audios[0], dtype=np.float32).reshape(-1), int(rate)

    def prepare_voice(self, profile):
        model = self.load("base")
        cache_key = (profile["speaker_id"], profile["reference_sha256"])
        if self.prompt_speaker != cache_key:
            with self.torch.inference_mode():
                self.prompt = model.create_voice_clone_prompt(ref_audio=str(ROOT / profile["reference_path"]),
                    ref_text=self.config["voice_bank"]["reference_text"], x_vector_only_mode=False)
            self.prompt_speaker = cache_key
        embedding = self.prompt[0].ref_spk_embedding.detach().float().cpu().numpy().reshape(-1)
        return embedding.tolist()

    def clone(self, text, profile, seed):
        self.prepare_voice(profile)
        self.seed(seed)
        self.disk_guard()
        check_stop()
        self.status.update(stage="synthesizing_sample", speaker_id=profile["speaker_id"], device=self.device)
        with self.torch.inference_mode():
            audios, rate = self.model.generate_voice_clone(text=text, language="Portuguese",
                voice_clone_prompt=self.prompt, non_streaming_mode=True, **self.kwargs())
        return np.asarray(audios[0], dtype=np.float32).reshape(-1), int(rate)

    def recognize(self, audio, rate):
        if not self.config["asr"]["enabled"]:
            return {"enabled": False, "text": None}
        from .audio_validation import resample
        from faster_whisper import WhisperModel
        if self.asr is None:
            self.asr = WhisperModel(self.registry["asr"]["path"], device="cpu", compute_type="int8",
                cpu_threads=self.config["asr"]["cpu_threads"], num_workers=1, local_files_only=True)
        segments, info = self.asr.transcribe(resample(audio, rate, 16000), language="pt", beam_size=1,
            vad_filter=False, condition_on_previous_text=False)
        pieces = list(segments)
        return {"enabled": True, "model": self.registry["asr"]["repo"], "revision": self.registry["asr"]["revision"],
                "text": " ".join(item.text.strip() for item in pieces),
                "average_log_probability": float(np.mean([item.avg_logprob for item in pieces])) if pieces else None,
                "interpretation": "independent_asr_corroboration_not_phonetic_certification"}

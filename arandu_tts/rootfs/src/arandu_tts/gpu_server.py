"""Worker HTTP privado para vozes OmniVoice (.pt) na RTX 3080.

Requer extras opcionais de GPU, não é instalado no add-on CPU.
"""
from __future__ import annotations

import argparse
import hmac
import io
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .voices import valid_voice_id

LOG = logging.getLogger(__name__)


class VoiceGPU:
    def __init__(self, profiles: Path, model_id: str = "k2-fsa/OmniVoice", device: str = "cuda:0") -> None:
        import torch
        from omnivoice import OmniVoice
        self.profiles = profiles
        self.profiles.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.lock = threading.Lock()
        self.model = OmniVoice.from_pretrained(model_id, device_map=device,
                                                 dtype=torch.float16 if device.startswith("cuda") else torch.float32)
        self._last_name: str | None = None
        self._prompt = None

    def speak(self, text: str, name: str) -> bytes:
        import numpy as np
        import soundfile as sf
        from omnivoice import VoiceClonePrompt
        if not valid_voice_id(name):
            raise ValueError("ID da voz inválido")
        profile = self.profiles / (name + ".pt")
        if not profile.is_file() or profile.is_symlink():
            raise FileNotFoundError("Perfil de voz não encontrado")
        with self.lock:
            if name != self._last_name:
                # Carregar somente prompts locais produzidos pelo próprio OmniVoice.
                self._prompt = VoiceClonePrompt.load(str(profile))
                self._last_name = name
            result = self.model.generate(text=text, voice_clone_prompt=self._prompt)
            samples = np.asarray(result[0], dtype="float32").reshape(-1)
            if not np.isfinite(samples).all():
                raise ValueError("Saída inválida do modelo")
            pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
            with io.BytesIO() as memory:
                sf.write(memory, pcm16, 24000, format="WAV", subtype="PCM_16")
                return memory.getvalue()


def make_handler(engine: VoiceGPU, token: str):
    if len(token) < 24:
        raise ValueError("Defina ARANDU_GPU_TOKEN com pelo menos 24 caracteres aleatórios")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AranduGPU/0.1"

        def respond_json(self, status: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self.respond_json(200, {"ok": True, "backend": "omnivoice"})
            else:
                self.respond_json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/synthesize":
                self.respond_json(404, {"error": "not_found"})
                return
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + token):
                self.respond_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not (2 <= size <= 16384):
                    raise ValueError("Tamanho da requisição inválido")
                data = json.loads(self.rfile.read(size).decode("utf-8"))
                phrase = data.get("text")
                voice_id = data.get("voice")
                if not isinstance(phrase, str) or not (1 <= len(phrase.strip()) <= 3000):
                    raise ValueError("Texto inválido")
                if not isinstance(voice_id, str) or not valid_voice_id(voice_id):
                    raise ValueError("ID de voz inválido")
                wav_bytes = engine.speak(phrase.strip(), voice_id)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(wav_bytes)
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
                self.respond_json(400, {"error": str(exc)})
            except FileNotFoundError:
                self.respond_json(404, {"error": "profile_not_found"})
            except Exception:
                LOG.exception("Falha na GPU")
                self.respond_json(500, {"error": "synthesis_failed"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Arandu TTS Worker GPU (OmniVoice)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10312)
    parser.add_argument("--profiles", type=Path, default=Path("./profiles"))
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--model", default="k2-fsa/OmniVoice")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("ARANDU_GPU_TOKEN", "")
    if len(token) < 24:
        parser.error("ARANDU_GPU_TOKEN deve ter pelo menos 24 caracteres")
    engine = VoiceGPU(args.profiles, model_id=args.model, device=args.device)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(engine, token))
    LOG.warning("Worker GPU disponível em %s:%d (não exponha na Internet)", args.host, args.port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

"""Sonda Wyoming real usando a biblioteca oficial; não grava áudio recebido."""

import asyncio
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.info import Describe, Info
from wyoming.wake import Detect, Detection, NotDetected


def check_uri(uri: str) -> None:
    parsed = urlsplit(uri)
    if parsed.scheme != "tcp" or not parsed.hostname or not parsed.port:
        raise ValueError("Use tcp://HOST:PORT para Wyoming")
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("URI Wyoming inválida")


async def probe(uri: str, wav: str | None = None, timeout: float = 15, realtime: bool = True) -> dict:
    check_uri(uri)
    if timeout <= 0:
        raise ValueError("timeout deve ser positivo")
    return await asyncio.wait_for(_probe(uri, wav, timeout, realtime), timeout)


async def _probe(uri, wav, timeout, realtime):
    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Describe().event())
        event = await asyncio.wait_for(client.read_event(), timeout)
        if event is None or not Info.is_type(event.type):
            raise ValueError("Wyoming não retornou info")
        info = Info.from_event(event)
        models = sorted({m.name for program in info.wake for m in program.models if m.installed})
        result = {"available_models": models, "alexa_installed": "alexa" in models}
        if wav is None:
            return result
        if "alexa" not in models:
            raise ValueError("Alexa não anunciada; interrompido para evitar fallback silencioso para okay_nabu")
        with wave.open(str(Path(wav)), "rb") as audio:
            if (audio.getframerate(), audio.getsampwidth(), audio.getnchannels(), audio.getcomptype()) != (16000, 2, 1, "NONE"):
                raise ValueError("WAV deve ser PCM16 mono 16000 Hz sem compressão")
            if audio.getnframes() > 16000 * 120:
                raise ValueError("Use amostras de até 120 segundos")
            await client.write_event(Detect(names=["alexa"]).event())
            await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())
            started = time.monotonic()

            async def send():
                timestamp = 0
                while data := audio.readframes(1280):
                    await client.write_event(AudioChunk(audio=data, rate=16000, width=2, channels=1, timestamp=timestamp).event())
                    timestamp += len(data) / 32
                    if realtime:
                        await asyncio.sleep(len(data) / 32000)
                await client.write_event(AudioStop().event())

            async def receive():
                while True:
                    response = await client.read_event()
                    if response is None:
                        raise ValueError("Wyoming encerrou antes do resultado")
                    if Detection.is_type(response.type):
                        detection = Detection.from_event(response)
                        return {"detected": detection.name == "alexa", "name": detection.name,
                                "audio_timestamp_ms": detection.timestamp,
                                "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}
                    if NotDetected.is_type(response.type):
                        return {"detected": False, "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}

            sender = asyncio.create_task(send())
            try:
                result.update(await asyncio.wait_for(receive(), timeout))
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        result["timing_scope"] = "Tempo desta sonda até detecção; não é latência acústica nem latência total Assist"
        return result

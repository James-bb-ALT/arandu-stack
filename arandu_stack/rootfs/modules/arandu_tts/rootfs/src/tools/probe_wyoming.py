#!/usr/bin/env python3
"""Cliente diagnóstico Wyoming (stdlib): valida áudio real, rede e latência do primeiro bloco."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path


async def send_event(writer: asyncio.StreamWriter, event_type: str, data: dict | None = None):
    event = {"type": event_type}
    if data is not None:
        event["data"] = data
    writer.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()


async def read_event(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    line = await asyncio.wait_for(reader.readline(), timeout=90)
    if not line:
        raise ConnectionError("Conexão Wyoming encerrada antes do fim da resposta")
    header = json.loads(line.decode("utf-8"))
    payload_length = int(header.get("payload_length", 0))
    if not (0 <= payload_length <= 32 * 1024 * 1024):
        raise ValueError("Payload Wyoming excede limite")
    payload = await asyncio.wait_for(reader.readexactly(payload_length), timeout=90) if payload_length else b""
    return header, payload


async def probe(host: str, port: int, text: str, voice: str | None = None, out: Path | None = None):
    reader, writer = await asyncio.open_connection(host, port)
    try:
        await send_event(writer, "describe")
        header, _ = await read_event(reader)
        if header.get("type") != "info":
            raise RuntimeError(f"Servidor não respondeu info: {header}")
        catalog = header.get("data", {}).get("tts", [])
        available = [v.get("name") for program in catalog for v in program.get("voices", [])]
        print("Vozes disponíveis:", ", ".join(available) if available else "nenhuma")
    finally:
        writer.close()
        await writer.wait_closed()

    reader, writer = await asyncio.open_connection(host, port)
    try:
        data = {"text": text}
        if voice:
            data["voice"] = {"name": voice}
        start = time.perf_counter()
        await send_event(writer, "synthesize", data)
        first_chunk = None
        pcm = bytearray()
        rate, width, channels = 22050, 2, 1
        while True:
            header, payload = await read_event(reader)
            kind = header.get("type")
            if kind == "audio-start":
                audio_format = header.get("data", {})
                rate = int(audio_format["rate"])
                width = int(audio_format["width"])
                channels = int(audio_format["channels"])
            elif kind == "audio-chunk":
                if first_chunk is None:
                    first_chunk = time.perf_counter() - start
                pcm.extend(payload)
            elif kind == "audio-stop":
                break
            elif kind == "error":
                raise RuntimeError(header.get("data", {}).get("text", "Erro Wyoming"))
        total = time.perf_counter() - start
        print(f"Primeiro chunk: {first_chunk:.3f}s" if first_chunk is not None else "Nenhum chunk recebido")
        print(f"Processamento + transferência: {total:.3f}s; PCM: {len(pcm)} bytes, {rate} Hz")
        if out:
            with wave.open(str(out), "wb") as wav:
                wav.setframerate(rate)
                wav.setsampwidth(width)
                wav.setnchannels(channels)
                wav.writeframes(pcm)
            print("WAV gravado:", out)
        if not pcm:
            raise RuntimeError("Servidor encerrou síntese sem conteúdo de áudio")
    finally:
        writer.close()
        await writer.wait_closed()


def main():
    p = argparse.ArgumentParser(description="Teste end-to-end Wyoming Arandu TTS")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=10212)
    p.add_argument("--text", default="Arandu, a luz da cozinha foi acesa.")
    p.add_argument("--voice")
    p.add_argument("--out", type=Path, default=Path("probe_arandu.wav"))
    args = p.parse_args()
    asyncio.run(probe(args.host, args.port, args.text, args.voice, args.out))


if __name__ == "__main__":
    main()

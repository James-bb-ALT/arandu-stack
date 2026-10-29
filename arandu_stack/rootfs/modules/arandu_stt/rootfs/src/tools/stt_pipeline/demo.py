"""Generate small synthetic media fixtures, never training recordings."""
from __future__ import annotations

import argparse
import array
import math
from pathlib import Path
import shutil
import sys
import wave

from .common import run, write_text
from .dataset import prepare

PHRASES = ["Acenda a luz da cozinha.", "Qual é a temperatura da sala?", "Desligue o ventilador.",
           "Abra a cortina do quarto.", "Feche a porta da garagem.", "Ligue o ar-condicionado.",
           "Apague a luz do corredor.", "Aumente o volume da televisão."]


def tone(path: Path, frequency=440, duration=0.6, sample_rate=22050, channels=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array.array("h")
    for i in range(int(sample_rate * duration)):
        value = int(5000 * math.sin(2 * math.pi * frequency * i / sample_rate))
        data.extend([value] * channels)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(data.tobytes())


def fixtures(root: Path, problems=True, ffmpeg="ffmpeg") -> None:
    """Seven unequal speaker folders, six numbered scripts and one unnumbered."""
    for speaker in range(1, 8):
        directory = root / f"participante_{speaker:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        amount = min(8, 4 + speaker)
        script = [f"Roteiro do participante {speaker}", "", "Seção: comandos"]
        script.extend((f"{i:02d}{['. ', ') ', ' - '][i % 3]}" if speaker != 7 else "") + PHRASES[i - 1]
                      for i in range(1, amount + 1))
        write_text(directory / "roteiro.txt", "\n".join(script) + "\n")
        for pid in range(1, amount + 1):
            if problems and speaker == 1 and pid == 2:
                continue
            source = directory / f"{pid:02d}.wav"
            tone(source, 200 + speaker * 51 + pid * 13)
            if pid == 1 and speaker in (1, 2, 3):
                extension = ".ogg" if speaker == 1 else (".mp4" if speaker == 2 else ".m4a.mp4")
                target = directory / f"{pid:02d}{extension}"
                codec = "libvorbis" if speaker == 1 else "aac"
                run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(source), "-c:a", codec, str(target)])
                source.unlink()  # Only a generated temporary fixture, never user input.
            if problems and speaker == 2 and pid == 3:
                shutil.copyfile(source, directory / "03_copia.wav")
    if problems:
        (root / "participante_04" / "99.ogg").write_bytes(b"not a media file")
        (root / "participante_05" / "99.mp4").write_bytes(b"")
    write_text(root / "SYNTHETIC_FIXTURES.md", "Áudios sintéticos (tons), sem fala. Exclusivos para testes de infraestrutura. Não treinar com este dataset.\n")


def main():
    parser = argparse.ArgumentParser(description="Criar demonstração sintética em diretório novo; não serve para treinar ASR.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Destino deve estar vazio")
    fixtures(args.output / "input")
    result = prepare(args.output / "input", args.output / "dataset")
    print(f"Demonstração sintética: {result['valid_examples']} válidos; {result['rejected_examples']} rejeitados.")


if __name__ == "__main__":
    main()

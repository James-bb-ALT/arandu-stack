"""Uma confirmação: processo próprio vivo + WAVs novos reais, íntegros e não vazios."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

root = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    ready_path = root / "state/ready.json"
    if not ready_path.exists():
        return 2
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    if ready.get("run_id") != args.run_id:
        return 2
    try:
        process = psutil.Process(ready["pid"])
        command = " ".join(process.cmdline()).casefold()
        if not process.is_running() or str(root / "scripts/background.py").casefold() not in command or args.run_id not in command:
            return 2
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 2
    progress = json.loads((root / "state/progress.json").read_text(encoding="utf-8"))
    heartbeat = datetime.fromisoformat(progress["heartbeat_at"])
    if progress.get("run_id") != args.run_id or (datetime.now(timezone.utc) - heartbeat).total_seconds() > 120:
        return 2
    samples = ready["new_validated_samples"]
    if not any(item["category"] == "positive" for item in samples) or not any(item["category"] != "positive" for item in samples):
        raise ValueError("Prontidão exige positivo e negativo novos")
    for sample in samples:
        path = (root / sample["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Caminho fora do projeto")
        info = sf.info(path)
        audio, rate = sf.read(path, dtype="float32")
        if info.samplerate != 16000 or info.channels != 1 or info.subtype != "PCM_16" or not info.frames:
            raise ValueError("WAV de readiness inválido")
        if not np.isfinite(audio).all() or float(np.max(np.abs(audio))) < 0.001:
            raise ValueError("Áudio de readiness vazio")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample["wav_sha256"]:
            raise ValueError("Checksum de readiness divergente")
    launcher = json.loads((root / "state/launcher.json").read_text(encoding="utf-8"))
    print(json.dumps({"READY": True, "project": str(root), "pid": ready["pid"],
        "persistent_id": launcher["persistent_id"], "persistence_mode": launcher["mode"],
        "validated_total": sum(ready["counts"].values()), "validated_new_in_background": len(samples),
        "counts": ready["counts"], "audio_paths": [str(root / s["path"]) for s in samples],
        "device": ready["device"], "pronunciation_status": ready["pronunciation_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

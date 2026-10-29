from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unicodedata


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(text: str) -> str:
    """Split/metric key only: never change the stored transcription."""
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(c for c in text if not unicodedata.category(c).startswith("P")).split())


def natural_key(value: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", value))


def identifier(path: Path) -> int | None:
    # Strip every extension, including 01.m4a.mp4. Multiple numeric groups are ambiguous.
    stem = path.name.split(".", 1)[0]
    numbers = re.findall(r"\d+", stem)
    return int(numbers[0]) if len(numbers) == 1 else None


def slug(value: str) -> str:
    clean = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")[:70] or "item"
    return clean + "_" + hashlib.sha256(value.encode()).hexdigest()[:8]


def run(command: list[str], timeout: int = 120) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{command[0]}: {exc}") from exc
    if result.returncode:
        raise ValueError(f"{command[0]} ({result.returncode}): {result.stderr.strip()[-2500:]}")
    return result.stdout


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(value)
        # A Windows reader or virus scanner may briefly deny delete-sharing on
        # a file exposed through a Docker bind mount. Keep atomic replacement,
        # retry that transient error, and preserve the previous file on failure.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * 2 ** attempt)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    write_text(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("record must be an object")
            rows.append(row)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{i}: {exc}") from exc
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    write_text(path, buffer.getvalue())


def require_disjoint(input_dir: Path, output_dir: Path) -> None:
    if input_dir == output_dir or input_dir in output_dir.parents or output_dir in input_dir.parents:
        raise ValueError("Entrada e saída devem ser diretórios separados, sem ancestralidade; originais preservados.")

#!/usr/bin/env python3
"""Sincroniza o pacote Python com o diretório de build do app Home Assistant."""
from __future__ import annotations
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "arandu_tts"
DST = BASE / "addon" / "app" / "arandu_tts"
if DST.exists():
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
print("App sincronizado:", DST)

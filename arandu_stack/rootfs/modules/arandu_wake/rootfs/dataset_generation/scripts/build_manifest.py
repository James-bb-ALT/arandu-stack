"""Reconstrói JSONL a partir de commits SQLite/sidecars sem carregar áudios na RAM."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import WorkerLock
from wakegen.store import Store

if __name__ == "__main__":
    # Não concorre com o gerador. Não pausa nem interrompe um worker já ativo.
    with WorkerLock():
        store = Store()
        try:
            store.recover()
            print(store.counts())
        finally:
            store.close()

"""Entrada pythonw: independente do terminal, com erros de bootstrap persistentes."""
import os
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TQDM_DISABLE"] = "1"
# Progresso de bibliotecas não cria gigabytes de barras. Logs próprios são rotativos.
sys.stdout = open(os.devnull, "w")
sys.stderr = open(os.devnull, "w")
try:
    from wakegen.runner import main
    raise SystemExit(main())
except Exception:
    (root / "logs").mkdir(exist_ok=True)
    with (root / "logs/bootstrap-error.log").open("a", encoding="utf-8") as log:
        traceback.print_exc(file=log)
    raise

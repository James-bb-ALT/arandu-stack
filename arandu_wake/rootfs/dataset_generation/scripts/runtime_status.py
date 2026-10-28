import json
from pathlib import Path

import psutil

root = Path(__file__).resolve().parents[1]
path = root / "state/progress.json"
if not path.exists():
    print(json.dumps({"running": False, "stage": "not_started"}))
else:
    state = json.loads(path.read_text(encoding="utf-8"))
    running = False
    try:
        process = psutil.Process(state["pid"])
        running = process.is_running() and str(root / "scripts/background.py").casefold() in " ".join(process.cmdline()).casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    state.update(running=running, stop_requested=(root / "state/STOP_REQUESTED").exists())
    print(json.dumps(state, ensure_ascii=False, indent=2))

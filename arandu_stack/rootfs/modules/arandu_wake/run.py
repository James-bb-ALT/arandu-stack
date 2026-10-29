import json
import os
from pathlib import Path
import sys
options = json.loads(Path("/data/options.json").read_text())
for key in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "HA_TOKEN"):
    os.environ.pop(key, None)
os.execv(sys.executable, [sys.executable, "-m", "wyoming_openwakeword", "--uri", "tcp://0.0.0.0:10400", "--threshold", str(options.get("threshold", .5)), "--trigger-level", str(options.get("trigger_level", 1))])

"""Explicit opt-in download followed by passive service."""
import json
from pathlib import Path
import os
from arandu_llm.service import install, create_app
from aiohttp import web
options = json.loads(Path("/data/options.json").read_text())
root = Path("/data/models")
root.mkdir(parents=True, exist_ok=True)
if options.get("auto_download"):
    install(root)
for key in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "HA_TOKEN"):
    os.environ.pop(key, None)
web.run_app(create_app(root, "/usr/local/bin/llama-server", int(options.get("threads", 2))), host="0.0.0.0", port=11558, access_log=None)

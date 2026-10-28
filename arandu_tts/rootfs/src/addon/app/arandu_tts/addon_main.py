"""Entrada específica do app Home Assistant. Lê opções JSON do Supervisor."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .server import main


def read_options(path: Path = Path("/data/options.json")) -> dict:
    with path.open(encoding="utf-8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise ValueError("Configuração inválida")
    return result


def configure(options: dict) -> None:
    os.environ["ARANDU_URI"] = "tcp://0.0.0.0:10212"
    os.environ["ARANDU_VOICES_DIR"] = "/share/arandu_tts/voices"
    os.environ["ARANDU_DEFAULT_VOICE"] = str(options.get("voice", "pt_BR-faber-medium"))
    os.environ["ARANDU_AUTO_DOWNLOAD"] = "1" if options.get("auto_download", True) else "0"
    os.environ["ARANDU_PRELOAD"] = "1" if options.get("preload", True) else "0"
    os.environ["ARANDU_PIPER_THREADS"] = str(options.get("cpu_threads", 2))
    os.environ["ARANDU_FALLBACK_VOICE"] = str(options.get("fallback_voice", "pt_BR-faber-medium"))
    os.environ["ARANDU_REMOTE_FALLBACK"] = "1" if options.get("remote_fallback", True) else "0"
    os.environ["ARANDU_REMOTE_URL"] = str(options.get("remote_url", ""))
    os.environ["ARANDU_REMOTE_TOKEN"] = str(options.get("remote_token", ""))
    os.environ['ARANDU_POCKET_ENABLED'] = '1' if options.get('pocket_enabled', True) else '0'
    os.environ['ARANDU_POCKET_PRELOAD'] = '1' if options.get('pocket_preload', False) else '0'
    os.environ['ARANDU_POCKET_THREADS'] = str(options.get('pocket_threads', 2))
    os.environ['ARANDU_POCKET_FALLBACK'] = '1' if options.get('pocket_fallback', True) else '0'
    os.environ['ARANDU_WEB_ENABLED'] = '1'
    os.environ['ARANDU_WEB_HOST'] = '0.0.0.0'
    os.environ['ARANDU_WEB_PORT'] = '8099'
    os.environ['ARANDU_WEB_INGRESS_ONLY'] = '1'
    if options.get('hf_token'):
        os.environ['HF_TOKEN'] = str(options['hf_token'])
    os.environ['HF_HOME'] = '/share/arandu_tts/hf-cache'
    os.environ['XDG_CACHE_HOME'] = '/share/arandu_tts/cache'
    os.environ["ARANDU_LOG_LEVEL"] = str(options.get("log_level", "INFO"))


def run():
    configure(read_options())
    main()


if __name__ == "__main__":
    run()

"""Ponto de entrada das ferramentas locais."""

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import load_config, validate_url
from .ha import inspect
from .metrics import summarize
from .probe import probe


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arandu Wake: configuração e diagnóstico; nenhuma escrita no HA")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "doctor", "ha-check"):
        item = sub.add_parser(command)
        item.add_argument("--config", default="config/satellites.example.json")
    item = sub.add_parser("wyoming")
    item.add_argument("--uri", default="tcp://127.0.0.1:10400")
    item.add_argument("--wav")
    item.add_argument("--timeout", type=float, default=15)
    item.add_argument("--fast", action="store_true", help="Enviar WAV sem pacing; tempo deixa de representar tempo real")
    item.add_argument("--expect", choices=["detection", "no-detection"])
    item = sub.add_parser("metrics")
    item.add_argument("events", help="JSON com lista de eventos de uma interação Assist")
    args = parser.parse_args(argv)
    try:
        exit_code = 0
        if args.command in {"validate", "doctor", "ha-check"}:
            config = load_config(args.config)
            if args.command == "validate":
                output = {"valid": True, "satellites": len(config["satellites"]), "deployed": False}
            elif args.command == "ha-check":
                output = inspect(config, os.environ.get("HA_URL", config["home_assistant"]["url"]), os.environ.get("HA_TOKEN", ""))
                exit_code = 0 if output["ready"] else 2
            else:
                url = validate_url(os.environ.get("HA_URL", config["home_assistant"]["url"]))
                # Sem token: nunca trata uma página HTTP 200 como pipeline validado.
                request = urllib.request.Request(url, headers={"User-Agent": "arandu-wake/0.1"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    status = response.status
                output = {"http_status": status, "token_present": bool(os.environ.get("HA_TOKEN")),
                          "pipeline_verified": False, "hardware_tested": False}
        elif args.command == "wyoming":
            if args.expect and not args.wav:
                raise ValueError("--expect exige --wav")
            output = asyncio.run(probe(args.uri, args.wav, args.timeout, not args.fast))
            if not output["alexa_installed"]:
                exit_code = 2
            if args.expect and output.get("detected") != (args.expect == "detection"):
                exit_code = 2
        else:
            events = json.loads(Path(args.events).read_text(encoding="utf-8-sig"))
            output = summarize(events)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as error:
        # Exceções de rede podem conter URLs e cabeçalhos. Só mensagens próprias são mostradas.
        safe = str(error) if type(error) is ValueError else type(error).__name__
        print(json.dumps({"error": safe}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

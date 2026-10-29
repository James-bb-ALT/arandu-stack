"""Real neural inference over FIXTURE_TECNICA, never residential accuracy or HA effects."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "mlp"))
from test_contextual import hass_, add
from custom_components.local_nlu.contextual_catalog import build
from custom_components.local_nlu.ai_lab import local_request


async def main(args):
    import aiohttp
    hass = hass_()
    for i in range(60):
        add(hass, f"fixture_{i}", f"light.fixture_{i}", f"Fixture {i}", {})
    catalog = build(hass, hass.auth.user, {}).payload
    report = {"fixture": "FIXTURE_TECNICA; no residential or linguistic accuracy", "host": platform.processor(),
              "platform": platform.platform(), "entities": len(catalog["entities"]), "results": []}
    async with aiohttp.ClientSession() as session:
        for model in args.models:
            for text in args.text or ("Liga a luz da sala", "Acende a lua", "Está muito quente aqui"):
                result = await local_request(session, args.worker, "/infer", payload={"action": "decision", "text": text, "catalog": catalog, "model": model})
                report["results"].append({"text": text, **result})
        if args.llm:
            decision = report["results"][0]
            try:
                report["llm"] = await local_request(session, args.worker, "/infer", payload={"action": "llm", "llm_model": args.llm_model, "text": "Liga a luz da sala", "nlu": {"status": "no_match"}, "catalog": catalog, "decision": decision})
            except Exception as exc:
                report["llm_error"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "models": args.models, "llm": bool(args.llm),
                      "decisions": [{"model": r["model"], "status": r["decision_status"], "load_ms": r["load_ms"], "inference_ms": r["inference_ms"]} for r in report["results"]]}, indent=2))
    if report.get("llm_error"):
        raise SystemExit(report["llm_error"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default="http://127.0.0.1:11556")
    parser.add_argument("--models", nargs="+", default=["brazembed"])
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--llm-model", choices=["qwen", "tucano"], default="qwen")
    parser.add_argument("--text", action="append")
    parser.add_argument("--output", type=Path, default=Path("target/ai-lab-smoke.json"))
    asyncio.run(main(parser.parse_args()))

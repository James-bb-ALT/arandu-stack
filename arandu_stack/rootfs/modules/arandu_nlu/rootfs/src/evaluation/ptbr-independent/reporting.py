"""Shared JSON and Markdown report rendering for independent evaluation."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Avaliação HTTP PT-BR independente",
        "",
        f"- Freeze: `{report['freeze_id']}`",
        f"- Endpoint: `{report['endpoint']}`",
        f"- Casos pontuados: {summary['scored_cases']}",
        f"- Known gaps não pontuados: {summary['known_gap_cases']}",
        "",
        "## Classificações",
        "",
        "| Classe | Casos |",
        "| --- | ---: |",
    ]
    for name, count in sorted(summary["classifications"].items()):
        lines.append(f"| `{name}` | {count} |")
    lines.extend(["", "## Buckets", "", "| Bucket | Casos |", "| --- | ---: |"])
    for name, count in sorted(summary["buckets"].items()):
        lines.append(f"| `{name}` | {count} |")
    failures = [
        case
        for case in report["cases"]
        if case["bucket"] != "known-gap-future" and case["classification"] != "exact_pass"
    ]
    gaps = [case for case in report["cases"] if case["bucket"] == "known-gap-future"]
    lines.extend(["", "## Divergências pontuadas", ""])
    if not failures:
        lines.append("Nenhuma.")
    else:
        lines.extend(["| ID | Bucket | Classe | Detalhe |", "| --- | --- | --- | --- |"])
        for case in failures:
            detail = case["detail"].replace("|", "\\|")
            lines.append(f"| `{case['id']}` | `{case['bucket']}` | `{case['classification']}` | {detail} |")
    lines.extend(["", "## Known gaps não pontuados", ""])
    if not gaps:
        lines.append("Nenhum.")
    else:
        lines.extend(["| ID | Classe | Detalhe |", "| --- | --- | --- |"])
        for case in gaps:
            detail = case["detail"].replace("|", "\\|")
            lines.append(f"| `{case['id']}` | `{case['classification']}` | {detail} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [case for case in cases if case["bucket"] != "known-gap-future"]
    return {
        "cases": len(cases),
        "scored_cases": len(scored),
        "known_gap_cases": len(cases) - len(scored),
        "classifications": dict(sorted(Counter(case["classification"] for case in cases).items())),
        "scored_classifications": dict(sorted(Counter(case["classification"] for case in scored).items())),
        "buckets": dict(sorted(Counter(case["bucket"] for case in cases).items())),
    }

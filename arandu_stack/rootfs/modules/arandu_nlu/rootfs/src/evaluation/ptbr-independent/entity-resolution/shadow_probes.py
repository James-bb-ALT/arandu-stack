"""Offline structured-shadow probes for entity resolution (ADR-0052 Step 3 tooling).

Derives tier-separated probes from a snapshot file (default: the frozen
synthetic catalog-v1.json; gen-001 there is fixture) and replays them against
POST /v2/resolve on a locally started release binary (or --endpoint).
Expectations come from the ADR-0051 tier rules applied to the snapshot
itself; legitimate ambiguity is expected, never resolved away.

Telemetry discipline: the JSON report holds aggregate counters only (per
probe class matched/total plus the measured ambiguity rate). No registry ID,
entity ID, mention, span, or utterance-derived value is written to disk.
Probe-level mismatch details print to stdout only with --verbose (ephemeral).

No residential data, no parser changes, no execution. Never point this at a
live residential snapshot until Step 3 is product-approved.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "evaluation" / "ptbr-independent"))

from arandu_adapter import start_local  # noqa: E402

BASE = REPO / "evaluation" / "ptbr-independent" / "entity-resolution"
DEFAULT_SNAPSHOT = BASE / "catalog-v1.json"


def normalize(value: str) -> str:
    """Mirror addon/engine/src/normalize.rs over the probe oracle only."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    expanded: list[str] = []
    for character in stripped:
        if character == "%":
            expanded.append(" por cento ")
        elif character.isalnum():
            expanded.append(character.lower())
        else:
            expanded.append(" ")
    return " ".join("".join(expanded).split())


def groups(entities: list[dict], key: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for entity in entities:
        names = entity[key] if isinstance(entity[key], list) else [entity[key]]
        for name in names:
            grouped.setdefault(normalize(name), []).append(entity["registry_id"])
    return grouped


def admit(entity: dict, constraints: dict) -> bool:
    area = constraints.get("area_id")
    if area is not None and entity.get("area_id") != area:
        return False
    domain = constraints.get("domain")
    if domain is not None and entity.get("domain") != domain:
        return False
    capability = constraints.get("capability")
    return capability is None or capability in entity.get("capabilities", [])


def by_id(entities: list[dict]) -> dict[str, dict]:
    return {entity["registry_id"]: entity for entity in entities}


def load_snapshot(path: Path) -> dict:
    """Project a snapshot file onto exactly the ResolutionCatalog fields."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "catalog_id": raw["catalog_id"],
        "generation": raw["generation"],
        "areas": [
            {"area_id": area["area_id"], "names": list(area["names"])}
            for area in raw["areas"]
        ],
        "entities": [
            {
                "registry_id": entity["registry_id"],
                "entity_id": entity["entity_id"],
                "domain": entity["domain"],
                "area_id": entity.get("area_id"),
                "display_name": entity["display_name"],
                "aliases": list(entity["aliases"]),
                "capabilities": list(entity["capabilities"]),
            }
            for entity in raw["entities"]
        ],
    }


def build_probes(snapshot: dict) -> list[dict]:
    entities = snapshot["entities"]
    lookup = by_id(entities)
    id_groups = groups(entities, "entity_id")
    alias_groups = groups(entities, "aliases")
    display_groups = groups(entities, "display_name")
    probes: list[dict] = []

    for entity in entities:
        norm = normalize(entity["entity_id"])
        owners = sorted(id_groups[norm])
        probes.append(
            {
                "class": "entity-id",
                "mention": entity["entity_id"],
                "constraints": {},
                "expected": (
                    {"outcome": "ambiguous", "candidates": owners}
                    if len(owners) > 1
                    else {"outcome": "resolved", "registry_id": owners[0]}
                ),
            }
        )
    seen_aliases: set[str] = set()
    for entity in entities:
        for alias in entity["aliases"]:
            norm = normalize(alias)
            if norm in seen_aliases:
                continue
            seen_aliases.add(norm)
            owners = sorted(alias_groups[norm])
            probes.append(
                {
                    "class": "alias",
                    "mention": alias,
                    "constraints": {},
                    "expected": (
                        {"outcome": "ambiguous", "candidates": owners}
                        if len(owners) > 1
                        else {"outcome": "resolved", "registry_id": owners[0]}
                    ),
                }
            )
    seen_displays: set[str] = set()
    for entity in entities:
        norm = normalize(entity["display_name"])
        if norm in seen_displays:
            continue
        seen_displays.add(norm)
        owners = sorted(display_groups[norm])
        if len(owners) == 1:
            probes.append(
                {
                    "class": "display-unconstrained",
                    "mention": entity["display_name"],
                    "constraints": {},
                    "expected": {"outcome": "no_match"},
                }
            )
        else:
            probes.append(
                {
                    "class": "display-unconstrained",
                    "mention": entity["display_name"],
                    "constraints": {},
                    "expected": {"outcome": "ambiguous", "candidates": owners},
                }
            )
        for owner_id in owners:
            owner = lookup[owner_id]
            constraints = {}
            if owner.get("area_id") is not None:
                constraints["area_id"] = owner["area_id"]
            constraints["domain"] = owner["domain"]
            admitted = sorted(
                candidate
                for candidate in owners
                if admit(lookup[candidate], constraints)
            )
            probes.append(
                {
                    "class": "display-constrained",
                    "mention": entity["display_name"],
                    "constraints": constraints,
                    "expected": (
                        {"outcome": "resolved", "registry_id": admitted[0]}
                        if len(admitted) == 1
                        else (
                            {"outcome": "ambiguous", "candidates": admitted}
                            if admitted
                            else {"outcome": "no_match"}
                        )
                    ),
                }
            )
    probes.append(
        {
            "class": "negative",
            "mention": "zxqv_wo_projetor_zz",
            "constraints": {},
            "expected": {"outcome": "no_match"},
        }
    )
    probes.append(
        {
            "class": "negative",
            "mention": "",
            "constraints": {},
            "expected": {"outcome": "no_match"},
        }
    )
    if entities and entities[0]["aliases"]:
        probes.append(
            {
                "class": "negative",
                "mention": entities[0]["aliases"][0],
                "constraints": {"area_id": "__missing_area__"},
                "expected": {"outcome": "no_match"},
            }
        )
    probes.append(
        {
            "class": "negative",
            "mention": entities[0]["entity_id"] if entities else "x",
            "constraints": {},
            "generation": snapshot["generation"] + "-stale",
            "expected": {"outcome": "no_match"},
        }
    )
    return probes


def post(endpoint: str, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{endpoint}/v2/resolve",
        data=body,
        headers={"Content-Type": "application/json", "Host": "localhost"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--endpoint")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO / "target" / "ptbr-independent" / "er-session" / "shadow-probes.json",
    )
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    snapshot = load_snapshot(arguments.snapshot)
    probes = build_probes(snapshot)
    server = None
    try:
        endpoint = arguments.endpoint
        if arguments.binary is not None:
            server = start_local(arguments.binary.resolve())
            endpoint = server.endpoint
        matched: dict[str, list[int]] = {}
        ambiguous = 0
        for probe in probes:
            payload = {
                "text": "Sonda estruturada.",
                "catalog": snapshot,
                "generation": probe.get("generation", snapshot["generation"]),
                "mention": probe["mention"],
                "constraints": probe["constraints"],
            }
            status, raw = post(endpoint, json.dumps(payload).encode("utf-8"))
            actual = json.loads(raw)
            want = probe["expected"]
            if want["outcome"] == "resolved":
                ok = (
                    status == 200
                    and actual.get("outcome") == "resolved"
                    and actual.get("registry_id") == want["registry_id"]
                )
            elif want["outcome"] == "ambiguous":
                ok = status == 200 and actual == {
                    "outcome": "ambiguous",
                    "candidates": want["candidates"],
                }
                if ok:
                    ambiguous += 1
            else:
                ok = status == 200 and actual == {"outcome": "no_match"}
            tally = matched.setdefault(probe["class"], [0, 0])
            tally[1] += 1
            if ok:
                tally[0] += 1
            elif arguments.verbose:
                print(
                    json.dumps(
                        {
                            "class": probe["class"],
                            "mention": probe["mention"],
                            "constraints": probe["constraints"],
                            "expected": want,
                            "status": status,
                            "actual": actual,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
    finally:
        if server is not None:
            server.close()
    total = sum(t[1] for t in matched.values())
    hits = sum(t[0] for t in matched.values())
    report = {
        "schema_version": 1,
        "snapshot": str(arguments.snapshot),
        "probes": total,
        "matched": hits,
        "by_class": {
            name: {"matched": tally[0], "total": tally[1]}
            for name, tally in sorted(matched.items())
        },
        "legitimate_ambiguity_rate": ambiguous / total if total else 0.0,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    if hits != total:
        raise SystemExit(f"SHADOW PROBES FAILED: {total - hits} mismatches")


if __name__ == "__main__":
    main()

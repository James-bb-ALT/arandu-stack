"""HTTP-level entity-resolution conformance: gold corpus over /v2/resolve.

Builds ResolutionRequest payloads from the frozen corpus, validates each
against schemas/entity-resolution-request.schema.json before sending, POSTs
to /v2/resolve on a locally started release binary (or --endpoint), validates
each response against schemas/entity-resolution-response.schema.json, and
compares outcomes with gold labels. Negative transport cases assert the
400 + invalid_request envelope. Writes a session report under target/ only;
never touches baselines, freezes, or labels.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "evaluation" / "ptbr-independent"))

from arandu_adapter import start_local  # noqa: E402

BASE = REPO / "evaluation" / "ptbr-independent" / "entity-resolution"
REQUEST_SCHEMA = json.loads(
    (REPO / "schemas" / "entity-resolution-request.schema.json").read_text(encoding="utf-8")
)
RESPONSE_SCHEMA = json.loads(
    (REPO / "schemas" / "entity-resolution-response.schema.json").read_text(encoding="utf-8")
)

try:
    import jsonschema

    def check(instance: object, schema: dict) -> None:
        jsonschema.validate(instance=instance, schema=schema)

except ImportError:  # pragma: no cover
    raise SystemExit("validate_http requires the jsonschema package")


def build_catalog() -> dict:
    raw = json.loads((BASE / "catalog-v1.json").read_text(encoding="utf-8"))
    return {
        "catalog_id": raw["catalog_id"],
        "generation": raw["generation"],
        "areas": [{"area_id": a["area_id"], "names": a["names"]} for a in raw["areas"]],
        "entities": [
            {
                "registry_id": e["registry_id"],
                "entity_id": e["entity_id"],
                "domain": e["domain"],
                "area_id": e["area_id"],
                "display_name": e["display_name"],
                "aliases": e["aliases"],
                "capabilities": e["capabilities"],
            }
            for e in raw["entities"]
        ],
    }


def build_request(catalog: dict, row: dict) -> dict:
    if row.get("catalog_variant") == "duplicate_registry_id":
        catalog = dict(catalog, entities=[*catalog["entities"], catalog["entities"][0]])
    payload = {
        "text": row["text"],
        "catalog": catalog,
        "generation": row["generation"],
        "mention": row["mention"],
        "constraints": dict(row.get("constraints", {})),
    }
    if row.get("span") is not None:
        payload["span"] = list(row["span"])
    return payload


def expected(row: dict) -> dict:
    want = row["expected"]
    if want["outcome"] == "resolved":
        return {
            "outcome": "resolved",
            "registry_id": want["candidates"][0],
            "evidence": want["evidence"],
        }
    if want["outcome"] == "ambiguous":
        return {"outcome": "ambiguous", "candidates": list(want["candidates"])}
    return {"outcome": "no_match"}


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
    parser.add_argument("--output", type=Path, default=REPO / "target" / "ptbr-independent" / "er-session" / "validate-http.json")
    arguments = parser.parse_args()

    catalog = build_catalog()
    rows = [
        json.loads(line)
        for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    server = None
    try:
        endpoint = arguments.endpoint
        if arguments.binary is not None:
            server = start_local(arguments.binary.resolve())
            endpoint = server.endpoint
        results = []
        for row in rows:
            payload = build_request(catalog, row)
            check(payload, REQUEST_SCHEMA)
            status, raw = post(endpoint, json.dumps(payload).encode("utf-8"))
            actual = json.loads(raw)
            check(actual, RESPONSE_SCHEMA)
            results.append(
                {"id": row["id"], "status": status, "match": status == 200 and actual == expected(row), "actual": actual}
            )
        negatives = []
        template = build_request(catalog, rows[0])
        for name, mutate in (
            ("unknown-field", lambda p: dict(p, bogus=True)),
            ("wrong-type", lambda p: dict(p, mention=123)),
            ("invalid-json", None),
        ):
            if mutate is None:
                status, raw = post(endpoint, b"{")
            else:
                status, raw = post(endpoint, json.dumps(mutate(template)).encode("utf-8"))
            body = json.loads(raw)
            negatives.append(
                {
                    "case": name,
                    "match": status == 400 and body == {"status": "invalid_request", "version": 2},
                    "status": status,
                    "body": body,
                }
            )
    finally:
        if server is not None:
            server.close()
    mismatches = [r for r in results if not r["match"]] + [n for n in negatives if not n["match"]]
    report = {
        "schema_version": 1,
        "cases": len(results),
        "gold_match": sum(1 for r in results if r["match"]),
        "negative_match": sum(1 for n in negatives if n["match"]),
        "mismatches": mismatches,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("cases", "gold_match", "negative_match")}, sort_keys=True))
    print(f"wrote {arguments.output}")
    if mismatches:
        raise SystemExit(f"HTTP conformance FAILED: {len(mismatches)} mismatches")


if __name__ == "__main__":
    main()

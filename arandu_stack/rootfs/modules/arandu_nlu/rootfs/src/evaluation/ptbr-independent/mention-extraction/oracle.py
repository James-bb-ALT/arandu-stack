"""Independent validator for mention-extraction evidence (shapes only)."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "mention-extraction"
SNAPSHOT = json.loads((BASE / "snapshot-v1.json").read_text(encoding="utf-8"))

ALLOWED = {"extracted", "no_extraction"}
EVIDENCE = {"mention_subspan", "inherited"}
CONSTRAINT = {"area", "domain", "capability"}
UNLINKED = {"area", "domain", "capability"}
KNOWN_DOMAINS = {"binary_sensor", "fan", "light", "sensor", "switch"}
KNOWN_CAPABILITIES = {"get_state", "set_fan_percentage", "turn_off", "turn_on"}


def fail(message): raise ValueError(message)


def check_span(text: str, span, what: str) -> bytes:
    if (
        not isinstance(span, list) or len(span) != 2
        or any(not isinstance(v, int) for v in span)
    ):
        fail(f"{what}: invalid span shape")
    start, end = span
    encoded = text.encode("utf-8")
    if start < 0 or end <= start or end > len(encoded):
        fail(f"{what}: invalid span bounds")
    try:
        decoded = encoded[start:end].decode("utf-8")
    except UnicodeDecodeError:
        fail(f"{what}: span splits a character")
    return encoded


def validate():
    area_ids = {a["area_id"] for a in SNAPSHOT["areas"]}
    area_names = {n.casefold() for a in SNAPSHOT["areas"] for n in a["names"]}
    registry = [e["registry_id"] for e in SNAPSHOT["entities"]]
    if len(registry) != len(set(registry)):
        fail("duplicate snapshot registry_id")
    for entity in SNAPSHOT["entities"]:
        if entity["area_id"] not in area_ids:
            fail("snapshot area reference")
        if not entity["display_name"] or not isinstance(entity["aliases"], list):
            fail("snapshot names")
    rows = []
    for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        outcome = row["expected"]["outcome"]
        if outcome not in ALLOWED:
            fail(f"{row['id']}: invalid outcome")
        if row["snapshot_id"] != SNAPSHOT["catalog_id"]:
            fail(f"{row['id']}: snapshot mismatch")
        if row["generation"] != SNAPSHOT["generation"]:
            fail(f"{row['id']}: generation mismatch")
        if not isinstance(row["text"], str):
            fail(f"{row['id']}: text type")
        if len(row["text"].encode("utf-8")) > 2048 and outcome != "no_extraction":
            fail(f"{row['id']}: oversize text must not extract")
        if outcome == "no_extraction":
            if "segments" in row["expected"]:
                fail(f"{row['id']}: no_extraction must not publish segments")
            rows.append(row)
            continue
        segments = row["expected"]["segments"]
        if not 1 <= len(segments) <= 4:
            fail(f"{row['id']}: segment count")
        total = 0
        seen_ids: set[int] = set()
        seg_ranges: list[tuple[int, int]] = []
        for segment in segments:
            check_span(row["text"], segment["verb_span"], f"{row['id']}: verb")
            if not segment["verb_text"] or not isinstance(segment["verb_text"], str):
                fail(f"{row['id']}: verb text")
            encoded = row["text"].encode("utf-8")
            start, end = segment["verb_span"]
            if encoded[start:end].decode("utf-8") != segment["verb_text"]:
                fail(f"{row['id']}: verb span mismatch")
            if not 1 <= len(segment["mentions"]) <= 4:
                fail(f"{row['id']}: mention count")
            first = total
            for mention in segment["mentions"]:
                mid = mention["id"]
                if not isinstance(mid, int) or mid in seen_ids:
                    fail(f"{row['id']}: mention id")
                seen_ids.add(mid)
                check_span(row["text"], mention["span"], f"{row['id']}: mention {mid}")
                if not mention["text"] or len(mention["text"].encode("utf-8")) > 255:
                    fail(f"{row['id']}: mention text bound")
                if encoded[mention["span"][0]:mention["span"][1]].decode("utf-8") != mention["text"]:
                    fail(f"{row['id']}: mention span mismatch")
                for constraint in mention["constraints"]:
                    if constraint["type"] not in CONSTRAINT:
                        fail(f"{row['id']}: constraint type")
                    value, evidence = constraint["value"], constraint["evidence"]
                    if evidence["kind"] not in EVIDENCE:
                        fail(f"{row['id']}: evidence kind")
                    if constraint["type"] == "area" and value not in area_ids:
                        fail(f"{row['id']}: area admissibility")
                    if constraint["type"] == "domain" and value not in KNOWN_DOMAINS:
                        fail(f"{row['id']}: domain admissibility")
                    if constraint["type"] == "capability" and value not in KNOWN_CAPABILITIES:
                        fail(f"{row['id']}: capability admissibility")
                    if evidence["kind"] == "mention_subspan":
                        check_span(row["text"], evidence["span"], f"{row['id']}: evidence")
                        start, end = evidence["span"]
                        mstart, mend = mention["span"]
                        if not (mstart <= start and end <= mend):
                            fail(f"{row['id']}: evidence outside mention")
                for unknown in mention.get("unlinked", []):
                    if unknown["kind"] not in UNLINKED:
                        fail(f"{row['id']}: unlinked kind")
                    check_span(row["text"], unknown["span"], f"{row['id']}: unlinked")
                    start, end = unknown["span"]
                    if encoded[start:end].decode("utf-8") != unknown["text"]:
                        fail(f"{row['id']}: unlinked span mismatch")
                    if unknown["kind"] == "area" and unknown["text"].casefold() in area_names:
                        fail(f"{row['id']}: unlinked area is known")
                    if unknown["kind"] == "domain" and unknown["text"] in KNOWN_DOMAINS:
                        fail(f"{row['id']}: unlinked domain is known")
                    if unknown["kind"] == "capability" and unknown["text"] in KNOWN_CAPABILITIES:
                        fail(f"{row['id']}: unlinked capability is known")
                total += 1
            seg_ranges.append((first, total))
        if total > 16:
            fail(f"{row['id']}: total mentions")
        for segment, (first, last) in zip(segments, seg_ranges):
            for mention in segment["mentions"]:
                if "inherits" not in mention:
                    continue
                link = mention["inherits"]
                donor = link["from_mention"]
                if not isinstance(donor, int) or not (first <= donor < mention["id"]):
                    fail(f"{row['id']}: inheritance reference")
                if link["via"] != "coordination" or not link["noun"]:
                    fail(f"{row['id']}: inheritance shape")
                check_span(row["text"], link["noun_span"], f"{row['id']}: noun")
                start, end = link["noun_span"]
                encoded = row["text"].encode("utf-8")
                if encoded[start:end].decode("utf-8") != link["noun"]:
                    fail(f"{row['id']}: noun span mismatch")
                donor_mention = next(m for s in segments for m in s["mentions"] if m["id"] == donor)
                dstart, dend = donor_mention["span"]
                if not (dstart <= start and end <= dend):
                    fail(f"{row['id']}: noun outside donor")
        rows.append(row)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        fail("duplicate case id")
    required = {"kind", "ellipsis", "ambiguity", "match", "span", "operation", "evidence"}
    dimensions = {d for r in rows for d in r["dimensions"]}
    if not required.issubset(dimensions):
        fail("coverage dimensions incomplete")
    return rows


if __name__ == "__main__":
    rows = validate()
    print(json.dumps({"cases": len(rows),
                      "outcomes": {k: sum(r["expected"]["outcome"] == k for r in rows) for k in sorted(ALLOWED)}}, sort_keys=True))

"""Generate deterministic mention-extraction corpus (labels hand-authored).

The case table below is the entire semantic content: texts, segments,
mentions, area evidence, unlinked references, and inheritance links are all
written by hand before any product implementation. This script only renders
byte spans mechanically (ordered substring search, loud on ambiguity) and
emits canonical JSONL. It never interprets, ranks, or resolves.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "mention-extraction"
SNAPSHOT = json.loads((BASE / "snapshot-v1.json").read_text(encoding="utf-8"))

P = {"kind": "PROJECT_AUTHORED_SYNTHETIC", "license": "Apache-2.0",
     "annotation": "manual mention-extraction contract label before product implementation",
     "source": "ADR-0053 mention extraction", "copied_text": False}

AREA_BY_NAME = {}
for _area in SNAPSHOT["areas"]:
    for _name in _area["names"]:
        if _name.casefold() in AREA_BY_NAME:
            raise SystemExit(f"snapshot area name not unique: {_name}")
        AREA_BY_NAME[_name.casefold()] = _area["area_id"]


def _span(text: str, piece: str, cursor: int, what: str) -> tuple[tuple[int, int], int]:
    found = text.find(piece, cursor)
    if found < 0:
        raise SystemExit(f"piece not found: {what}={piece!r}")
    start = len(text[:found].encode("utf-8"))
    end = start + len(piece.encode("utf-8"))
    if text.encode("utf-8")[start:end].decode("utf-8") != piece:
        raise SystemExit(f"span decode mismatch: {what}")
    return (start, end), found + len(piece)


def _subspan(mention: str, base: int, name: str, what: str, base_text: str) -> tuple[int, int]:
    folded = mention.casefold()
    target = name.casefold()
    found = folded.find(target)
    if found < 0 or folded.find(target, found + 1) >= 0:
        raise SystemExit(f"evidence not unique in mention: {what}={name!r}")
    prefix = len(mention[:found].encode("utf-8"))
    start = base + prefix
    end = start + len(mention[found:found + len(name)].encode("utf-8"))
    if base_text.encode("utf-8")[start:end].decode("utf-8").casefold() != target:
        raise SystemExit(f"evidence decode mismatch: {what}")
    return (start, end)


def _mention(text: str, spec: dict, cursor: int, counter: int) -> tuple[dict, int]:
    (start, end), cursor = _span(text, spec["text"], cursor, "mention")
    constraints = []
    for area_name in spec.get("areas", []):
        key = area_name.casefold()
        if key not in AREA_BY_NAME:
            raise SystemExit(f"unknown fixture area: {area_name}")
        span = _subspan(spec["text"], start, area_name, "area", text)
        constraints.append({"type": "area", "value": AREA_BY_NAME[key],
                            "evidence": {"kind": "mention_subspan", "span": list(span)}})
    unlinked = []
    for name in spec.get("unlinked", []):
        if name.casefold() in AREA_BY_NAME:
            raise SystemExit(f"unlinked name is a known area: {name}")
        span = _subspan(spec["text"], start, name, "unlinked", text)
        unlinked.append({"kind": "area", "text": base_slice(text, span), "span": list(span)})
    mention: dict = {"id": counter, "text": spec["text"], "span": [start, end],
                     "constraints": constraints, "unlinked": unlinked}
    if "inherits" in spec:
        mention["inherits"] = spec["inherits"]
    return mention, cursor


def base_slice(text: str, span: tuple[int, int]) -> str:
    return text.encode("utf-8")[span[0]:span[1]].decode("utf-8")


def _segment(text: str, spec: dict, cursor: int, counter: int) -> tuple[dict, int, int]:
    (vstart, vend), cursor = _span(text, spec["verb"], cursor, "verb")
    mentions = []
    for mention_spec in spec["mentions"]:
        mention, cursor = _mention(text, mention_spec, cursor, counter)
        counter += 1
        mentions.append(mention)
    segment = {"verb_text": spec["verb"], "verb_span": [vstart, vend], "mentions": mentions}
    return segment, cursor, counter


def row(identifier: str, text: str, outcome: str, segments_spec=None, dimensions=()):
    expected: dict = {"outcome": outcome}
    if outcome == "extracted":
        cursor, counter, segments = 0, 0, []
        for segment_spec in segments_spec:
            segment, cursor, counter = _segment(text, segment_spec, cursor, counter)
            segments.append(segment)
        if counter > 16:
            raise SystemExit(f"too many mentions: {identifier}")
        expected["segments"] = segments
    value = {"schema_version": 1, "id": identifier, "split": "mention-extraction-dev",
             "snapshot_id": SNAPSHOT["catalog_id"], "generation": "gen-001",
             "text": text, "expected": expected,
             "dimensions": dict(dimensions), "provenance": P}
    return value


def M(text, areas=(), unlinked=(), inherits=None):
    spec: dict = {"text": text, "areas": list(areas), "unlinked": list(unlinked)}
    if inherits is not None:
        spec["inherits"] = inherits
    return spec


def S(verb, *mentions):
    return {"verb": verb, "mentions": list(mentions)}


def cases():
    D = lambda *pairs: tuple(pairs)
    return [
        row("mx-single-alias", "Acenda o abajur do quarto.", "extracted",
            [S("Acenda", M("abajur do quarto", areas=["quarto"]))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "subspan"))),
        row("mx-single-id", "Acenda light.luz_sala.", "extracted",
            [S("Acenda", M("light.luz_sala"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-ellipsis", "Acenda a luz da sala e do quarto.", "extracted",
            [S("Acenda", M("luz da sala", areas=["sala"]),
                          M("do quarto", areas=["quarto"], inherits={"noun": "luz", "noun_span": [9, 12], "from_mention": 0, "via": "coordination"}))],
            D(("kind", "positive"), ("ellipsis", "coordinated"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "inherited"))),
        row("mx-mixed-ops", "Acenda a luz da sala e desligue o ventilador do quarto.", "extracted",
            [S("Acenda", M("luz da sala", areas=["sala"])),
             S("desligue", M("ventilador do quarto", areas=["quarto"]))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "multi"), ("evidence", "subspan"))),
        row("mx-ellipsis-unlinked-third", "Acenda a luz da sala e do quarto e da copa.", "extracted",
            [S("Acenda", M("luz da sala", areas=["sala"]),
                          M("do quarto", areas=["quarto"], inherits={"noun": "luz", "noun_span": [9, 12], "from_mention": 0, "via": "coordination"}),
                          M("da copa", unlinked=["copa"], inherits={"noun": "luz", "noun_span": [9, 12], "from_mention": 0, "via": "coordination"}))],
            D(("kind", "negative"), ("ellipsis", "chain"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "unlinked"))),
        row("mx-unlinked-alias", "Acenda o abajur da copa.", "extracted",
            [S("Acenda", M("abajur da copa", unlinked=["copa"]))],
            D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "unlinked"))),
        row("mx-unlinked-id", "Acenda light.luz_sala da copa.", "extracted",
            [S("Acenda", M("light.luz_sala da copa", unlinked=["copa"]))],
            D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "unlinked"))),
        row("mx-ambiguous-preserved", "Acenda o abajur.", "extracted",
            [S("Acenda", M("abajur"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "preserved"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-utf8-case", "ACENDA O ABAJUR DO QUARTO.", "extracted",
            [S("ACENDA", M("ABAJUR DO QUARTO", areas=["QUARTO"]))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "case"), ("operation", "single"), ("evidence", "subspan"))),
        row("mx-utf8-diacritic", "Ligue a lâmpada da sala.", "extracted",
            [S("Ligue", M("lâmpada da sala", areas=["sala"]))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "multibyte"), ("operation", "single"), ("evidence", "subspan"))),
        row("mx-area-alias", "Apague a luz do estar.", "extracted",
            [S("Apague", M("luz do estar", areas=["estar"]))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "subspan"))),
        row("mx-no-inherit", "Apague o abajur da sala e o abajur do quarto.", "extracted",
            [S("Apague", M("abajur da sala", areas=["sala"]), M("abajur do quarto", areas=["quarto"]))],
            D(("kind", "positive"), ("ellipsis", "coordinated"), ("ambiguity", "preserved"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "subspan"))),
        row("mx-inherit-chain", "Acenda o abajur da sala e do quarto.", "extracted",
            [S("Acenda", M("abajur da sala", areas=["sala"]),
                          M("do quarto", areas=["quarto"], inherits={"noun": "abajur", "noun_span": [9, 15], "from_mention": 0, "via": "coordination"}))],
            D(("kind", "positive"), ("ellipsis", "coordinated"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "inherited"))),
        row("mx-fan-percent", "Coloque o ventilador em 50 por cento.", "extracted",
            [S("Coloque", M("ventilador"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-4segments", "Acenda a luz e apague o abajur e ligue o ventilador e desligue a luz.", "extracted",
            [S("Acenda", M("luz")), S("apague", M("abajur")), S("ligue", M("ventilador")), S("desligue", M("luz"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "multi"), ("evidence", "none"))),
        row("mx-5segments-no", "Acenda a luz e apague o abajur e ligue o ventilador e desligue a luz e acenda o abajur.", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-5clauses-no", "Acenda a luz da sala e do quarto e da copa e do estar e da sala.", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "chain"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-last-op-empty", "Acenda a luz da sala e desligue.", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-first-op-empty", "Apague e ligue a luz.", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-empty-text", "", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-control-char", "Acenda\ta luz.", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-too-long", "a" * 2049, "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-query-verb", "Qual é o estado do abajur?", "no_extraction",
            None, D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "reject"), ("evidence", "none"))),
        row("mx-unlinked-late", "Acenda a luz da sala e do banheiro.", "extracted",
            [S("Acenda", M("luz da sala", areas=["sala"]),
                          M("do banheiro", unlinked=["banheiro"], inherits={"noun": "luz", "noun_span": [9, 12], "from_mention": 0, "via": "coordination"}))],
            D(("kind", "negative"), ("ellipsis", "coordinated"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "unlinked"))),
        row("mx-emoji", "Acenda a cafeteira ☕.", "extracted",
            [S("Acenda", M("cafeteira"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "unknown"), ("span", "multibyte"), ("operation", "single"), ("evidence", "none"))),
        row("mx-ellipsis-4clauses", "Ligue a luz da sala e do quarto e do estar e da copa.", "extracted",
            [S("Ligue", M("luz da sala", areas=["sala"]),
                         M("do quarto", areas=["quarto"], inherits={"noun": "luz", "noun_span": [8, 11], "from_mention": 0, "via": "coordination"}),
                         M("do estar", areas=["estar"], inherits={"noun": "luz", "noun_span": [8, 11], "from_mention": 0, "via": "coordination"}),
                         M("da copa", unlinked=["copa"], inherits={"noun": "luz", "noun_span": [8, 11], "from_mention": 0, "via": "coordination"}))],
            D(("kind", "negative"), ("ellipsis", "chain"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "inherited"))),
        row("mx-duplicate-text", "Acenda o abajur e o abajur.", "extracted",
            [S("Acenda", M("abajur"), M("abajur"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "preserved"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-percent-para", "Defina o ventilador para 30 por cento.", "extracted",
            [S("Defina", M("ventilador"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "exact"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-unknown-display", "Acenda o projetor.", "extracted",
            [S("Acenda", M("projetor"))],
            D(("kind", "positive"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "unknown"), ("span", "ascii"), ("operation", "single"), ("evidence", "none"))),
        row("mx-unlinked-word", "Acenda a luz da garagem.", "extracted",
            [S("Acenda", M("luz da garagem", unlinked=["garagem"]))],
            D(("kind", "negative"), ("ellipsis", "none"), ("ambiguity", "none"), ("match", "unlinked"), ("span", "ascii"), ("operation", "single"), ("evidence", "unlinked"))),
    ]


def render(items):
    return "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in items)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render(cases())
    corpus = BASE / "corpus-v1.jsonl"
    if args.check:
        if not corpus.exists() or corpus.read_text(encoding="utf-8") != expected:
            raise SystemExit("mention-extraction corpus is not reproducible")
        print("mention-extraction corpus verified")
    else:
        corpus.write_text(expected, encoding="utf-8", newline="\n")
        print(f"mention-extraction corpus generated: {corpus}")


if __name__ == "__main__":
    main()

"""Generate Rust and Python PT-BR semantic indices from the shared ontology.

Usage:
    python scripts/generate_semantics.py          # generate all artifacts
    python scripts/generate_semantics.py --check    # verify freshness and invariants
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import unicodedata
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONCEPTS_PATH = ROOT / "arandu_stack" / "shared" / "semantics" / "pt_BR" / "concepts.json"
CONFLICT_REPORT_PATH = ROOT / "arandu_stack" / "shared" / "semantics" / "pt_BR" / "conflict_report.json"

PYTHON_TARGETS = [
    ROOT / "custom_components" / "local_nlu" / "generated_semantics_ptbr.py",
    ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_nlu" / "rootfs" / "src" / "custom_components" / "local_nlu" / "generated_semantics_ptbr.py",
    ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_decision" / "rootfs" / "src" / "src" / "arandu_decision" / "generated_semantics_ptbr.py",
    ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src" / "arandu_llm" / "generated_semantics_ptbr.py",
    ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_stt" / "rootfs" / "src" / "arandu_stt" / "generated_semantics_ptbr.py",
]

RUST_TARGET = (
    ROOT
    / "arandu_stack"
    / "rootfs"
    / "modules"
    / "arandu_nlu"
    / "rootfs"
    / "src"
    / "addon"
    / "engine"
    / "src"
    / "contextual"
    / "generated_semantics_ptbr.rs"
)


def normalize_ptbr(text: str) -> str:
    nfkc = unicodedata.normalize("NFKC", text or "")
    nfd = unicodedata.normalize("NFD", nfkc.casefold())
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return " ".join(re.findall(r"\w+", stripped))


def _rust_str(val: str) -> str:
    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_and_compile(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    concepts = data.get("concepts") or []
    ambiguities = data.get("ambiguities") or []
    non_equivalences = data.get("non_equivalences") or []
    asr_families = data.get("asr_families") or []
    morph_rules = data.get("morphological_rules") or []

    if not concepts:
        raise ValueError("concepts list cannot be empty")

    surface_to_concepts: dict[str, list[str]] = {}
    concept_map: dict[str, dict[str, Any]] = {}
    group_forms: dict[str, set[str]] = {}

    for entry in concepts:
        cid = entry.get("id")
        group = entry.get("group")
        canonical = entry.get("canonical")
        forms = entry.get("forms") or []
        if not cid or not group or not canonical or not forms:
            raise ValueError(f"Invalid concept entry: {entry!r}")
        if cid in concept_map:
            raise ValueError(f"Duplicate concept id: {cid}")
        norm_forms = sorted({normalize_ptbr(f) for f in forms if normalize_ptbr(f)})
        concept_map[cid] = {
            "id": cid,
            "group": group,
            "canonical": canonical,
            "forms": list(forms),
            "norm_forms": norm_forms,
        }
        gset = group_forms.setdefault(group, set())
        for nf in norm_forms:
            gset.add(nf)
            surface_to_concepts.setdefault(nf, []).append(cid)

    ambiguity_surfaces = {normalize_ptbr(a["surface"]): list(a.get("candidate_senses") or []) for a in ambiguities if a.get("surface")}

    # Verify non-equivalences never share a non-domain concept id (e.g. action/query/safety)
    non_equiv_violations: list[dict[str, str]] = []
    non_equiv_domain_notes: list[dict[str, str]] = []
    for ne in non_equivalences:
        na = normalize_ptbr(ne.get("a", ""))
        nb = normalize_ptbr(ne.get("b", ""))
        ca = set(surface_to_concepts.get(na, ()))
        cb = set(surface_to_concepts.get(nb, ()))
        shared = sorted(ca & cb)
        if shared:
            if all(cid.startswith("entity.") for cid in shared):
                non_equiv_domain_notes.append({"a": na, "b": nb, "shared_domain_concepts": ",".join(shared)})
            else:
                non_equiv_violations.append({"a": na, "b": nb, "shared_concepts": ",".join(shared)})

    if non_equiv_violations:
        raise ValueError(f"Non-equivalence violations detected: {non_equiv_violations}")

    collisions = {
        surf: sorted(set(cids))
        for surf, cids in sorted(surface_to_concepts.items())
        if len(set(cids)) > 1
    }

    asr_map: dict[str, str] = {}
    for fam in asr_families:
        forms = [normalize_ptbr(f) for f in (fam.get("forms") or []) if normalize_ptbr(f)]
        # Only safe single-token or explicit canonical mappings where last form is canonical anchor
        if len(forms) >= 2 and fam.get("category") in {"phoneme", "homophone", "segmentation", "accent"}:
            target = forms[-1]
            for variant in forms[:-1]:
                if variant and target and variant != target:
                    asr_map.setdefault(variant, target)

    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    compiled = {
        "schema_version": data.get("schema_version"),
        "language": data.get("language", "pt-BR"),
        "digest": digest,
        "concept_count": len(concept_map),
        "form_count": sum(len(v["forms"]) for v in concept_map.values()),
        "ambiguity_count": len(ambiguity_surfaces),
        "non_equivalence_count": len(non_equivalences),
        "asr_family_count": len(asr_families),
        "morphological_rule_count": len(morph_rules),
        "concepts": concept_map,
        "surface_to_concepts": {k: sorted(set(v)) for k, v in sorted(surface_to_concepts.items())},
        "ambiguities": {k: v for k, v in sorted(ambiguity_surfaces.items())},
        "non_equivalences": [
            {"a": normalize_ptbr(ne["a"]), "b": normalize_ptbr(ne["b"]), "reason": ne.get("reason", "")}
            for ne in non_equivalences
        ],
        "group_forms": {k: sorted(v) for k, v in sorted(group_forms.items())},
        "asr_variants": dict(sorted(asr_map.items())),
    }

    report = {
        "digest": digest,
        "concept_count": compiled["concept_count"],
        "surface_collision_count": len(collisions),
        "ambiguity_count": compiled["ambiguity_count"],
        "non_equivalence_count": compiled["non_equivalence_count"],
        "non_equivalence_violations": non_equiv_violations,
        "non_equivalence_domain_notes": non_equiv_domain_notes,
        "collisions": collisions,
    }
    return compiled, report


def render_python(compiled: dict[str, Any]) -> str:
    return (
        '"""AUTO-GENERATED from arandu_stack/shared/semantics/pt_BR/concepts.json. DO NOT EDIT."""\n'
        "from __future__ import annotations\n\n"
        f"SEMANTICS_DIGEST = {compiled['digest']!r}\n"
        f"CONCEPT_COUNT = {compiled['concept_count']}\n"
        f"FORM_COUNT = {compiled['form_count']}\n"
        f"AMBIGUITIES = {json.dumps(compiled['ambiguities'], ensure_ascii=False, sort_keys=True)}\n"
        f"NON_EQUIVALENCES = {json.dumps(compiled['non_equivalences'], ensure_ascii=False, sort_keys=True)}\n"
        f"GROUP_FORMS = {json.dumps(compiled['group_forms'], ensure_ascii=False, sort_keys=True)}\n"
        f"SURFACE_TO_CONCEPTS = {json.dumps(compiled['surface_to_concepts'], ensure_ascii=False, sort_keys=True)}\n"
        f"ASR_VARIANTS = {json.dumps(compiled['asr_variants'], ensure_ascii=False, sort_keys=True)}\n"
        f"CONCEPTS = {json.dumps(compiled['concepts'], ensure_ascii=False, sort_keys=True)}\n"
    )


def render_rust(compiled: dict[str, Any]) -> str:
    lines = [
        "// AUTO-GENERATED from arandu_stack/shared/semantics/pt_BR/concepts.json. DO NOT EDIT.",
        "#![allow(dead_code)]",
        "",
        f"pub const SEMANTICS_DIGEST: &str = {_rust_str(compiled['digest'])};",
        f"pub const CONCEPT_COUNT: usize = {compiled['concept_count']};",
        "",
        "pub static SURFACE_TO_CONCEPTS: &[(&str, &[&str])] = &[",
    ]
    for surf, cids in compiled["surface_to_concepts"].items():
        cids_str = ", ".join(_rust_str(c) for c in cids)
        lines.append(f"    ({_rust_str(surf)}, &[{cids_str}]),")
    lines.append("];")
    lines.append("")
    lines.append("pub static AMBIGUOUS_SURFACES: &[(&str, &[&str])] = &[")
    for surf, senses in compiled["ambiguities"].items():
        senses_str = ", ".join(_rust_str(s) for s in senses)
        lines.append(f"    ({_rust_str(surf)}, &[{senses_str}]),")
    lines.append("];")
    lines.append("")
    lines.append("pub static NON_EQUIVALENCES: &[(&str, &str)] = &[")
    for ne in compiled["non_equivalences"]:
        lines.append(f"    ({_rust_str(ne['a'])}, {_rust_str(ne['b'])}),")
    lines.append("];")
    lines.append("")
    lines.append("pub static ASR_VARIANTS: &[(&str, &str)] = &[")
    for variant, canonical in compiled["asr_variants"].items():
        lines.append(f"    ({_rust_str(variant)}, {_rust_str(canonical)}),")
    lines.append("];")
    lines.append("")
    lines.extend(
        [
            "pub fn lookup_concepts(surface_norm: &str) -> &'static [&'static str] {",
            "    if let Ok(idx) = SURFACE_TO_CONCEPTS.binary_search_by_key(&surface_norm, |(k, _)| *k) {",
            "        SURFACE_TO_CONCEPTS[idx].1",
            "    } else {",
            "        &[]",
            "    }",
            "}",
            "",
            "pub fn lookup_ambiguity(surface_norm: &str) -> &'static [&'static str] {",
            "    if let Ok(idx) = AMBIGUOUS_SURFACES.binary_search_by_key(&surface_norm, |(k, _)| *k) {",
            "        AMBIGUOUS_SURFACES[idx].1",
            "    } else {",
            "        &[]",
            "    }",
            "}",
            "",
            "pub fn lookup_asr_variant(surface_norm: &str) -> Option<&'static str> {",
            "    ASR_VARIANTS",
            "        .binary_search_by_key(&surface_norm, |(k, _)| *k)",
            "        .ok()",
            "        .map(|idx| ASR_VARIANTS[idx].1)",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify generated files match concepts.json")
    args = parser.parse_args()

    data = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
    compiled, report = validate_and_compile(data)

    py_src = render_python(compiled)
    rs_src = render_rust(compiled)
    report_src = json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    expected_files: list[tuple[pathlib.Path, str]] = [
        (CONFLICT_REPORT_PATH, report_src),
        (RUST_TARGET, rs_src),
        *((path, py_src) for path in PYTHON_TARGETS),
    ]

    if args.check:
        stale = []
        for path, expected in expected_files:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(str(path.relative_to(ROOT)))
        if stale:
            print("Stale or missing generated semantic artifacts:", stale, file=sys.stderr)
            return 1
        print(
            f"OK: {compiled['concept_count']} concepts, {compiled['form_count']} forms, "
            f"{compiled['ambiguity_count']} ambiguities, 0 non-equivalence violations."
        )
        return 0

    for path, content in expected_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(
        f"Generated {len(expected_files)} files: {compiled['concept_count']} concepts, "
        f"{compiled['form_count']} forms, {compiled['ambiguity_count']} ambiguities, "
        f"{report['surface_collision_count']} surface collisions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

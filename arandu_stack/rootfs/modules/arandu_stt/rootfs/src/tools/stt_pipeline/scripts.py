from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

NUMBERED = re.compile(r"^\s*(\d+)\s*(?:[.)]\s*|[-–—]\s+)(.+?)\s*$")
HEADER = re.compile(r"^(?:roteiro|participante|locutor|identifica[çc][ãa]o|se[çc][ãa]o|bloco|cap[íi]tulo|"
                    r"instru[çc][õo]es|t[íi]tulo|frases\s+(?:para|de)|data)\b", re.IGNORECASE)


@dataclass
class Script:
    path: Path
    phrases: dict[int, dict] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    numbered: bool = False
    ambiguous: bool = False


def parse_script(path: Path, options: dict | None = None) -> Script:
    options = options or {}
    result = Script(path)
    # Never silently replace bytes or guess a legacy encoding.
    lines = path.read_text(encoding=options.get("encoding", "utf-8-sig")).splitlines()
    include, exclude = set(options.get("include_lines", [])), set(options.get("exclude_lines", []))
    if include & exclude or any(not isinstance(n, int) or n < 1 or n > len(lines) for n in include | exclude):
        raise ValueError(f"Mapeamento de linhas inválido: {path}")
    candidates = []
    for n, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or n in exclude:
            continue
        match = NUMBERED.match(text)
        if match:
            candidates.append((n, int(match[1]), match[2], False))
            continue
        explicit_header = text.startswith("#") or HEADER.match(text) or text.endswith(":") or bool(re.fullmatch(r"[-=_*\s]+", text))
        suspected = explicit_header or text.isupper() or not text.endswith((".", "?", "!", "…", '"', "”"))
        if n in include:
            suspected = False
            explicit_header = False
        if suspected:
            result.issues.append({"line": n, "code": "header" if explicit_header else "ambiguous_line", "text": text})
        if explicit_header:
            continue
        candidates.append((n, None, text, suspected))
    result.numbered = any(pid is not None for _, pid, _, _ in candidates)
    seen = set()
    for ordinal, (line, pid, text, suspected) in enumerate(candidates, 1):
        if result.numbered and pid is None:
            result.issues.append({"line": line, "code": "mixed_numbering", "text": text})
            result.ambiguous = True
            continue
        pid = pid if pid is not None else ordinal
        if pid < 1 or pid in seen:
            result.phrases.pop(pid, None)
            result.issues.append({"line": line, "code": "duplicate_phrase_id", "text": text, "phrase_id": pid})
            result.ambiguous = True
        elif suspected:
            result.ambiguous = True
        else:
            result.phrases[pid] = {"text": text, "line": line, "phrase_id": pid}
        seen.add(pid)
    if not result.phrases:
        result.issues.append({"line": 0, "code": "empty_script", "text": ""})
    return result

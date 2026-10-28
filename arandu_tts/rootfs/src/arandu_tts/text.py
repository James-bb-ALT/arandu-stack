"""Normalização leve para PT-BR e divisão de texto de LLM em frases."""
from __future__ import annotations

import re

_CHUNK_LIMIT = 220
_END = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_VISIBLE = re.compile(r"[\wÀ-ÿ]", re.UNICODE)


def normalize_text(text: str) -> str:
    clean = " ".join(text.replace("\x00", " ").split())
    return clean if _VISIBLE.search(clean) else ""


class SentenceBuffer:
    """Segmenta texto sem deixar uma frase longa bloquear a fala inteira."""

    def __init__(self, max_chars: int = _CHUNK_LIMIT) -> None:
        self._pending = ""
        self.max_chars = max_chars

    def push(self, text: str) -> list[str]:
        self._pending += text
        output: list[str] = []
        while True:
            match = _END.search(self._pending)
            if match:
                end = match.start()
                sentence = normalize_text(self._pending[:end])
                self._pending = self._pending[match.end():]
                if sentence:
                    output.append(sentence)
                continue
            if len(self._pending) >= self.max_chars:
                at = self._pending.rfind(" ", 0, self.max_chars + 1)
                if at < self.max_chars // 2:
                    at = self.max_chars
                sentence = normalize_text(self._pending[:at])
                self._pending = self._pending[at:].lstrip()
                if sentence:
                    output.append(sentence)
                continue
            break
        return output

    def finish(self) -> str:
        final = normalize_text(self._pending)
        self._pending = ""
        return final

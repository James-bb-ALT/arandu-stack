"""Bounded transport cleanup; command semantics remain exclusively in Rust."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any


def without_wake_word(user_input: Any, options: dict, trusted_origin: bool) -> Any:
    """Strip one explicitly configured prefix; never guess a wake word."""
    words = options.get("wake_words", [])
    if type(words) is not list or len(words) > 8 or any(type(word) is not str or not 1 <= len(word) <= 64 or word != word.strip() or not all(ch.isalpha() or ch == " " for ch in word) for word in words):
        raise ValueError("wake_words")
    if not trusted_origin:
        return user_input
    source = user_input.text
    for word in sorted(words, key=len, reverse=True):
        prefix = source[:len(word)]
        if prefix.casefold() != word.casefold():
            continue
        tail = source[len(word):]
        if not tail or tail[0] not in " ,:":
            continue
        text = tail.lstrip(" ,:")
        if not text:
            return user_input
        if hasattr(user_input, "__dataclass_fields__"):
            return replace(user_input, text=text)
        return SimpleNamespace(**{key: getattr(user_input, key, None) for key in ("context", "conversation_id", "device_id", "satellite_id", "language", "agent_id", "extra_system_prompt")}, text=text)
    return user_input

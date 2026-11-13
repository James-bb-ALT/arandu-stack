"""Cross-phase dialogue invalidation coordinator scoped by user, origin, conversation, and entry."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any


@dataclass(frozen=True)
class DialogueScope:
    user_id: str
    origin_id: str
    conversation_id: str | None
    config_entry_id: str | None = None


@dataclass
class PendingRequest:
    """Bounded multi-turn slot-filling state scoped by user, origin, conversation, and catalog generation."""

    user_id: str
    origin_id: str
    conversation_id: str
    generation: str
    preserved_slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    original_text: str = ""
    candidates: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.monotonic)
    ttl_s: float = 90.0

    def is_valid(self, *, user_id: str, origin_id: str, conversation_id: str | None, generation: str, now: float | None = None) -> bool:
        if not conversation_id or self.conversation_id != conversation_id:
            return False
        if self.user_id != user_id or self.origin_id != origin_id or self.generation != generation:
            return False
        current = time.monotonic() if now is None else now
        return (current - self.created_at) <= self.ttl_s

    def merge_reply(self, reply_text: str) -> str:
        """Merge a short clarification reply with preserved context without overwriting non-empty slots."""
        clean = (reply_text or "").strip()
        if not clean:
            return self.original_text
        if self.original_text:
            return f"{self.original_text} ({clean})"
        parts = [str(self.preserved_slots[k]) for k in ("action", "target", "area") if self.preserved_slots.get(k)]
        parts.append(clean)
        return " ".join(parts)


class PendingRequestStore:
    def __init__(self) -> None:
        self._by_conv: dict[str, PendingRequest] = {}

    def put(self, req: PendingRequest) -> None:
        self._by_conv[req.conversation_id] = req

    def get_valid(self, *, user_id: str, origin_id: str, conversation_id: str | None, generation: str, now: float | None = None) -> PendingRequest | None:
        if not conversation_id:
            return None
        req = self._by_conv.get(conversation_id)
        if req is None:
            return None
        if not req.is_valid(user_id=user_id, origin_id=origin_id, conversation_id=conversation_id, generation=generation, now=now):
            self._by_conv.pop(conversation_id, None)
            return None
        return req

    def pop(self, conversation_id: str | None) -> None:
        if conversation_id:
            self._by_conv.pop(conversation_id, None)


def invalidate_operational_state(runtime: Any, scope: DialogueScope) -> None:
    """Clear pending Phase 5 / runtime operational confirmations and selections in this scope."""
    if runtime is None:
        return
    if hasattr(runtime, "invalidate_pending"):
        runtime.invalidate_pending(scope.conversation_id)
    elif hasattr(runtime, "_pending") and isinstance(scope.conversation_id, str):
        runtime._pending.pop(scope.conversation_id, None)

    contextual = getattr(runtime, "_contextual", None)
    if contextual is not None and hasattr(contextual, "invalidate_scope"):
        contextual.invalidate_scope(scope.user_id, scope.origin_id, scope.conversation_id)
    pending_store = getattr(contextual or runtime, "_pending_requests", None)
    if pending_store is not None and hasattr(pending_store, "pop"):
        pending_store.pop(scope.conversation_id)


def invalidate_announcement_state(hass: Any, scope: DialogueScope) -> None:
    """Clear pending Phase 6 announcement sessions in this scope."""
    if hass is None:
        return
    from .announcement_router import get_session_store

    store = get_session_store(hass, scope.config_entry_id)
    store.clear_scope(scope.user_id, scope.origin_id, scope.conversation_id)

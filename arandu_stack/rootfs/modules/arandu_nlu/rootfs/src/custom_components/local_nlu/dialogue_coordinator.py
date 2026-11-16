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
    origin_area: str | None = None
    origin_source: str | None = None
    permissions_fingerprint: str = ""
    question_kind: str = ""
    last_question: str = ""
    turn_count: int = 0
    max_turns: int = 3
    command: dict[str, Any] | None = None
    is_sensitive: bool = False

    def is_valid(self, *, user_id: str, origin_id: str, conversation_id: str | None, generation: str, permissions_fingerprint: str | None = None, now: float | None = None) -> bool:
        if not conversation_id or self.conversation_id != conversation_id:
            return False
        if self.user_id != user_id or self.origin_id != origin_id or self.generation != generation:
            return False
        if permissions_fingerprint is not None and self.permissions_fingerprint and self.permissions_fingerprint != permissions_fingerprint:
            return False
        current = time.monotonic() if now is None else now
        return (current - self.created_at) <= self.ttl_s

    def is_valid_for_origin(self, *, user_id: str, origin_id: str, generation: str, permissions_fingerprint: str | None = None, max_age_seconds: float = 30.0, now: float | None = None) -> bool:
        if not origin_id or origin_id == "unknown" or origin_id.startswith("anonymous"):
            return False
        if self.is_sensitive or "confirmation" in self.missing_slots:
            return False
        if self.user_id != user_id or self.origin_id != origin_id or self.generation != generation:
            return False
        if permissions_fingerprint is not None and self.permissions_fingerprint and self.permissions_fingerprint != permissions_fingerprint:
            return False
        current = time.monotonic() if now is None else now
        return (current - self.created_at) <= min(self.ttl_s, max_age_seconds)

    def merge_into_frame(self, reply_text: str) -> dict[str, Any]:
        """Fill missing typed slots from a follow-up reply without parenthetical concatenation."""
        from .progressive_contract import UnderstandingFrameV2

        clean = (reply_text or "").strip()
        slots = dict(self.preserved_slots)
        missing = list(self.missing_slots)
        if clean:
            if "area" in missing and not slots.get("area"):
                slots["area"] = clean
                missing = [s for s in missing if s != "area"]
            elif "target" in missing and not slots.get("target"):
                slots["target"] = clean
                missing = [s for s in missing if s != "target"]
            elif "action" in missing and not slots.get("action"):
                slots["action"] = clean
                missing = [s for s in missing if s != "action"]
        frame = UnderstandingFrameV2(
            intent=slots.get("intent") or (self.command or {}).get("intent"),
            action=slots.get("action"),
            domain=slots.get("domain"),
            area=slots.get("area"),
            targets=(slots["target"],) if isinstance(slots.get("target"), str) else (),
            missing_slots=tuple(missing),
            candidate_ids=tuple(self.candidates),
            turn_count=max(1, self.turn_count + 1),
            history=(self.original_text, clean) if clean else (self.original_text,),
        )
        return {"slots": slots, "missing_slots": tuple(missing), "frame": frame.as_dict()}

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


PendingDialogue = PendingRequest


class PendingRequestStore:
    def __init__(self) -> None:
        self._by_conv: dict[str, PendingRequest] = {}

    def put(self, req: PendingRequest) -> None:
        self._by_conv[req.conversation_id] = req

    def get_valid(self, *, user_id: str, origin_id: str, conversation_id: str | None, generation: str, permissions_fingerprint: str | None = None, now: float | None = None) -> PendingRequest | None:
        if not conversation_id:
            return None
        req = self._by_conv.get(conversation_id)
        if req is None:
            return None
        if not req.is_valid(user_id=user_id, origin_id=origin_id, conversation_id=conversation_id, generation=generation, permissions_fingerprint=permissions_fingerprint, now=now):
            self._by_conv.pop(conversation_id, None)
            return None
        return req

    def get_valid_for_origin(self, *, user_id: str, origin_id: str, generation: str, permissions_fingerprint: str | None = None, max_age_seconds: float = 30.0, now: float | None = None) -> PendingRequest | None:
        current = time.monotonic() if now is None else now
        matches = []
        expired_ids = []
        for conv_id, req in list(self._by_conv.items()):
            if (current - req.created_at) > req.ttl_s:
                expired_ids.append(conv_id)
                continue
            if req.is_valid_for_origin(
                user_id=user_id,
                origin_id=origin_id,
                generation=generation,
                permissions_fingerprint=permissions_fingerprint,
                max_age_seconds=max_age_seconds,
                now=current,
            ):
                matches.append(req)
        for conv_id in expired_ids:
            self._by_conv.pop(conv_id, None)
        return matches[0] if len(matches) == 1 else None

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

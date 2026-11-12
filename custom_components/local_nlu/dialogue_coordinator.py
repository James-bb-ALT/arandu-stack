"""Cross-phase dialogue invalidation coordinator scoped by user, origin, conversation, and entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DialogueScope:
    user_id: str
    origin_id: str
    conversation_id: str | None
    config_entry_id: str | None = None


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


def invalidate_announcement_state(hass: Any, scope: DialogueScope) -> None:
    """Clear pending Phase 6 announcement sessions in this scope."""
    if hass is None:
        return
    from .announcement_router import get_session_store

    store = get_session_store(hass, scope.config_entry_id)
    store.clear_scope(scope.user_id, scope.origin_id, scope.conversation_id)

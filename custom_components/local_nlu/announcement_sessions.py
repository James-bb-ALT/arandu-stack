"""Session management, authorization scope, deduplication and rate limits for announcements."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
import time
from typing import Any, Literal

_LOGGER = logging.getLogger(__name__)

MAX_SESSIONS = 64
MAX_GLOBAL_CONCURRENT = 2


@dataclass(frozen=True)
class SessionOrigin:
    user_id: str
    origin_kind: str  # "satellite" | "device" | "text"
    origin_id: str
    conversation_id: str | None


@dataclass(frozen=True)
class ClarifyMessageSession:
    kind: Literal["area", "satellite", "all"]
    destination: str
    origin: SessionOrigin
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class ConfirmationSession:
    targets: tuple[str, ...]
    message: str
    fingerprint: str
    origin: SessionOrigin
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class ReplySession:
    recipient_origin: str  # satellite or area where announcement was received
    reply_destination: str  # sender satellite entity or area
    created_at: float
    expires_at: float


class GlobalConcurrencyGate:
    """Global concurrency gate shared across all config entries on a Home Assistant instance."""

    def __init__(self, max_concurrent: int = MAX_GLOBAL_CONCURRENT) -> None:
        self._max_concurrent = max_concurrent
        self._active_calls = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active_calls >= self._max_concurrent:
                return False
            self._active_calls += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active_calls > 0:
                self._active_calls -= 1


class AnnouncementSessionStore:
    """In-memory bounded session store scoped by user, origin, and entry."""

    def __init__(self, gate: GlobalConcurrencyGate | None = None) -> None:
        self._sessions: dict[str, ClarifyMessageSession | ConfirmationSession] = {}
        self._reply_sessions: dict[str, ReplySession] = {}
        self._cooldowns: dict[str, float] = {}
        self._dedup_cache: dict[str, float] = {}
        self._gate = gate or GlobalConcurrencyGate()

    def _session_key(self, origin: SessionOrigin) -> str:
        return f"{origin.user_id}:{origin.origin_kind}:{origin.origin_id}:{origin.conversation_id or ''}"

    def clear(self) -> None:
        """Clear all active sessions, reply windows and caches on configuration reload."""
        self._sessions.clear()
        self._reply_sessions.clear()
        self._dedup_cache.clear()
        self._cooldowns.clear()

    def clean_expired(self) -> None:
        now = time.monotonic()
        # Clean sessions
        expired_sessions = [k for k, v in self._sessions.items() if v.expires_at < now]
        for k in expired_sessions:
            self._sessions.pop(k, None)

        # Clean replies
        expired_replies = [k for k, v in self._reply_sessions.items() if v.expires_at < now]
        for k in expired_replies:
            self._reply_sessions.pop(k, None)

        # Clean cooldowns older than 60s
        expired_cooldowns = [k for k, v in self._cooldowns.items() if v + 60 < now]
        for k in expired_cooldowns:
            self._cooldowns.pop(k, None)

        # Clean dedup cache
        expired_dedup = [k for k, exp in self._dedup_cache.items() if exp < now]
        for k in expired_dedup:
            self._dedup_cache.pop(k, None)

    def get_session(self, origin: SessionOrigin) -> ClarifyMessageSession | ConfirmationSession | None:
        self.clean_expired()
        key = self._session_key(origin)
        return self._sessions.get(key)

    def pop_session(self, origin: SessionOrigin) -> ClarifyMessageSession | ConfirmationSession | None:
        self.clean_expired()
        key = self._session_key(origin)
        return self._sessions.pop(key, None)

    def clear_scope(self, user_id: str, origin_id: str, conversation_id: str | None) -> int:
        """Invalidate all announcement sessions matching the exact scope."""
        self.clean_expired()
        conv = conversation_id or ""
        to_remove = [
            k
            for k, sess in self._sessions.items()
            if sess.origin.user_id == user_id
            and sess.origin.origin_id == origin_id
            and (sess.origin.conversation_id or "") == conv
        ]
        for k in to_remove:
            self._sessions.pop(k, None)
        return len(to_remove)

    def set_clarify_session(
        self,
        origin: SessionOrigin,
        kind: Literal["area", "satellite", "all"],
        destination: str,
        ttl_s: int = 30,
    ) -> None:
        self.clean_expired()
        if len(self._sessions) >= MAX_SESSIONS:
            # Evict oldest
            oldest_key = min(self._sessions.keys(), key=lambda k: self._sessions[k].created_at)
            self._sessions.pop(oldest_key, None)

        now = time.monotonic()
        key = self._session_key(origin)
        self._sessions[key] = ClarifyMessageSession(
            kind=kind,
            destination=destination,
            origin=origin,
            created_at=now,
            expires_at=now + ttl_s,
        )

    def set_confirmation_session(
        self,
        origin: SessionOrigin,
        targets: tuple[str, ...],
        message: str,
        fingerprint: str,
        ttl_s: int = 30,
    ) -> None:
        self.clean_expired()
        if len(self._sessions) >= MAX_SESSIONS:
            oldest_key = min(self._sessions.keys(), key=lambda k: self._sessions[k].created_at)
            self._sessions.pop(oldest_key, None)

        now = time.monotonic()
        key = self._session_key(origin)
        self._sessions[key] = ConfirmationSession(
            targets=targets,
            message=message,
            fingerprint=fingerprint,
            origin=origin,
            created_at=now,
            expires_at=now + ttl_s,
        )

    def set_reply_window(self, recipient_origin: str, reply_destination: str, ttl_s: int = 30) -> None:
        self.clean_expired()
        now = time.monotonic()
        self._reply_sessions[recipient_origin] = ReplySession(
            recipient_origin=recipient_origin,
            reply_destination=reply_destination,
            created_at=now,
            expires_at=now + ttl_s,
        )

    def get_reply_window(self, recipient_origin: str) -> ReplySession | None:
        self.clean_expired()
        return self._reply_sessions.get(recipient_origin)

    def pop_reply_window(self, recipient_origin: str) -> ReplySession | None:
        self.clean_expired()
        return self._reply_sessions.pop(recipient_origin, None)

    def check_cooldown(self, origin_key: str, cooldown_s: int) -> bool:
        """Returns True if origin is cooling down, False if allowed."""
        now = time.monotonic()
        last = self._cooldowns.get(origin_key)
        if last is not None and (now - last) < cooldown_s:
            return True
        return False

    def update_cooldown(self, origin_key: str) -> None:
        self._cooldowns[origin_key] = time.monotonic()

    def is_duplicate(self, dedup_key: str) -> bool:
        """Returns True if key is currently in dedup cache, False otherwise."""
        now = time.monotonic()
        self.clean_expired()
        return dedup_key in self._dedup_cache and self._dedup_cache[dedup_key] > now

    def record_dedup(self, dedup_key: str, ttl_s: int = 5) -> None:
        """Record key into dedup cache with specified TTL."""
        now = time.monotonic()
        self.clean_expired()
        self._dedup_cache[dedup_key] = now + ttl_s

    def check_and_record_dedup(self, dedup_key: str, ttl_s: int = 5) -> bool:
        """Returns True if duplicate (within TTL), False if unique and records it."""
        now = time.monotonic()
        self.clean_expired()
        if dedup_key in self._dedup_cache and self._dedup_cache[dedup_key] > now:
            return True
        self._dedup_cache[dedup_key] = now + ttl_s
        return False

    async def acquire_slot(self) -> bool:
        """Acquire one of the MAX_GLOBAL_CONCURRENT delivery slots."""
        return await self._gate.acquire()

    async def release_slot(self) -> None:
        await self._gate.release()


def compute_fingerprint(
    targets: tuple[str, ...],
    message: str,
    user_id: str,
    origin_id: str,
    dest_config_hash: str,
    conversation_id: str = "",
) -> str:
    """Compute an immutable fingerprint for broadcast confirmation."""
    data = (
        f"{sorted(targets)}|{message}|{user_id}|{origin_id}|{conversation_id}|{dest_config_hash}"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

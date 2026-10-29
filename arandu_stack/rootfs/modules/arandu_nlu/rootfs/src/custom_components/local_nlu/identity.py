"""Explicit Assist request identity; never infer a user from spoken content."""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import re
from types import SimpleNamespace
from typing import Any

from .const import CONF_DEVICE_IDENTITY_BINDINGS, CONF_FALLBACK_USER_ID, CONF_VOICE_IDENTITY_BINDINGS

_LOGGER = logging.getLogger(__name__)
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SATELLITE = re.compile(r"assist_satellite\.[a-z0-9_]{1,200}\Z")
IDENTITY_OPTIONS = (CONF_FALLBACK_USER_ID, CONF_VOICE_IDENTITY_BINDINGS, CONF_DEVICE_IDENTITY_BINDINGS)


class IdentityError(Exception):
    code = "denied"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ResolvedIdentity:
    user_id: str
    user: Any
    source: str


def valid_user_id(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def validate_identity_options(options: dict) -> None:
    fallback = options.get(CONF_FALLBACK_USER_ID)
    if fallback not in (None, "") and not valid_user_id(fallback):
        raise IdentityError("invalid_identity_configuration")
    for key, pattern in ((CONF_VOICE_IDENTITY_BINDINGS, _SATELLITE), (CONF_DEVICE_IDENTITY_BINDINGS, _ID)):
        bindings = options.get(key, {})
        if type(bindings) is not dict or len(bindings) > 128:
            raise IdentityError("invalid_identity_configuration")
        if any(type(origin) is not str or pattern.fullmatch(origin) is None or not valid_user_id(user_id)
               for origin, user_id in bindings.items()):
            raise IdentityError("invalid_identity_configuration")


def identity_diagnostics(options: dict, source: str | None = None, *, binding: bool = False) -> dict:
    """Only bounded labels and booleans; never serialize identities or origins."""
    return {"last_source": source, "fallback_configured": bool(options.get(CONF_FALLBACK_USER_ID)),
            "origin_binding_configured": binding,
            "satellite_bindings_configured": bool(options.get(CONF_VOICE_IDENTITY_BINDINGS)),
            "device_bindings_configured": bool(options.get(CONF_DEVICE_IDENTITY_BINDINGS))}


async def revalidate_identity(hass: Any, identity: ResolvedIdentity) -> Any:
    """Refresh permissions for the same stable ID; never run precedence again."""
    user = await hass.auth.async_get_user(identity.user_id)
    if user is None or user.is_active is not True:
        reason = "inactive_user" if identity.source == "context" else "inactive_or_missing_configured_user"
        raise IdentityError(reason)
    if getattr(user, "id", identity.user_id) != identity.user_id:
        raise IdentityError("identity_changed")
    return user


def validate_binding_origin(hass: Any, user_input: Any, source: str) -> None:
    if source == "satellite_binding":
        from homeassistant.helpers import entity_registry
        entry = entity_registry.async_get(hass).entities.get(getattr(user_input, "satellite_id", None))
    elif source == "device_binding":
        from homeassistant.helpers import device_registry
        entry = device_registry.async_get(hass).async_get(getattr(user_input, "device_id", None))
    else:
        return
    if entry is None or getattr(entry, "disabled_by", None) is not None:
        raise IdentityError("identity_origin_unavailable")


async def resolve_request_identity(hass: Any, user_input: Any, options: dict) -> ResolvedIdentity:
    """Context > explicit satellite entity binding > device registry binding > fallback."""
    user_id = getattr(getattr(user_input, "context", None), "user_id", None)
    source = "context"
    if user_id is not None:
        # Even an invalid/inactive authenticated identity must not fall through.
        if not valid_user_id(user_id):
            raise IdentityError("invalid_context_user")
    else:
        validate_identity_options(options)
        satellite = getattr(user_input, "satellite_id", None)
        device = getattr(user_input, "device_id", None)
        satellites = options.get(CONF_VOICE_IDENTITY_BINDINGS, {})
        devices = options.get(CONF_DEVICE_IDENTITY_BINDINGS, {})
        if type(satellite) is str and satellite in satellites:
            user_id, source = satellites[satellite], "satellite_binding"
        elif type(device) is str and device in devices:
            user_id, source = devices[device], "device_binding"
        else:
            user_id, source = options.get(CONF_FALLBACK_USER_ID), "fallback"
        if user_id in (None, ""):
            raise IdentityError("missing_user")
    validate_binding_origin(hass, user_input, source)
    identity = ResolvedIdentity(user_id, None, source)
    user = await revalidate_identity(hass, identity)
    _LOGGER.debug("ARANDU request identity source=%s authenticated=%s", source, source == "context")
    return replace(identity, user=user)


def effective_input(user_input: Any, identity: ResolvedIdentity) -> Any:
    """Keep authenticated context; configured identities get a linked HA context."""
    original = getattr(user_input, "context", None)
    if identity.source == "context":
        context = original
    else:
        from homeassistant.core import Context
        context = Context(user_id=identity.user_id, parent_id=getattr(original, "id", None))
    if hasattr(user_input, "__dataclass_fields__"):
        return replace(user_input, context=context)
    return SimpleNamespace(**{key: getattr(user_input, key, None) for key in
                             ("text", "language", "conversation_id", "device_id", "satellite_id", "agent_id")}, context=context)


def request_origin(user_input: Any) -> str:
    for key in ("satellite_id", "device_id"):
        value = getattr(user_input, key, None)
        if value is not None:
            if type(value) is not str or not 1 <= len(value) <= 255 or any(ord(ch) < 32 for ch in value):
                raise IdentityError("invalid_identity_origin")
            return value
    return "text"

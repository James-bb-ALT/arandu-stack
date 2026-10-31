"""Authorize and execute passive plans inside Home Assistant."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import device_registry, entity_registry

from .catalog import (
    CatalogError,
    CatalogSnapshot,
    ErSnapshot,
    build_catalog,
    build_er_snapshot,
)
from .client import ClientError, LocalNluClient
from .const import (
    FAN_FEATURE_SET_SPEED,
    FAN_FEATURE_TURN_OFF,
    FAN_FEATURE_TURN_ON,
    MAX_NAME_BYTES,
    MAX_QUERY_RESPONSE_BYTES,
    MAX_STATE_BYTES,
    MAX_UNIT_BYTES,
    SUPPORTED_DOMAINS,
)
from .protocol import (
    MusicPlan,
    Outcome,
    PlanOperation,
    PlanV2,
    ProtocolError,
    parse_response,
    parse_v2_plan,
    parse_v2_response,
    parse_v3_plan,
)


ResultCode = Literal[
    "ambiguous",
    "denied",
    "execution_failed",
    "invalid_request",
    "missing_slot",
    "no_match",
    "query_success",
    "stale",
    "success",
    "unsupported_intent",
    "unresolved_target",
    "unavailable",
    "cancelled",
    "confirmation_required",
    "partial_failure",
]


@dataclass(frozen=True)
class StateResult:
    label: str
    state: str
    unit: str | None


@dataclass(frozen=True)
class RuntimeResult:
    code: ResultCode
    operation_count: int = 0
    states: tuple[StateResult, ...] = ()
    missing_slot: str | None = None
    provider: str | None = None
    response_text: str | None = None
    continue_conversation: bool = False
    conversation_id: str | None = None
    reason: str | None = None
    timings: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _PreparedTarget:
    area_id: str | None
    domain: str
    entity_id: str
    label: str
    registry_id: str


@dataclass(frozen=True)
class _PreparedOperation:
    action: str
    percentage: int | None
    targets: tuple[_PreparedTarget, ...]


@dataclass(frozen=True)
class _PendingDialogue:
    created: float
    kind: str
    action: str | None = None
    domain: str | None = None
    provider: str | None = None
    media_query: str | None = None
    candidates: tuple[str, ...] = ()


class _PreflightError(Exception):
    def __init__(self, code: ResultCode) -> None:
        super().__init__(code)
        self.code = code


_LOGGER = logging.getLogger(__name__)

_QUERY_PREFIXES = ("qual ", "como ", "quanto ")
_SHADOW_PROBE_LIMIT = 4
_SHADOW_UNKNOWN_MENTION = "zxqv_wo_projetor_zz"
_SHADOW_STALE_GENERATION = "gen-000"
_PENDING_TTL_SECONDS = 30.0
_MAX_PENDING_DIALOGUES = 16
SHADOW_COUNTERS = (
    "probes",
    "matched",
    "resolved",
    "ambiguous",
    "no_match",
    "errors",
)


def _route_v1_first(text: Any) -> bool:
    """Return True when v2 must not be attempted (conservative default)."""
    if type(text) is not str:
        return True
    lowered = text.casefold().strip()
    return lowered.startswith(_QUERY_PREFIXES)


def _looks_like_music(text: Any) -> bool:
    if type(text) is not str:
        return False
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "música",
            "musica",
            "spotify",
            "deezer",
            "toca",
            "toque",
            "pausa",
            "próxima",
            "proxima",
        )
    )


def _valid_snapshot_identifier(value: Any) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "_-")
            for character in value
        )
    )


def _er_allowed_row(row: Any) -> dict[str, Any]:
    """Adapt one ER snapshot row to the preflight view (validated)."""
    try:
        registry_id = row["registry_id"]
        entity_id = row["entity_id"]
        display_name = row["display_name"]
        domain = row["domain"]
        capabilities = row["capabilities"]
    except (KeyError, TypeError) as error:
        raise CatalogError("er") from error
    if (
        not _valid_snapshot_identifier(registry_id)
        or type(entity_id) is not str
        or not entity_id
        or len(entity_id.encode("utf-8")) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in entity_id)
        or type(display_name) is not str
        or not display_name
        or len(display_name.encode("utf-8")) > MAX_NAME_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in display_name)
        or domain not in SUPPORTED_DOMAINS
        or type(capabilities) is not list
        or not capabilities
        or any(
            type(capability) is not str or capability not in (
                "get_state", "set_fan_percentage", "turn_off", "turn_on",
            )
            for capability in capabilities
        )
        or any(
            not _action_supports_domain(capability, domain)
            for capability in capabilities
        )
    ):
        raise CatalogError("er")
    return {
        "actions": sorted(set(capabilities)),
        "entity_id": entity_id,
        "names": [display_name],
        "registry_id": registry_id,
    }


class LocalNluRuntime:
    """Create a request snapshot, validate the returned plan, and execute it."""

    def __init__(
        self,
        hass: Any,
        client: LocalNluClient,
        v2_enabled: Callable[[], bool] | None = None,
        shadow_enabled: Callable[[], bool] | None = None,
        contextual_enabled: Callable[[], bool] | None = None,
        contextual_options: Callable[[], dict] | None = None,
    ) -> None:
        self._hass = hass
        self._client = client
        self._v2_enabled = v2_enabled if v2_enabled is not None else (lambda: False)
        self._shadow_enabled = (
            shadow_enabled if shadow_enabled is not None else (lambda: False)
        )
        self._shadow_counters = {key: 0 for key in SHADOW_COUNTERS}
        self._pending: dict[str, _PendingDialogue] = {}
        self._contextual_enabled = contextual_enabled or (lambda: False)
        self._contextual_options = contextual_options or (lambda: {})
        self._contextual = None

    async def async_process(self, user_input: Any) -> RuntimeResult:
        routing = self._contextual_options()
        from .voice_controls import cancellation, volume
        text = getattr(user_input, 'text', None)
        if type(text) is str and routing.get('wake_words'):
            from .voice_input import without_wake_word
            try:
                text = without_wake_word(user_input, routing, True).text
            except ValueError:
                return RuntimeResult(code='invalid_request')
        control = type(text) is str and (cancellation(text) or volume(text)[0])
        if control or self._contextual_enabled() or routing.get("decision_enabled") is True or routing.get("llm_enabled") is True:
            if self._contextual is None:
                from .contextual_runtime import ContextualRuntime
                self._contextual = ContextualRuntime(self._hass, self._client, self._contextual_options)
            result = await self._contextual.process(user_input)
            if control and result.code in ('success', 'cancelled'):
                self._pending.pop(getattr(user_input, 'conversation_id', None), None)
            return result
        conversation_id = getattr(user_input, "conversation_id", None)
        if type(conversation_id) is str and conversation_id in self._pending:
            result = await self._async_continue_pending(conversation_id, user_input)
        elif self._v2_enabled() and not _route_v1_first(getattr(user_input, "text", None)):
            result = await self._async_process_v3(user_input)
        else:
            result = await self._async_process_v1(user_input)
        if self._shadow_enabled():
            await self._observe_shadow()
        return result

    async def _async_continue_pending(
        self,
        conversation_id: str,
        user_input: Any,
    ) -> RuntimeResult:
        pending = self._pending.pop(conversation_id)
        if time.monotonic() - pending.created > _PENDING_TTL_SECONDS:
            return RuntimeResult(code="stale")
        if pending.kind == "media_query":
            return await self._async_execute_music(
                MusicPlan(
                    action="play",
                    media_query=user_input.text,
                    provider=pending.provider,
                    player=None,
                    player_area=pending.media_query,
                ),
                user_input.context,
            )
        if pending.kind == "player":
            player = self._resolve_music_player_from_text(user_input.text)
            if player is None:
                return RuntimeResult(code="missing_slot", missing_slot="player")
            return await self._async_execute_music(
                MusicPlan(
                    action="play",
                    media_query=pending.media_query,
                    provider=pending.provider,
                    player=player,
                ),
                user_input.context,
            )
        if pending.kind == "target" and pending.action is not None:
            registry_id = self._resolve_device_followup(
                user_input.text, pending.domain, pending.candidates
            )
            if registry_id is None:
                return RuntimeResult(code="unresolved_target")
            return await self._execute_resolved_v3_device(
                pending.action, (registry_id,), user_input
            )
        return RuntimeResult(code="invalid_request")

    async def _async_process_v3(self, user_input: Any) -> RuntimeResult:
        try:
            initial = build_er_snapshot(self._hass)
            raw = await self._client.async_interpret_v3(
                {
                    "catalog": initial.payload,
                    "generation": initial.payload["generation"],
                    "text": user_input.text,
                }
            )
            outcome = parse_v3_plan(raw)
        except (CatalogError, ClientError, ProtocolError):
            return RuntimeResult(code="unavailable")
        except Exception:
            return RuntimeResult(code="unavailable")

        if outcome.status == "plan" and outcome.music is not None:
            return await self._async_execute_music(outcome.music, user_input.context)
        if outcome.status == "plan":
            return await self._execute_v2_operations(outcome, user_input, initial)
        if outcome.status == "ambiguous_target":
            return RuntimeResult(code="ambiguous")
        if outcome.status == "missing_slot":
            provider = outcome.music.provider if outcome.music is not None else None
            missing = outcome.music.missing_slot if outcome.music is not None else None
            conversation_id = getattr(user_input, "conversation_id", None)
            if type(conversation_id) is str and missing is not None:
                self._remember_pending(
                    conversation_id,
                    _PendingDialogue(
                        created=time.monotonic(),
                        kind=missing,
                        action=outcome.music.pending_action if outcome.music is not None else None,
                        domain=outcome.music.pending_domain if outcome.music is not None else None,
                        provider=provider,
                        media_query=outcome.music.media_query if outcome.music is not None else None,
                        candidates=outcome.music.candidates if outcome.music is not None else (),
                    ),
                )
            return RuntimeResult(
                code="missing_slot", missing_slot=missing, provider=provider
            )
        if outcome.status == "unsupported_intent":
            return RuntimeResult(code="unsupported_intent")
        if outcome.status == "unresolved_target":
            return RuntimeResult(code="unresolved_target")
        return RuntimeResult(code="invalid_request")

    async def _async_process_v1(self, user_input: Any) -> RuntimeResult:
        try:
            initial = build_catalog(self._hass)
            raw = await self._client.async_interpret(
                {
                    "catalog": initial.payload,
                    "text": user_input.text,
                    "version": 1,
                }
            )
            outcome = parse_response(raw)
        except (CatalogError, ClientError, ProtocolError):
            return RuntimeResult(code="unavailable")
        except Exception:
            return RuntimeResult(code="unavailable")
        if outcome.status != "plan":
            return RuntimeResult(code=outcome.status)

        user_id = getattr(user_input.context, "user_id", None)
        if type(user_id) is not str:
            return RuntimeResult(code="denied")
        try:
            user = await self._hass.auth.async_get_user(user_id)
        except Exception:
            return RuntimeResult(code="denied")
        if user is None or user.is_active is not True:
            return RuntimeResult(code="denied")

        try:
            current = build_catalog(self._hass)
            if current.payload != initial.payload:
                return RuntimeResult(code="stale")
            prepared = self._preflight(outcome, user, current)
        except CatalogError:
            return RuntimeResult(code="stale")
        except _PreflightError as error:
            return RuntimeResult(code=error.code)
        except Exception:
            return RuntimeResult(code="unavailable")

        if prepared[0].action == "get_state":
            try:
                states = self._query(prepared[0], user)
            except _PreflightError as error:
                return RuntimeResult(code=error.code)
            except Exception:
                return RuntimeResult(code="unavailable")
            return RuntimeResult(
                code="query_success",
                operation_count=1,
                states=states,
            )

        completed = 0
        try:
            for operation in prepared:
                await self._async_execute(
                    operation,
                    user_id,
                    user_input.context,
                )
                completed += 1
        except _PreflightError as error:
            return RuntimeResult(code=error.code, operation_count=completed)
        except Exception:
            return RuntimeResult(code="execution_failed", operation_count=completed)
        return RuntimeResult(code="success", operation_count=completed)

    async def _async_process_v2(self, user_input: Any) -> RuntimeResult:
        try:
            initial = build_er_snapshot(self._hass)
            raw = await self._client.async_interpret_v2(
                {
                    "catalog": initial.payload,
                    "generation": initial.payload["generation"],
                    "text": user_input.text,
                }
            )
            outcome = parse_v2_plan(raw)
        except (CatalogError, ClientError, ProtocolError):
            return RuntimeResult(code="unavailable")
        except Exception:
            return RuntimeResult(code="unavailable")
        if outcome.status != "plan":
            return RuntimeResult(code=outcome.status)

        return await self._execute_v2_operations(outcome, user_input, initial)

    async def _execute_v2_operations(
        self,
        outcome: Any,
        user_input: Any,
        initial: ErSnapshot,
    ) -> RuntimeResult:

        user_id = getattr(user_input.context, "user_id", None)
        if type(user_id) is not str:
            return RuntimeResult(code="denied")
        try:
            user = await self._hass.auth.async_get_user(user_id)
        except Exception:
            return RuntimeResult(code="denied")
        if user is None or user.is_active is not True:
            return RuntimeResult(code="denied")

        try:
            current = build_er_snapshot(self._hass)
            if current.payload["generation"] != initial.payload["generation"]:
                return RuntimeResult(code="stale")
            allowed = [
                _er_allowed_row(row) for row in current.payload["entities"]
            ]
            prepared = self._preflight(
                outcome, user, CatalogSnapshot(payload={"entities": allowed})
            )
        except CatalogError:
            return RuntimeResult(code="stale")
        except _PreflightError as error:
            return RuntimeResult(code=error.code)
        except Exception:
            return RuntimeResult(code="unavailable")

        completed = 0
        try:
            for operation in prepared:
                if operation.action == "get_state":
                    states = self._query(operation, user)
                    return RuntimeResult(code="query_success", operation_count=1, states=states)
                await self._async_execute(
                    operation,
                    user_id,
                    user_input.context,
                )
                completed += 1
        except _PreflightError as error:
            return RuntimeResult(code=error.code, operation_count=completed)
        except Exception:
            return RuntimeResult(code="execution_failed", operation_count=completed)
        return RuntimeResult(code="success", operation_count=completed)

    async def _execute_resolved_v3_device(
        self,
        action: str,
        targets: tuple[str, ...],
        user_input: Any,
    ) -> RuntimeResult:
        return await self._execute_v2_operations(
            PlanV2(status="plan", operations=(PlanOperation(action=action, targets=targets),)),
            user_input,
            build_er_snapshot(self._hass),
        )

    async def _async_execute_music(
        self,
        music: MusicPlan,
        context: Any,
    ) -> RuntimeResult:
        if music.player is None:
            if music.player_area is not None:
                player = self._resolve_music_player_in_area(music.player_area)
                if player is not None:
                    music = MusicPlan(
                        action=music.action,
                        media_query=music.media_query,
                        media_type=music.media_type,
                        provider=music.provider,
                        player=player,
                        queue_mode=music.queue_mode,
                        volume=music.volume,
                    )
                else:
                    return RuntimeResult(code="missing_slot", missing_slot="player")
            else:
                players = self._music_assistant_players()
                if len(players) == 1:
                    music = MusicPlan(
                        action=music.action,
                        media_query=music.media_query,
                        media_type=music.media_type,
                        provider=music.provider,
                        player=players[0],
                        queue_mode=music.queue_mode,
                        volume=music.volume,
                    )
                else:
                    return RuntimeResult(code="missing_slot", missing_slot="player")
        if not _is_music_assistant_player(self._hass, music.player):
            return RuntimeResult(code="stale")
        services = self._hass.services
        try:
            if music.action == "play":
                if music.media_query is None:
                    return RuntimeResult(code="missing_slot", missing_slot="media_query", provider=music.provider)
                if services.has_service("music_assistant", "search") is not True:
                    return RuntimeResult(code="stale")
                if services.has_service("music_assistant", "play_media") is not True:
                    return RuntimeResult(code="stale")
                search_data = {"name": music.media_query}
                if music.media_type is not None:
                    search_data["media_type"] = music.media_type
                if music.provider is not None:
                    search_data["provider"] = music.provider
                await services.async_call(
                    "music_assistant", "search", search_data,
                    blocking=True, context=context, target={},
                )
                play_data: dict[str, Any] = {
                    "media_id": music.media_query,
                    "media_type": music.media_type or "track",
                    "enqueue": music.queue_mode or "replace",
                }
                if music.provider is not None:
                    play_data["provider"] = music.provider
                await services.async_call(
                    "music_assistant", "play_media", play_data,
                    blocking=True, context=context,
                    target={"entity_id": [music.player]},
                )
                return RuntimeResult(code="success", operation_count=1)
            service = {
                "next": "media_next_track",
                "pause": "media_pause",
                "previous": "media_previous_track",
                "resume": "media_play",
                "set_volume": "volume_set",
            }[music.action]
            if services.has_service("media_player", service) is not True:
                return RuntimeResult(code="stale")
            data = (
                {"volume_level": music.volume / 100}
                if music.action == "set_volume" and music.volume is not None
                else {}
            )
            await services.async_call(
                "media_player", service, data,
                blocking=True, context=context,
                target={"entity_id": [music.player]},
            )
            return RuntimeResult(code="success", operation_count=1)
        except Exception:
            return RuntimeResult(code="execution_failed")

    def _remember_pending(self, conversation_id: str, pending: _PendingDialogue) -> None:
        if len(self._pending) >= _MAX_PENDING_DIALOGUES:
            oldest = min(self._pending, key=lambda key: self._pending[key].created)
            self._pending.pop(oldest, None)
        self._pending[conversation_id] = pending

    def _resolve_device_followup(
        self, text: str, domain: str | None, candidates: tuple[str, ...]
    ) -> str | None:
        snapshot = build_er_snapshot(self._hass).payload
        needle = _normalize_pt(text)
        rows = [
            row for row in snapshot["entities"]
            if (not candidates or row["registry_id"] in candidates)
            and (domain is None or row["domain"] == domain)
        ]
        matches = [
            row["registry_id"] for row in rows
            if _row_matches_followup(row, snapshot["areas"], needle)
        ]
        matches = sorted(set(matches))
        return matches[0] if len(matches) == 1 else None

    def _resolve_music_player_from_text(self, text: str) -> str | None:
        area_id = self._area_id_from_text(text)
        if area_id is not None:
            return self._resolve_music_player_in_area(area_id)
        needle = _normalize_pt(text)
        matches = [
            entity_id for entity_id in self._music_assistant_players()
            if needle in _normalize_pt(entity_id)
        ]
        return matches[0] if len(matches) == 1 else None

    def _resolve_music_player_in_area(self, area_id: str) -> str | None:
        registry = entity_registry.async_get(self._hass)
        devices = device_registry.async_get(self._hass)
        matches = []
        for entry in registry.entities.values():
            if (
                entry.entity_id.startswith("media_player.")
                and _effective_area_id(entry, devices) == area_id
                and _is_music_assistant_player(self._hass, entry.entity_id)
            ):
                matches.append(entry.entity_id)
        matches.sort()
        return matches[0] if len(matches) == 1 else None

    def _area_id_from_text(self, text: str) -> str | None:
        snapshot = build_er_snapshot(self._hass).payload
        needle = _normalize_pt(text)
        matches = [
            area["area_id"] for area in snapshot["areas"]
            if any(_normalize_pt(name) in needle for name in area["names"])
        ]
        matches = sorted(set(matches))
        return matches[0] if len(matches) == 1 else None

    def _music_assistant_players(self) -> tuple[str, ...]:
        registry = entity_registry.async_get(self._hass)
        return tuple(
            sorted(
                entry.entity_id for entry in registry.entities.values()
                if entry.entity_id.startswith("media_player.")
                and _is_music_assistant_player(self._hass, entry.entity_id)
            )
        )

    async def _observe_shadow(self) -> None:
        try:
            await self._run_shadow_probes()
        except Exception:
            self._shadow_counters["errors"] += 1
        _LOGGER.debug(
            "local_nlu shadow aggregates: %s", dict(self._shadow_counters)
        )

    async def _run_shadow_probes(self) -> None:
        try:
            snapshot = build_er_snapshot(self._hass)
        except CatalogError:
            self._shadow_counters["errors"] += 1
            return
        generation = snapshot.payload["generation"]
        probes: list[dict[str, Any]] = []
        for row in sorted(
            snapshot.payload["entities"], key=lambda item: item["registry_id"]
        )[:_SHADOW_PROBE_LIMIT]:
            probes.append(
                {
                    "mention": row["entity_id"],
                    "constraints": {},
                    "generation": generation,
                    "expect": ("identity", row["registry_id"]),
                }
            )
        probes.append(
            {
                "mention": _SHADOW_UNKNOWN_MENTION,
                "constraints": {},
                "generation": generation,
                "expect": ("no_match", None),
            }
        )
        probes.append(
            {"mention": "", "constraints": {}, "generation": generation,
             "expect": ("no_match", None)}
        )
        first_id = snapshot.payload["entities"][0]["entity_id"]
        probes.append(
            {
                "mention": first_id,
                "constraints": {},
                "generation": _SHADOW_STALE_GENERATION,
                "expect": ("no_match", None),
            }
        )
        for probe in probes:
            try:
                raw = await self._client.async_resolve(
                    {
                        "catalog": snapshot.payload,
                        "constraints": probe["constraints"],
                        "generation": probe["generation"],
                        "mention": probe["mention"],
                        "text": "shadow",
                    }
                )
                outcome = parse_v2_response(raw)
            except (CatalogError, ClientError, ProtocolError):
                self._shadow_counters["errors"] += 1
                continue
            kind, target = probe["expect"]
            self._shadow_counters["probes"] += 1
            self._shadow_counters[outcome.status] += 1
            if kind == "no_match":
                if outcome.status == "no_match":
                    self._shadow_counters["matched"] += 1
            elif outcome.status == "resolved" and outcome.registry_id == target:
                self._shadow_counters["matched"] += 1
            elif (
                outcome.status == "ambiguous"
                and outcome.candidates is not None
                and target in outcome.candidates
            ):
                self._shadow_counters["matched"] += 1

    def _preflight(
        self,
        outcome: Outcome,
        user: Any,
        snapshot: CatalogSnapshot,
    ) -> tuple[_PreparedOperation, ...]:
        registry = entity_registry.async_get(self._hass)
        by_id = {entry.id: entry for entry in registry.entities.values()}
        devices = device_registry.async_get(self._hass)
        allowed = {
            row["registry_id"]: row
            for row in snapshot.payload["entities"]
        }
        prepared: list[_PreparedOperation] = []
        affected: set[str] = set()
        for operation in outcome.operations:
            targets = tuple(
                self._prepare_target(
                    operation,
                    registry_id,
                    by_id,
                    allowed,
                    devices,
                    user,
                )
                for registry_id in operation.targets
            )
            if operation.action != "get_state":
                for target in targets:
                    if target.registry_id in affected:
                        raise _PreflightError("invalid_request")
                    affected.add(target.registry_id)
            prepared.append(
                _PreparedOperation(
                    action=operation.action,
                    percentage=operation.percentage,
                    targets=targets,
                )
            )
        return tuple(prepared)

    def _prepare_target(
        self,
        operation: PlanOperation,
        registry_id: str,
        by_id: dict[str, Any],
        allowed: dict[str, dict[str, Any]],
        devices: Any,
        user: Any,
    ) -> _PreparedTarget:
        entry = by_id.get(registry_id)
        admitted = allowed.get(registry_id)
        if (
            entry is None
            or admitted is None
            or admitted["entity_id"] != entry.entity_id
            or operation.action not in admitted["actions"]
        ):
            raise _PreflightError("stale")
        state = self._live_state(entry, operation.action, user)
        area_id = _effective_area_id(entry, devices)
        label = admitted["names"][0]
        return _PreparedTarget(
            area_id=area_id,
            domain=entry.entity_id.split(".", 1)[0],
            entity_id=entry.entity_id,
            label=label,
            registry_id=registry_id,
        )

    def _live_state(self, entry: Any, action: str, user: Any) -> Any:
        from homeassistant.components.homeassistant import exposed_entities

        if entry.disabled_by is not None:
            raise _PreflightError("stale")
        domain = entry.entity_id.split(".", 1)[0]
        if domain not in SUPPORTED_DOMAINS or not _action_supports_domain(
            action, domain
        ):
            raise _PreflightError("stale")
        if (
            exposed_entities.async_should_expose(
                self._hass, "conversation", entry.entity_id
            )
            is not True
        ):
            raise _PreflightError("stale")
        state = self._hass.states.get(entry.entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise _PreflightError("stale")
        policy = POLICY_READ if action == "get_state" else POLICY_CONTROL
        if user.is_admin is not True and (
            user.permissions.check_entity(entry.entity_id, policy) is not True
        ):
            raise _PreflightError("denied")
        if action in ("turn_off", "turn_on"):
            required_feature = (
                FAN_FEATURE_TURN_OFF
                if action == "turn_off"
                else FAN_FEATURE_TURN_ON
            )
            supported = state.attributes.get("supported_features", 0)
            if (
                self._hass.services.has_service(domain, action) is not True
                or (
                    domain == "fan"
                    and (
                        type(supported) is not int
                        or not supported & required_feature
                    )
                )
            ):
                raise _PreflightError("stale")
        elif action == "set_fan_percentage":
            supported = state.attributes.get("supported_features", 0)
            if (
                domain != "fan"
                or self._hass.services.has_service("fan", "set_percentage")
                is not True
                or type(supported) is not int
                or not supported & FAN_FEATURE_SET_SPEED
            ):
                raise _PreflightError("stale")
        return state

    def _revalidate(self, operation: _PreparedOperation, user: Any) -> None:
        registry = entity_registry.async_get(self._hass)
        devices = device_registry.async_get(self._hass)
        by_id = {entry.id: entry for entry in registry.entities.values()}
        for target in operation.targets:
            entry = by_id.get(target.registry_id)
            if (
                entry is None
                or entry.entity_id != target.entity_id
                or _effective_area_id(entry, devices) != target.area_id
            ):
                raise _PreflightError("stale")
            self._live_state(entry, operation.action, user)

    async def _async_execute(
        self,
        operation: _PreparedOperation,
        user_id: str,
        context: Any,
    ) -> None:
        by_domain: dict[str, list[str]] = {}
        for target in operation.targets:
            by_domain.setdefault(target.domain, []).append(target.entity_id)
        service = (
            "set_percentage"
            if operation.action == "set_fan_percentage"
            else operation.action
        )
        data = (
            {"percentage": operation.percentage}
            if operation.action == "set_fan_percentage"
            else {}
        )
        for domain in sorted(by_domain):
            try:
                user = await self._hass.auth.async_get_user(user_id)
            except Exception as error:
                raise _PreflightError("denied") from error
            if user is None or user.is_active is not True:
                raise _PreflightError("denied")
            batch = _PreparedOperation(
                action=operation.action,
                percentage=operation.percentage,
                targets=tuple(
                    target
                    for target in operation.targets
                    if target.domain == domain
                ),
            )
            self._revalidate(batch, user)
            await self._hass.services.async_call(
                domain,
                service,
                data,
                blocking=True,
                context=context,
                target={"entity_id": sorted(by_domain[domain])},
            )

    def _query(
        self,
        operation: _PreparedOperation,
        user: Any,
    ) -> tuple[StateResult, ...]:
        self._revalidate(operation, user)
        results: list[StateResult] = []
        response_bytes = 0
        for target in sorted(operation.targets, key=lambda item: item.entity_id):
            state = self._hass.states.get(target.entity_id)
            if (
                state is None
                or type(state.state) is not str
                or len(state.state.encode("utf-8")) > MAX_STATE_BYTES
            ):
                raise _PreflightError("stale")
            unit = state.attributes.get("unit_of_measurement")
            if (
                type(unit) is not str
                or not unit
                or len(unit.encode("utf-8")) > MAX_UNIT_BYTES
            ):
                unit = None
            result = StateResult(
                label=target.label,
                state=state.state,
                unit=unit,
            )
            response_bytes += (
                len(result.label.encode("utf-8"))
                + len(result.state.encode("utf-8"))
                + (
                    len(result.unit.encode("utf-8"))
                    if result.unit is not None
                    else 0
                )
                + 64
            )
            if response_bytes > MAX_QUERY_RESPONSE_BYTES:
                raise _PreflightError("stale")
            results.append(result)
        return tuple(results)


def _effective_area_id(entry: Any, devices: Any) -> str | None:
    if type(entry.area_id) is str:
        return entry.area_id
    if type(entry.device_id) is not str:
        return None
    device = devices.async_get(entry.device_id)
    return device.area_id if device is not None and type(device.area_id) is str else None


def _action_supports_domain(action: str, domain: str) -> bool:
    if action == "get_state":
        return domain in SUPPORTED_DOMAINS
    if action == "set_fan_percentage":
        return domain == "fan"
    return action in ("turn_off", "turn_on") and domain in (
        "fan",
        "light",
        "switch",
    )


def _is_music_assistant_player(hass: Any, entity_id: str) -> bool:
    if not entity_id.startswith("media_player."):
        return False
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    attrs = getattr(state, "attributes", {})
    return (
        attrs.get("mass_player_type") is not None
        or attrs.get("music_assistant_player") is True
        or attrs.get("integration") == "music_assistant"
    )


def _normalize_pt(value: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFD", value.casefold())
    asciiish = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return " ".join(asciiish.replace(".", " ").replace("_", " ").split())


def _row_matches_followup(row: dict[str, Any], areas: list[dict[str, Any]], needle: str) -> bool:
    values = [row["display_name"], *row["aliases"], row["entity_id"]]
    for area in areas:
        if row.get("area_id") == area["area_id"]:
            values.extend(area["names"])
    normalized = [_normalize_pt(value) for value in values if type(value) is str]
    return any(value and (value in needle or needle in value) for value in normalized)

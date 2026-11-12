"""Home Assistant execution boundary for the passive contextual Rust engine."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import time
from typing import Any
import uuid

from homeassistant.helpers import device_registry, entity_registry

from .capabilities import ADAPTERS, READ_ACTIONS, CapabilityError, capabilities, finite, is_music_player, parameters, sensitive
from .catalog import CatalogError, _effective_area_id
from .client import ClientError
from .contextual_catalog import CatalogCache, Snapshot, exposed, origin_area, origin_context
from .contextual_protocol import Operation, Outcome, normalize, parse
from .protocol import ProtocolError
from .request_trace import CURRENT, observe, observe_execution, TracePublisher
from .queries import QueryError, calendar_response, calendar_parameters, date_window, now, read, render
from .identity import IDENTITY_OPTIONS, IdentityError, ResolvedIdentity, effective_input, identity_diagnostics, request_origin, resolve_request_identity, revalidate_identity, validate_binding_origin
from .workload import Admission, ResourceLocks, Saturated, Circuit, bounded, observed_wait

_LOGGER = logging.getLogger(__name__)


class ExecutionError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code, self.reason = code, reason
        super().__init__(reason)


@dataclass
class Session:
    expires: float
    identity_source: str | None = None
    last_targets: tuple[str, ...] = ()
    last_area: str | None = None
    last_action: str | None = None
    pending: dict | None = None
    candidates: tuple[str, ...] = ()
    confirmation: tuple[Operation, ...] = ()
    confirmation_generation: str | None = None
    confirmation_origin: str | None = None
    confirmation_origin_context: tuple[str | None, str] | None = None
    confirmation_options: str | None = None
    response: str | None = None
    signature: str | None = None
    last_execution: float = 0.0
    history_generation: str | None = None
    history_origin: tuple[str | None, str] | None = None
    pending_generation: str | None = None
    pending_origin: str | None = None
    pending_options: str | None = None
    selection: dict | None = None
    dialogue: Any = None
    todo_confirmation: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Prepared:
    operation: Operation
    rows: tuple[dict, ...]
    calls: tuple[dict, ...] = ()
    needs_confirmation: bool = False


class ContextualRuntime:
    def __init__(self, hass: Any, client: Any, options: Any = None) -> None:
        self.hass, self.client = hass, client
        self.options = options if options is not None else lambda: {}
        self.catalog = CatalogCache(hass)
        self.phase5_supported = None if callable(getattr(client,'async_phase5_supported',None)) else True
        self.feature_lock = asyncio.Lock()
        self.sessions: dict[tuple[str, str, str], Session] = {}
        self.sent_generations: set[str] = set()
        self.session_locks = ResourceLocks()
        self.resource_locks = ResourceLocks()
        limits = dict(self.options())
        concurrent = bounded(limits,'performance_concurrency',4,1,8)
        waiting = bounded(limits,'performance_waiting',16,0,32)
        # Total requests are bounded, but neural waits never occupy NLU slots.
        self.admission = Admission(concurrent+waiting,0)
        self.nlu_admission = Admission(concurrent,waiting)
        self.model_admission = Admission(1, bounded(limits,'performance_model_waiting',4,0,8))
        self.circuits = {name: Circuit() for name in ('decision','llm')}
        self.total_timeout = bounded(limits,'performance_timeout',45,5,90)
        self._stage = ContextVar('arandu_stage', default='normalization')
        self._revision = ContextVar('arandu_options_revision', default=0)
        self.identity_source: str | None = None
        self.origin_binding_configured = False
        self.identity_options: str | None = None
        self.options_revision = 0
        self.request_revision = 0
        self.active_requests = {}
        self.cancelled_tasks = set()
        self.queued_requests = {}
        from .scheduled_delivery import ScheduledDeliveryHandler
        from .scheduled_execution import ScheduledExecutionHandler
        from .scheduled_jobs import ScheduledJobManager
        self.delivery_handler = ScheduledDeliveryHandler(hass, options)
        self.execution_handler = ScheduledExecutionHandler(hass, options, self._execute_scheduled)
        endpoint=limits.get('stack_endpoint','')
        self._scheduler_publisher=TracePublisher(hass,endpoint) if endpoint else None
        self.scheduler = ScheduledJobManager(hass, self.delivery_handler, self.execution_handler,
            self._scheduler_publisher.submit if self._scheduler_publisher else None,
            concurrency=bounded(limits,'performance_scheduler_concurrency',2,1,4),
            deadline=bounded(limits,'performance_scheduler_timeout',20,5,60),
            max_active=bounded(limits,'performance_scheduler_jobs',50,1,50))

    def close(self) -> None:
        self.options_revision += 1
        for task in set(self.active_requests) | set(self.queued_requests):
            task.cancel()
        self.catalog.close()
        self.sessions.clear()
        if hasattr(self, "scheduler"):
            self.scheduler.close()
        if self._scheduler_publisher:
            self._scheduler_publisher.close()

    async def async_close(self):
        tasks = set(self.active_requests) | set(self.queued_requests) | set(self.scheduler._tasks)
        self.close()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    @property
    def stage(self):
        return self._stage.get()

    @stage.setter
    def stage(self, value):
        self._stage.set(value)

    @property
    def request_revision(self):
        return self._revision.get()

    @request_revision.setter
    def request_revision(self, value):
        self._revision.set(value)

    def options_changed(self) -> None:
        self.options_revision += 1
        self.sessions.clear()
        self.catalog.invalidate()

    def result(self, code: str, user_input: Any, *, speech: str | None = None, count: int = 0, followup: bool = False, reason: str | None = None, timings: dict | None = None) -> Any:
        from .runtime import RuntimeResult
        from .contextual_errors import speech as failure_speech
        from .response_profile import FAILURES, short_failure_origin
        if code in FAILURES and not count and short_failure_origin(self.hass, user_input):
            # End this dialogue, including any clarification/selection retained
            # by NLU or semantic fallback. A later "sim" must not revive it.
            owner = (getattr(user_input.context, 'user_id', None), request_origin(user_input),
                     user_input.conversation_id)
            self.sessions.pop(owner, None)
            code, speech, followup = 'no_match', 'Não entendi.', False
        reason = reason or {"denied": "permission_denied", "stale": "stale", "invalid_request": "invalid_request", "no_match": "no_match", "unsupported_intent": "unsupported_intent", "partial_failure": "partial_failure"}.get(code)
        stage = getattr(self, "stage", "normalization")
        if reason in ("missing_origin_area", "unknown_area", "ambiguous_area", "ambiguous_group", "origin_area_changed"):
            stage = "area_resolution"
        elif reason in ("no_compatible_capability", "ambiguous_target", "bulk_target_unavailable", "target_limit", "target_not_found"):
            stage = "entity_resolution"
        elif reason == "contradictory_plan":
            stage = "planning"
        elif code == "no_match":
            stage = "intent_recognition"
        self.last_outcome = {"code": code, "reason": reason, "stage": stage}
        _LOGGER.debug("ARANDU contextual stage=%s code=%s reason=%s", stage, code, reason)
        speech = speech or failure_speech(code, reason)
        return RuntimeResult(code=code, operation_count=count, response_text=speech,
                             continue_conversation=followup, conversation_id=user_input.conversation_id,
                             reason=reason, timings=timings or {})

    async def diagnostics(self) -> dict:
        from pathlib import Path
        integration_version = json.loads(Path(__file__).with_name("manifest.json").read_text())["version"]
        service = await self.client.async_diagnostics()
        return {**service, "integration_version": integration_version, "protocol": 4,
                "contextual_enabled": True, "route": "/v4/interpret",
                "compatible": 4 in service["protocols"],
                "identity": self.identity_diagnostics(),
                "last_outcome": getattr(self, "last_outcome", None),
                "workload": self.admission.status(), 'nlu_queue':self.nlu_admission.status(),
                'model_queue':self.model_admission.status(), 'scheduler':self.scheduler.diagnostics()}

    def identity_diagnostics(self) -> dict:
        return identity_diagnostics(dict(self.options()), self.identity_source, binding=self.origin_binding_configured)

    async def revalidate(self, identity: ResolvedIdentity, user_input: Any, options: dict) -> Any:
        self.validate_options(options)
        validate_binding_origin(self.hass, user_input, identity.source)
        if getattr(user_input.context, "user_id", None) != identity.user_id:
            raise IdentityError("identity_changed")
        user = await revalidate_identity(self.hass, identity)
        self.validate_options(options)
        validate_binding_origin(self.hass, user_input, identity.source)
        if getattr(user_input.context, "user_id", None) != identity.user_id:
            raise IdentityError("identity_changed")
        return user

    async def process(self, user_input: Any) -> Any:
        from .voice_controls import cancellation
        from .voice_input import without_wake_word
        if type(getattr(user_input, 'text', None)) is not str or not user_input.text.strip() or not 1 <= len(user_input.text) <= 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in user_input.text):
            return self.result('invalid_request', user_input)
        if getattr(user_input, 'language', 'pt-BR') not in ('pt-BR', 'pt', 'pt-br'):
            return self.result('no_match', user_input)
        conversation_id = getattr(user_input, 'conversation_id', None)
        if conversation_id is not None and (type(conversation_id) is not str or not 1 <= len(conversation_id) <= 128):
            return self.result('invalid_request', user_input)
        if conversation_id is None:
            conversation_id = uuid.uuid4().hex
            user_input = replace(user_input, conversation_id=conversation_id) if hasattr(user_input,'__dataclass_fields__') else _input_with_id(user_input,conversation_id)
        try:
            options = self.current_options()
            if normalization_trace := CURRENT.get():
                normalization_trace.start('normalization')
            cleaned = without_wake_word(user_input, options, origin_context(self.hass, user_input, options)[1] in ('satellite', 'device'))
            if normalization_trace:
                normalization_trace.end('normalization', 'completed')
            identity = await observe('authorization', resolve_request_identity(self.hass, cleaned, options))
        except (ExecutionError, ValueError, TypeError):
            return self.result('invalid_request', user_input)
        except IdentityError:
            return self.result('denied', user_input, reason='permission_denied')
        if cancellation(cleaned.text):
            return await self.cancel_request(cleaned, options)
        owner = (identity.user_id, request_origin(effective_input(cleaned, identity)), conversation_id)
        cancelled = asyncio.Event()
        request_task = asyncio.current_task()
        self.queued_requests[request_task] = (owner, cancelled)
        # Identity/session order covers dialogue state, never unrelated conversations.
        # Resource locks are acquired only after resolution, inside execute().
        try:
            if trace := CURRENT.get():
                trace.start('queue')
            async with asyncio.timeout(self.total_timeout), self.admission.enter(), self.session_locks.hold([owner]):
                if trace:
                    trace.end('queue', 'completed')
                if cancelled.is_set():
                    from .runtime import RuntimeResult
                    return RuntimeResult(code='cancelled', response_text='', conversation_id=getattr(user_input, 'conversation_id', None))
                task = asyncio.create_task(self._process(user_input))
                self.active_requests[task] = owner
                try:
                    return await task
                except asyncio.CancelledError:
                    if task not in self.cancelled_tasks:
                        raise
                    from .runtime import RuntimeResult
                    return RuntimeResult(code='cancelled', response_text='', conversation_id=getattr(user_input, 'conversation_id', None))
                finally:
                    self.cancelled_tasks.discard(task)
                    self.active_requests.pop(task, None)
        except Saturated:
            return self.result('unavailable', user_input, reason='queue_saturated', speech='Estou ocupado. Tente novamente em instantes.')
        except TimeoutError:
            return self.result('unavailable', user_input, reason='request_timeout')
        except asyncio.CancelledError:
            if request_task not in self.cancelled_tasks:
                raise
            self.cancelled_tasks.discard(request_task)
            from .runtime import RuntimeResult
            return RuntimeResult(code='cancelled', response_text='', conversation_id=conversation_id)
        finally:
            self.queued_requests.pop(request_task, None)
            self.cancelled_tasks.discard(request_task)

    async def cancel_request(self, user_input, options):
        """Cancellation bypasses the inference queue, scoped to identity and origin."""
        from .runtime import RuntimeResult
        from .voice_controls import origin_player
        try:
            identity = await resolve_request_identity(self.hass, user_input, options)
            user_input = effective_input(user_input, identity)
            origin = request_origin(user_input)
            if self.current_options() != options:
                raise IdentityError('identity_changed')
            owner_key = (identity.user_id, origin, getattr(user_input, 'conversation_id', None))
            for queued_task, (owner, cancelled) in tuple(self.queued_requests.items()):
                if owner == owner_key:
                    cancelled.set()
                    self.cancelled_tasks.add(queued_task)
                    queued_task.cancel()
            for task, owner in tuple(self.active_requests.items()):
                if owner == owner_key:
                    self.cancelled_tasks.add(task)
                    task.cancel()
            for key in list(self.sessions):
                if key == owner_key:
                    self.sessions.pop(key)
            # Output stop must not wait behind inference on another satellite.
            # Reuse the HA executor's permission/capability checks without its queue.
            from .contextual_catalog import build
            user = await revalidate_identity(self.hass, identity)
            validate_binding_origin(self.hass, user_input, identity.source)
            snapshot = build(self.hass, user, options)
            target = origin_player(self.hass, user_input, snapshot, 'stop', options)
            action = 'stop'
            if target is None:
                target = origin_player(self.hass, user_input, snapshot, 'pause', options)
                action = 'pause'
            playback_stopped = False
            if target is not None:
                operation = Operation('satellite.stop', action, (target,), {}, evidence=('origin_device',))
                user = await revalidate_identity(self.hass, identity)
                validate_binding_origin(self.hass, user_input, identity.source)
                if self.current_options() != options:
                    raise IdentityError('identity_changed')
                fresh = build(self.hass, user, options)
                if origin_player(self.hass, user_input, fresh, action, options) != target:
                    raise ExecutionError('stale', 'origin_output_changed')
                prepared = self.prepare(operation, fresh, user, options)
                call = prepared.calls[0]
                try:
                    async with asyncio.timeout(10):
                        await observe('ha', self.hass.services.async_call(call['domain'], call['service'], call['data'],
                            blocking=True, context=user_input.context, target={'entity_id': [call['entity_id']]}))
                    playback_stopped = True
                except Exception:
                    _LOGGER.warning('ARANDU satellite output stop failed; dialogue cancelled')
            # Do not start another spoken answer after stopping playback.
            return RuntimeResult(code='cancelled', response_text='', conversation_id=getattr(user_input, 'conversation_id', None),
                                 reason='satellite_playback_stopped' if playback_stopped else 'satellite_playback_stop_unavailable')
        except (IdentityError, ExecutionError, CatalogError, CapabilityError) as exc:
            return self.result('denied', user_input, reason=getattr(exc, 'reason', 'permission_denied'))

    def current_options(self) -> dict:
        encoded = json.dumps(dict(self.options()), sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ExecutionError("invalid_request", "options_bytes")
        value=json.loads(encoded)
        if self.phase5_supported is False:value['_phase5_supported']=False
        return value

    def validate_options(self, expected: dict) -> None:
        if (task := asyncio.current_task()) is not None and task.cancelling():
            raise ExecutionError('cancelled','request_cancelled')
        if self.options_revision != self.request_revision or self.current_options() != expected:
            raise ExecutionError("stale", "options_changed")

    async def _interpret(self, payload):
        async def interpret():
            return parse(await self.client.async_interpret_v4(payload))
        async with observed_wait('nlu_queue', self.nlu_admission.enter()):
            return await observe('nlu', interpret())

    async def _process(self, user_input: Any) -> Any:
        started = time.perf_counter()
        self.stage = "normalization"
        self.identity_source = None
        self.origin_binding_configured = False
        self.request_revision = self.options_revision
        try:
            if self.phase5_supported is None:
                async with self.feature_lock:
                    if self.phase5_supported is None:
                        self.phase5_supported=await self.client.async_phase5_supported()
            options = self.current_options()
        except (ExecutionError, ClientError, TypeError, ValueError):
            return self.result("invalid_request", user_input)
        if type(getattr(user_input, "text", None)) is not str or not user_input.text or len(user_input.text.encode()) > 2048 or len(user_input.text) > 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in user_input.text):
            return self.result("invalid_request", user_input)
        if getattr(user_input, "language", "pt-BR") not in ("pt-BR", "pt", "pt-br"):
            return self.result("no_match", user_input)
        conversation_id = getattr(user_input, "conversation_id", None)
        if conversation_id is None:
            conversation_id = uuid.uuid4().hex
            user_input = replace(user_input, conversation_id=conversation_id) if hasattr(user_input, "__dataclass_fields__") else _input_with_id(user_input, conversation_id)
        if type(conversation_id) is not str or not 1 <= len(conversation_id) <= 128:
            return self.result("invalid_request", user_input)
        try:
            policy = json.dumps({key: options.get(key) for key in IDENTITY_OPTIONS}, sort_keys=True)
            if self.identity_options is not None and self.identity_options != policy:
                self.sessions.clear()
                self.catalog.invalidate()
            self.identity_options = policy
            self.stage = "authorization"
            identity = await resolve_request_identity(self.hass, user_input, options)
            self.validate_options(options)
            self.identity_source = identity.source
            self.origin_binding_configured = identity.source in ("satellite_binding", "device_binding")
            user_input = effective_input(user_input, identity)
            from .voice_input import without_wake_word
            try:
                user_input = without_wake_word(user_input, options, origin_context(self.hass, user_input, options)[1] in ("satellite", "device"))
            except ValueError:
                raise ExecutionError("invalid_request", "wake_word_configuration") from None
            user_id, user = identity.user_id, identity.user
            origin = request_origin(user_input)
            key = (user_id, origin, conversation_id)
            current_time = time.monotonic()
            expired = key in self.sessions and self.sessions[key].expires < current_time
            protected = set(self.active_requests.values())
            for stale_key in [item for item, session in self.sessions.items() if session.expires < current_time and item not in protected]:
                self.sessions.pop(stale_key)
            if key not in self.sessions:
                if len(self.sessions) >= 128:
                    oldest = next((item for item in self.sessions if item not in protected),None)
                    if oldest is None:
                        raise Saturated('session_saturated')
                    self.sessions.pop(oldest)
                self.sessions[key] = Session(current_time + _ttl(options), identity_source=identity.source)
            session = self.sessions[key]
            text = normalize(user_input.text)
            from .voice_controls import volume, select_origin_player
            is_volume, percent = volume(user_input.text)
            if is_volume:
                if percent is None:
                    return self.result('missing_slot', user_input, speech='Diga um volume de zero a dez, ou de zero a cem por cento.')
                from .contextual_catalog import build
                snapshot = build(self.hass, user, options)
                target, problem = select_origin_player(self.hass, user_input, snapshot, 'volume', options, user)
                if target is None:
                    messages = {
                        'voice_output_not_exposed': 'A saída associada não está exposta ao Assist. Exponha esse media player ao Assist nas configurações do Home Assistant.',
                        'voice_output_no_volume': 'A saída associada não oferece controle de volume no Home Assistant.',
                        'voice_output_unavailable': 'A saída de áudio associada está indisponível ou foi desativada.',
                        'voice_output_permission': 'O usuário desta voz não tem permissão para controlar a saída associada.',
                        'voice_output_missing_origin': 'Este pedido chegou sem identificar o satélite ou dispositivo de origem.',
                        'voice_output_ambiguous_binding': 'Há associações de áudio diferentes para o mesmo dispositivo. Revise as associações de saída de áudio.',
                    }
                    return self.result('unavailable', user_input, reason=problem, speech=messages.get(problem,
                        'Não encontrei uma saída de áudio única neste dispositivo. Confira a associação em Saída de áudio do satélite.'))
                session.pending, session.selection, session.confirmation, session.candidates = None, None, (), ()
                operation = Operation('satellite.volume_set', 'volume', (target,), {'value': percent}, evidence=('origin_device',))
                return await self.execute((operation,), snapshot, user_input, session, options, identity)
            independent = _independent_command(text)
            if session.identity_source != identity.source:
                self.sessions[key] = Session(current_time + _ttl(options), identity_source=identity.source)
                session = self.sessions[key]
                if not independent:
                    return self.result("stale", user_input, reason="identity_changed")
            if not independent and (session.selection or session.pending or session.confirmation) and text not in ("sim", "não", "nao", "confirmo", "pode", "pode confirmar", "isso"):
                # Rust remains the grammar authority for command families without verbs.
                # This probe has no effects and never renders a catalog or candidates.
                from .contextual_catalog import build
                probe = build(self.hass, user, options)
                await self._send(probe)
                recognized = await self._interpret({"version": 4, "generation": probe.payload["generation"], "text": user_input.text, "origin_area": origin_area(self.hass, user_input, options), "origin_area_source": origin_context(self.hass, user_input, options)[1], "last_area": None, "last_targets": [], "pending": None})
                independent = recognized.status == "plan" or bool(recognized.command and recognized.command["intent"].endswith(".implicit_on"))
            if expired and not independent:
                return self.result("stale", user_input, reason="dialogue_expired")
            if independent:
                session.selection = None
                session.pending = None
                session.candidates = ()
                session.confirmation = ()
            elif session.selection is not None:
                return await self.choose_set(text, user_input, session, identity, options)
            if text in ("cancela", "cancela isso", "nao", "nao pode", "esquece", "deixa pra la"):
                session.pending = None
                session.confirmation = ()
                return self.result("cancelled", user_input, speech="Cancelado.")
            if text in ("repete", "repita", "fala de novo"):
                return self.result("query_success", user_input, speech=session.response or "Não há uma resposta recente para repetir.")
            if text in ("sim", "confirmo", "pode confirmar", "pode", "isso"):
                if expired or not session.confirmation:
                    return self.result("stale", user_input, speech="Não há uma confirmação válida pendente.")
                operations = session.confirmation
                session.confirmation = ()
                from .contextual_catalog import build
                snapshot = build(self.hass, user, options)
                if snapshot.payload["generation"] != session.confirmation_generation:
                    raise ExecutionError("stale", "confirmation_catalog_changed")
                if origin_area(self.hass, user_input, options) != session.confirmation_origin:
                    raise ExecutionError("stale", "confirmation_origin_changed")
                if session.confirmation_origin_context is not None and origin_context(self.hass, user_input, options) != session.confirmation_origin_context:
                    raise ExecutionError("stale", "confirmation_origin_changed")
                if _options_hash(options) != session.confirmation_options:
                    raise ExecutionError("stale", "confirmation_options_changed")
                return await self.execute(operations, snapshot, user_input, session, options, identity, confirmed=True)
            if session.confirmation:
                # Any new utterance invalidates sensitive pending authorization.
                session.confirmation = ()
            if session.pending is not None:
                self.catalog.invalidate()
            snapshot = self.catalog.get(user_id, user, options)
            generation = snapshot.payload["generation"]
            if generation not in self.sent_generations:
                await self._send(snapshot)
            area = origin_area(self.hass, user_input, options)
            spatial_context = origin_context(self.hass, user_input, options)
            if session.history_generation != generation or session.history_origin != spatial_context:
                session.dialogue = None
                session.last_targets = ()
                session.last_area = None
                session.last_action = None
            session.history_generation = generation
            session.history_origin = spatial_context
            from .semantic_context import semantic_refusal
            from .dialogue_context import reserved
            from .dialogue_context import context_request
            if self.phase5_supported is False and context_request(user_input.text):
                session.dialogue=None
                return self.result('unavailable',user_input,reason='phase5_not_supported',speech='Atualize também o módulo NLU para usar o contexto da Fase 5.')
            refusal = semantic_refusal(user_input.text, snapshot.payload)
            pending = session.pending
            if pending is not None and (generation != session.pending_generation or area != session.pending_origin or _options_hash(options) != session.pending_options):
                session.pending = None
                session.candidates = ()
                raise ExecutionError("stale", "dialogue_changed")
            # New commands replace a clarification; only a short target answer fills it.
            if text.split()[0] in ("liga", "desliga", "coloca", "aumenta", "diminui", "qual", "quanto", "onde", "ativa", "abre", "fecha"):
                pending = None
            payload = {"version": 4, "generation": generation, "text": user_input.text,
                       "origin_area": area, "origin_area_source": spatial_context[1], "last_area": session.last_area,
                       "last_targets": list(session.last_targets), "pending": pending}
            prior=session.dialogue
            if self.phase5_supported and prior and prior.valid(generation,_options_hash(options)) and not session.confirmation and not pending:
                payload['antecedent']=prior.antecedent()
            _LOGGER.debug("ARANDU contextual route=/v4/interpret")
            self.stage = "intent_recognition"
            outcome = await self._interpret(payload)
            if outcome.status == "stale":
                await self._send(snapshot)
                outcome = await self._interpret(payload)
            self.validate_options(options)
            if outcome.status!='plan':session.dialogue=None
            if outcome.status!='plan' and (reserved(user_input.text) or (outcome.status=='clarification' and outcome.reason=='scene_not_unique')):
                session.dialogue=None
                session.pending=None
                session.candidates=()
                from .contextual_errors import MESSAGES
                return self.result('missing_slot' if outcome.status=='clarification' else 'unavailable',user_input,
                    speech=MESSAGES.get(outcome.reason,'Preciso de um alvo, lista, período ou antecedente único e autorizado. Repita o pedido completo.'),reason=outcome.reason)
            if refusal:
                from .typed_constraints import operational_refusal
                remaining = operational_refusal(user_input.text,snapshot.payload,outcome.operations) if outcome.status=='plan' else refusal
                from .todo_actions import literal_binding
                if literal_binding(user_input.text,outcome.operations): remaining=None
                if remaining:
                    session.dialogue=None
                    session.pending,session.selection,session.confirmation,session.candidates=None,None,(),()
                    return self.result('cancelled' if remaining=='semantic_negation' else 'unavailable',user_input,reason=remaining,
                        speech='Não executei: o pedido contém uma restrição que não consigo aplicar com segurança.')
            from .intelligence import eligible, fallback, nlu_summary
            if not reserved(user_input.text) and (options.get("decision_enabled") is True or options.get("llm_enabled") is True) and eligible(outcome, pending=pending is not None or bool(session.candidates)):
                from .ai_lab import local_request
                from homeassistant.helpers.aiohttp_client import async_get_clientsession
                endpoint = options.get("stack_endpoint", "")
                async def infer(body):
                    if not endpoint:
                        raise ValueError("Endpoint Arandu Stack ausente")
                    source = body['action']
                    timeout = 10 if source == 'decision' else 30
                    async with observed_wait('model_queue', self.model_admission.enter()):
                        async with self.circuits[source].call(), asyncio.timeout(timeout):
                            return await local_request(async_get_clientsession(self.hass), endpoint, "/infer", payload=body, timeout=timeout)
                from .contextual_protocol import legacy_neural_catalog
                model_payload=legacy_neural_catalog(snapshot.payload)
                model_snapshot=Snapshot(model_payload,{row['registry_id']:row for row in model_payload['entities']})
                proposed, trace = await fallback(user_input.text, model_snapshot, options, infer,
                    {"origin_area": area, "last_area": session.last_area, "last_targets": list(session.last_targets), "last_action":session.last_action,
                     'temperature_unit':getattr(getattr(getattr(self.hass,'config',None),'units',None),'temperature_unit',None)},
                    nlu=nlu_summary(outcome))
                self.validate_options(options)
                self.last_intelligence = trace
                if any(row.get('reason') == 'queue_saturated' for row in trace.values()):
                    raise Saturated('queue_saturated')
                reply = trace.get('llm', {}).get('response_text')
                if trace.get('llm', {}).get('status') == 'conversation' and reply:
                    await self.revalidate(identity, user_input, options)
                    session.pending = None
                    session.candidates = ()
                    session.response = reply
                    return self.result('query_success', user_input, speech=reply, reason='llm_conversation')
                if proposed:
                    if origin_context(self.hass, user_input, options) != spatial_context:
                        raise ExecutionError("stale", "origin_area_changed")
                    current_user = await self.revalidate(identity, user_input, options)
                    from .contextual_catalog import build
                    if build(self.hass, current_user, options).payload["generation"] != generation:
                        raise ExecutionError("stale", "semantic_catalog_changed")
                    self.stage = "planning"
                    if any(item.get("status") == "confirmation_required" for item in trace.values()):
                        prepared = tuple(self.prepare(op, snapshot, current_user, options) for op in proposed)
                        self.validate_plan(prepared)
                        self.validate_bulk_snapshot(proposed, snapshot, current_user, options)
                        from .semantic_context import confirmation_question
                        row = snapshot.by_id[proposed[0].targets[0]]
                        question = confirmation_question(proposed[0].action, row, snapshot.payload)
                        session.confirmation = proposed
                        session.confirmation_generation = generation
                        session.confirmation_origin = area
                        session.confirmation_origin_context = spatial_context
                        session.confirmation_options = _options_hash(options)
                        session.expires = time.monotonic() + min(30, _ttl(options))
                        session.response = question
                        return self.result("confirmation_required", user_input, speech=question, followup=True, reason="semantic_confirmation")
                    result = await self.execute(proposed, snapshot, user_input, session, options, identity)
                    timings = {**result.timings, **{name + "_ms": value["latency_ms"] for name, value in trace.items() if "latency_ms" in value},
                               "integration_total_ms": (time.perf_counter() - started) * 1000}
                    return replace(result, timings=timings)
            if outcome.status == "clarification":
                # Interpretation awaits I/O; cached names are not permission authority.
                from .contextual_catalog import build
                current_user = await self.revalidate(identity, user_input, options)
                self.validate_options(options)
                current_snapshot = build(self.hass, current_user, options)
                if current_snapshot.payload["generation"] != generation or origin_area(self.hass, user_input, options) != area:
                    session.pending = None
                    session.selection = None
                    session.candidates = ()
                    raise ExecutionError("stale", "clarification_catalog_changed")
                snapshot = current_snapshot
                # No partial plan is retained across a compound clarification.
                session.pending = outcome.command
                session.candidates = outcome.candidates
                session.pending_generation = generation
                session.pending_origin = area
                session.pending_options = _options_hash(options)
                session.expires = current_time + min(30, _ttl(options))
                if outcome.options:
                    question = self.set_question(outcome.options, snapshot)
                    session.selection = {"options": outcome.options, "command": outcome.command,
                                         "text": user_input.text, "generation": generation, "origin": area,
                                         "options_hash": _options_hash(options), "question": question,
                                         "confirmation_semantics": {"sim": "area", "nao": "name"}}
                    return self.result("missing_slot", user_input, speech=question, followup=True, reason=outcome.reason)
                labels = [(snapshot.by_id[item].get("attributes", {}).get("reference_device_name") or snapshot.by_id[item].get("device_name") or snapshot.by_id[item]["name"]) if outcome.command and outcome.command["parameters"].get("metric") in ("battery", "location") else snapshot.by_id[item]["name"] for item in outcome.candidates if item in snapshot.by_id]
                speech = "Qual alvo você quer usar?"
                if labels:
                    speech = "Encontrei " + ", ".join(labels[:4]) + ". Qual deles?"
                    if outcome.reason == "ambiguous_source":
                        kind = "aparelho" if outcome.command and outcome.command["parameters"].get("metric") in ("battery", "location") else "sensor"
                        speech = "Encontrei " + ", ".join(labels[:4]) + f". Qual {kind} você quer consultar?"
                elif outcome.reason in ("missing_origin_area", "unknown_area", "ambiguous_area"):
                    speech = "Em qual cômodo?"
                elif outcome.reason == "missing_media_query":
                    speech = "O que você quer ouvir?"
                elif outcome.reason and outcome.reason.startswith("missing_calendar_"):
                    speech = {"summary": "Qual é o título do compromisso?", "date": "Em qual dia?", "time": "A que horas?", "duration": "Qual é a duração do compromisso?"}.get(outcome.reason.removeprefix("missing_calendar_"), "Qual informação falta?")
                return self.result("missing_slot", user_input, speech=speech, followup=session.pending is not None, reason=outcome.reason)
            session.pending = None
            if outcome.status in ("cancel", "confirm", "repeat_response"):
                return self.result("cancelled", user_input, speech="Nenhuma ação executada.")
            if outcome.status != "plan":
                speech = None
                if eligible(outcome, pending=pending is not None or bool(session.candidates)):
                    # Clarification names are subject to the same post-I/O privacy
                    # and origin checks as executable semantic proposals.
                    current_user = await self.revalidate(identity, user_input, options)
                    from .contextual_catalog import build
                    if build(self.hass, current_user, options).payload["generation"] != generation or origin_context(self.hass, user_input, options) != spatial_context:
                        raise ExecutionError("stale", "semantic_catalog_changed")
                    from .semantic_context import clarification
                    partial = clarification(user_input.text, snapshot.payload, {"origin_area": area})
                    from .semantic_context import understood_action
                    action_missing = bool(partial["target_entities"]) and understood_action(user_input.text) is None
                    if action_missing:
                        session.last_targets = tuple(key for key, row in snapshot.by_id.items() if row["entity_id"] in partial["target_entities"])
                        session.last_area = partial["target_area"]
                    return self.result("missing_slot" if action_missing else "no_match", user_input,
                        speech=partial["clarification_question"], followup=True, reason="semantic_clarification")
                if outcome.reason == "target_not_found" and outcome.command is not None:
                    from .contextual_errors import requested_target
                    label = requested_target(outcome.command, user_input.text)
                    if label:
                        speech = f'Não encontrei um dispositivo chamado “{label}”.'
                return self.result(outcome.status, user_input, speech=speech, reason=outcome.reason)
            if origin_context(self.hass, user_input, options) != spatial_context:
                raise ExecutionError("stale", "origin_area_changed")
            if pending is not None and session.candidates:
                targets = {target for operation in outcome.operations for target in operation.targets}
                if not targets <= set(session.candidates):
                    raise ExecutionError("stale", "clarification_candidate_changed")
            session.expires = time.monotonic() + _ttl(options)
            self.stage = "planning"
            result = await self.execute(outcome.operations, snapshot, user_input, session, options, identity)
            timings = dict(outcome.timings or {})
            timings.update(result.timings)
            timings["integration_total_ms"] = (time.perf_counter() - started) * 1000
            return replace(result, timings=timings)
        except (ExecutionError, IdentityError) as error:
            if 'session' in locals():session.dialogue=None
            return self.result(error.code, user_input, reason=error.reason)
        except QueryError as error:
            if 'session' in locals():session.dialogue=None
            return self.result("unavailable", user_input, reason=str(error))
        except ClientError as error:
            reason = str(error) if str(error) in {'endpoint', 'health', 'diagnostics', 'length', 'status', 'transport', 'json'} else 'unknown'
            _LOGGER.warning('Falha na comunicação com o NLU local (motivo=%s). Confira o hostname do app, a porta 11555 e os logs do módulo NLU.', reason)
            return self.result("unavailable", user_input, reason="backend_unavailable")
        except (CatalogError, ProtocolError, CapabilityError):
            return self.result("unavailable", user_input, reason="invalid_request")
        except asyncio.CancelledError:
            raise
        except Saturated:
            raise
        except Exception:
            # Exception messages can include user data or authenticated URLs.
            return self.result("unavailable", user_input)

    def set_question(self, options: tuple[dict, ...], snapshot: Snapshot) -> str:
        nominal = next(option for option in options if option["key"] == "name")
        spatial = next(option for option in options if option["key"] == "area")
        from homeassistant.helpers import area_registry
        room = area_registry.async_get(self.hass).areas.get(spatial["area"])
        room_name = room.name if room is not None else "cômodo indicado"
        domain = snapshot.by_id[spatial["targets"][0]]["domain"]
        category = {"fan": "ventiladores", "light": "luzes", "switch": "interruptores"}.get(domain, "dispositivos")
        return (f"Quer usar todos os {len(spatial['targets'])} {category} de {room_name}? "
                f"Responda sim para todos do cômodo, ou não para apenas os {len(nominal['targets'])} que correspondem ao nome.")

    async def choose_set(self, text: str, user_input: Any, session: Session, identity: ResolvedIdentity, options: dict) -> Any:
        pending = session.selection
        if text in ("cancela", "cancela isso", "esquece", "deixa pra la"):
            session.selection = None
            session.pending = None
            return self.result("cancelled", user_input, speech="Cancelado.")
        from .contextual_catalog import build
        user = await self.revalidate(identity, user_input, options)
        snapshot = build(self.hass, user, options)
        area = origin_area(self.hass, user_input, options)
        if snapshot.payload["generation"] != pending["generation"] or area != pending["origin"] or _options_hash(options) != pending["options_hash"]:
            session.selection = None
            session.pending = None
            raise ExecutionError("stale", "dialogue_changed")
        choice = pending["confirmation_semantics"].get(text)
        number_words = {1: "um", 2: "dois", 3: "tres", 4: "quatro", 5: "cinco", 6: "seis"}
        if choice is None:
            spatial = next(option for option in pending["options"] if option["key"] == "area")
            from homeassistant.helpers import area_registry
            room = area_registry.async_get(self.hass).areas.get(spatial["area"])
            scope = next((text.split(marker, 1)[1] for marker in (" do ", " da ") if marker in text), None)
            if scope is None and text.startswith(("do ", "da ")):
                scope = text[3:]
            valid_scope = scope == "comodo" or (scope is not None and room is not None and any(set(scope.split()) <= set(normalize(label).split()) for label in (room.name, *getattr(room, "aliases", ()))))
            base = text if scope is None else text[:text.index(scope)].removesuffix("do ").removesuffix("da ").strip()
            nominal_words = set(normalize(pending["command"].get("mention") or "").split())
            name_reply = text.startswith("so os que tem ") and text.endswith(" no nome") and set(text.removeprefix("so os que tem ").removesuffix(" no nome").split()) <= nominal_words
            if text in ("todos", "opcao 2", "segunda opcao") or (valid_scope and base in ("", "todos")):
                choice = "area"
            elif name_reply or text in ("opcao 1", "primeira opcao", "por nome", "so por nome"):
                choice = "name"
            else:
                matches = [option["key"] for option in pending["options"] if (scope is None or valid_scope) and base.removeprefix("so ") in (f"os {len(option['targets'])}", f"os {number_words.get(len(option['targets']), len(option['targets']))}")]
                if len(matches) == 1:
                    choice = matches[0]
        if choice is None:
            return self.result("missing_slot", user_input, speech=pending["question"], followup=True, reason="invalid_set_choice")
        # Re-run the original interpretation against a fresh authorized snapshot.
        await self._send(snapshot)
        outcome = await self._interpret({"version": 4, "generation": snapshot.payload["generation"],
                      "text": pending["text"], "origin_area": area, "origin_area_source": origin_context(self.hass, user_input, options)[1], "last_area": session.last_area, "last_targets": list(session.last_targets), "pending": None})
        current = next((option for option in outcome.options if option["key"] == choice), None)
        expected = next(option for option in pending["options"] if option["key"] == choice)
        if current != expected or outcome.command != pending["command"]:
            session.selection = None
            session.pending = None
            raise ExecutionError("stale", "dialogue_changed")
        command = pending["command"]
        params = dict(command["parameters"])
        if current["area"] is not None:
            params["area"] = current["area"]
        evidence = tuple(current["evidence"])
        if command["action"] in ("turn_on", "turn_off") and command["plural"]:
            params["scope"] = "bulk_area" if current["area"] is not None else "bulk_global"
            if len(command["domains"]) == 1:
                params["category"] = command["domains"][0]
            evidence += ("bulk_area",)
            if current["area"] is not None:
                evidence += ("complete_spatial_set",)
        operation = Operation(command["intent"], command["action"], tuple(current["targets"]), params, evidence=evidence)
        session.selection = None
        session.pending = None
        session.candidates = ()
        session.expires = time.monotonic() + _ttl(options)
        return await self.execute((operation,), snapshot, user_input, session, options, identity)

    async def _send(self, snapshot: Snapshot) -> None:
        response = await self.client.async_catalog_v4(snapshot.payload)
        if parse(response).status != "catalog_ready" or response.get("generation") != snapshot.payload["generation"]:
            raise ProtocolError("catalog_ack")
        if len(self.sent_generations) >= 8:
            self.sent_generations.clear()
        self.sent_generations.add(snapshot.payload["generation"])

    def live(self, row: dict, action: str, user: Any, options: dict) -> Any:
        registry = entity_registry.async_get(self.hass)
        entry = registry.entities.get(row["entity_id"])
        if entry is not None and (entry.id != row["registry_id"] or getattr(entry, "device_id", None) != row.get("device_id")):
            raise ExecutionError("stale", "entity_replaced")
        if entry is None and not row["registry_id"].startswith("state_"):
            raise ExecutionError("stale", "entity_removed")
        if not exposed(self.hass, entry, row["entity_id"]):
            raise ExecutionError("denied", "not_exposed")
        if entry is not None and _effective_area_id(entry, device_registry.async_get(self.hass)) != row["area_id"]:
            raise ExecutionError("stale", "area_changed")
        state = self.hass.states.get(row["entity_id"])
        if state is None or state.state in ("unknown", "unavailable"):
            raise ExecutionError("stale", "entity_unavailable")
        if state.attributes.get("device_class") != row.get("device_class"):
            raise ExecutionError("stale", "device_class_changed")
        policy = "read" if action in READ_ACTIONS else "control"
        if user.is_admin is not True and user.permissions.check_entity(row["entity_id"], policy) is not True:
            raise ExecutionError("denied", "entity_permission")
        if action not in capabilities(self.hass, row["entity_id"], state, options.get("device_mappings")):
            raise ExecutionError("stale", "capability_changed")
        return state

    def prepare(self, operation: Operation, snapshot: Snapshot, user: Any, options: dict) -> Prepared:
        if operation.action in ("local_time", "local_date"):
            return Prepared(operation, ())
        from .capabilities import TEMPORAL_ACTIONS
        if operation.action in TEMPORAL_ACTIONS or (operation.action.startswith("timer_") and not operation.targets):
            if operation.action == "schedule_action":
                rows = []
                target_action = operation.parameters.get("target_action", "turn_on")
                for target in operation.targets:
                    row = snapshot.by_id.get(target)
                    if row is None:
                        raise ExecutionError("stale", "target_not_in_snapshot")
                    state = self.live(row, target_action, user, options)
                    self.validate_scope(row, operation)
                    rows.append(row)
                return Prepared(operation, tuple(rows), ())
            return Prepared(operation, ())
        if operation.action in ("binding", "camera_view"):
            binding = options.get("intent_bindings", {}).get(operation.intent)
            if type(binding) is not dict:
                raise ExecutionError("unavailable", "configured_adapter_missing")
            entity_id = binding.get("entity_id")
            rows = [row for row in snapshot.by_id.values() if row["entity_id"] == entity_id]
            if len(rows) != 1 or rows[0]["domain"] not in ("script", "button", "input_button"):
                raise ExecutionError("unavailable", "configured_adapter_target")
            row = rows[0]
            bound_action = "activate" if row["domain"] == "script" else "press"
            state = self.live(row, bound_action, user, options)
            values = operation.parameters.get("value", {})
            names = binding.get("variables", {})
            if type(values) is not dict or type(names) is not dict or (values and row["domain"] != "script"):
                raise ExecutionError("unavailable", "adapter_parameters")
            if any(key not in names or type(names[key]) is not str or not names[key].isidentifier() or len(names[key]) > 64 for key in values):
                raise ExecutionError("unavailable", "adapter_parameter_not_mapped")
            data = {"variables": {names[key]: value for key, value in values.items()}} if values else {}
            return Prepared(operation, (row,), ({"domain": row["domain"], "service": ADAPTERS[(row["domain"], bound_action)].service, "data": data, "entity_id": entity_id, "bound_action": bound_action},), binding.get("sensitive", True) is True)
        rows = []
        calls = []
        needs_confirmation = False
        for target in operation.targets:
            row = snapshot.by_id.get(target)
            if row is None:
                raise ExecutionError("stale", "target_not_in_snapshot")
            state = self.live(row, operation.action, user, options)
            if self.is_bulk(operation):
                defaults = ["light", "switch", "fan", "climate", "media_player", "humidifier"]
                preferences = options.get("entity_preferences", {}).get(row["entity_id"], {})
                from .contextual_catalog import _bulk_exclusions
                if operation.action not in ("turn_on", "turn_off") or (operation.action == "turn_on" and operation.parameters.get("scope") not in ("bulk_area", "bulk_floor_group", "floor_group")) or row["domain"] not in defaults or row["domain"] not in options.get("bulk_domains", defaults) or row["entity_id"] in _bulk_exclusions(self.hass, options.get("excluded_from_bulk_actions", [])) or row.get("attributes", {}).get("bulk_eligible") is not True or (type(preferences) is dict and (preferences.get("excluded_from_bulk_actions") is True or preferences.get("critical") is True or preferences.get("bulk_eligible") is False)):
                    raise ExecutionError("denied", "bulk_policy")
            self.validate_scope(row, operation)
            rows.append(row)
            is_sensitive = sensitive(row["domain"], operation.action, state)
            if is_sensitive:
                if row["entity_id"] not in options.get("sensitive_entities", []):
                    raise ExecutionError("denied", "sensitive_policy")
                needs_confirmation = True
            if row["domain"] == "lock" and operation.action == "unlock" and state.attributes.get("code_format"):
                raise ExecutionError("denied", "authentication_required")
            if row["domain"] == "alarm_control_panel" and operation.action in ("arm", "disarm") and (state.attributes.get("code_arm_required", True) if operation.action == "arm" else state.attributes.get("code_format")):
                raise ExecutionError("denied", "authentication_required")
            if operation.action in ('automation_trigger','todo_remove_completed'):
                needs_confirmation=True
            if operation.action in ("query", "calendar", "forecast", "todo_list", "history_query"):
                if operation.action == "query":
                    self.read_live(row, state, operation.parameters, snapshot, user, options)
                elif operation.action == "calendar":
                    date_window(self.hass, operation.parameters.get("date"))
                continue
            if operation.action in ("music", "transfer"):
                if not is_music_player(self.hass, row["entity_id"]):
                    raise ExecutionError("unavailable", "music_assistant_player_required")
                if operation.action == "music" and (type(operation.parameters.get("value")) is not str or not operation.parameters["value"].strip()):
                    raise ExecutionError("unavailable", "missing_media_query")
                if operation.action == "transfer":
                    source_targets = operation.parameters.get("secondary_targets", [])
                    if len(source_targets) != 1 or source_targets[0] not in snapshot.by_id:
                        raise ExecutionError("unavailable", "missing_music_source")
                    if source_targets[0] in operation.targets:
                        raise ExecutionError("unavailable", "music_source_equals_destination")
                    self.live(snapshot.by_id[source_targets[0]], "transfer", user, options)
                continue
            if operation.action in ("remote_key", "channel"):
                calls.extend(self.remote_calls(row, operation, snapshot, user, options))
                continue
            if operation.action == "calendar_create":
                value = operation.parameters.get("value")
                data = calendar_parameters(self.hass, value)
            else:
                adapter = ADAPTERS.get((row["domain"], operation.action))
                if adapter is None:
                    raise ExecutionError("unavailable", "adapter_missing")
                try:
                    data = parameters(operation.action, adapter, state, operation.parameters, options.get("increments", {}), strict_numeric=operation.intent.startswith('llm2.'))
                except CapabilityError as error:
                    raise ExecutionError("unavailable", str(error)) from None
                if operation.action == "vacuum_area":
                    from homeassistant.helpers import area_registry
                    if operation.parameters.get("area") not in area_registry.async_get(self.hass).areas:
                        raise ExecutionError("unavailable", "unknown_cleaning_area")
            adapter = ADAPTERS[(row["domain"], operation.action)]
            calls.append({"domain": row["domain"], "service": adapter.service, "data": data, "entity_id": row["entity_id"]})
        return Prepared(operation, tuple(rows), tuple(calls), needs_confirmation)

    def validate_scope(self, row: dict, operation: Operation) -> None:
        if operation.parameters.get("category") is not None and row["domain"] != operation.parameters["category"]:
            raise ExecutionError("denied", "bulk_category_mismatch")
        if operation.parameters.get("scope") == "bulk_area" and (operation.parameters.get("area") is None or row.get("area_id") != operation.parameters["area"]):
            raise ExecutionError("denied", "bulk_area_mismatch")
        if operation.parameters.get("scope") in ("floor_group", "bulk_floor_group"):
            from homeassistant.helpers import area_registry
            area = area_registry.async_get(self.hass).areas.get(row.get("area_id"))
            if area is None or getattr(area, "floor_id", None) != operation.parameters.get("area"):
                raise ExecutionError("stale", "floor_membership_changed")

    def remote_calls(self, row: dict, operation: Operation, snapshot: Snapshot, user: Any, options: dict) -> list[dict]:
        mapping = options.get("device_mappings", {}).get(row["entity_id"], {}).get(operation.action)
        value = operation.parameters.get("value")
        if operation.action == "remote_key":
            if type(value) is not str or type(mapping) is not dict or normalize(value) not in mapping:
                raise ExecutionError("unavailable", "remote_key_not_mapped")
            mapping = mapping[normalize(value)]
            commands = [mapping.get("command")] if type(mapping) is dict else []
        else:
            if type(value) not in (int, float) or value != int(value) or not 0 <= value <= 9999 or type(mapping) is not dict:
                raise ExecutionError("invalid_request", "channel")
            digits = mapping.get("digits", {})
            commands = [digits.get(digit) for digit in str(int(value))]
            if mapping.get("enter"):
                commands.append(mapping["enter"])
        if type(mapping) is not dict:
            raise ExecutionError("unavailable", "remote_mapping")
        target = mapping.get("entity_id")
        targets = [candidate for candidate in snapshot.by_id.values() if candidate["entity_id"] == target]
        if len(targets) != 1 or targets[0]["domain"] not in ("remote", "script", "button", "input_button"):
            raise ExecutionError("unavailable", "remote_backend")
        backend = targets[0]
        action = "remote_key" if backend["domain"] == "remote" else "activate" if backend["domain"] == "script" else "press"
        self.live(backend, action, user, options)
        if backend["domain"] == "remote":
            if not self.hass.services.has_service("remote", "send_command") or not commands or any(type(command) is not str or not command or len(command.encode()) > 128 for command in commands):
                raise ExecutionError("unavailable", "remote_command")
            data = {"command": commands}
            if type(mapping.get("device")) is str:
                data["device"] = mapping["device"]
            return [{"domain": "remote", "service": "send_command", "entity_id": target, "data": data, "backend_row": backend, "bound_action": action}]
        return [{"domain": backend["domain"], "service": ADAPTERS[(backend["domain"], action)].service, "entity_id": target, "data": {}, "backend_row": backend, "bound_action": action}]

    @observe_execution
    async def execute(self, operations: tuple[Operation, ...], snapshot: Snapshot, user_input: Any, session: Session, options: dict, identity: ResolvedIdentity, *, confirmed: bool = False) -> Any:
        from .dialogue_context import context_request
        if len(operations)==1 and operations[0].intent=='context.ellipsis':
            from .dialogue_context import validate_copy
            if session.dialogue is None or not session.dialogue.valid(snapshot.payload['generation'],_options_hash(options)):
                raise QueryError('context_insufficient')
            validate_copy(user_input.text,session.dialogue,operations[0],snapshot,options)
        if len(operations)==1 and operations[0].action=='contextual_undo':
            record=session.dialogue
            if record is None or not record.valid(snapshot.payload['generation'],_options_hash(options)):
                raise QueryError('undo_unavailable')
            source=snapshot.by_id.get(operations[0].targets[0])
            if source is None:raise QueryError('undo_unavailable')
            if options.get('entity_preferences',{}).get(source['entity_id'],{}).get('critical') is True:raise QueryError('undo_unavailable')
            operations=(record.undo(self.hass.states.get(source['entity_id']),operations[0].targets),)
        elif not context_request(user_input.text):
            session.dialogue=None
        # Resolve physical targets/backends before locking. Preparation here is
        # passive; repeat all live validation and relative reads under the locks.
        user = await self.revalidate(identity, user_input, options)
        tentative = tuple(self.prepare(op, snapshot, user, options) for op in operations)
        resources = {row['entity_id'] for item in tentative for row in item.rows}
        resources.update('device:'+row['device_id'] for item in tentative for row in item.rows if row.get('device_id'))
        resources.update(call['entity_id'] for item in tentative for call in item.calls)
        resources.update('device:'+call['backend_row']['device_id'] for item in tentative for call in item.calls if call.get('backend_row',{}).get('device_id'))
        async with observed_wait('resource_queue', self.resource_locks.hold(resources)):
            return await self._execute_locked(operations, snapshot, user_input, session, options, identity, confirmed=confirmed)

    async def _execute_locked(self, operations, snapshot, user_input, session, options, identity, *, confirmed=False):
        validation_start = time.perf_counter()
        from .typed_constraints import operational_refusal
        from .semantic_context import read_only_question, clearly_conversational, speech_only_request
        refusal = operational_refusal(user_input.text, snapshot.payload, operations)
        from .todo_actions import literal_binding, bind as bind_todo, MUTATIONS
        from .dialogue_context import exact_scene, remember, acknowledged
        if user_input.text.lstrip().startswith(('"',"'",'“','‘','`','«')) and any(op.action not in READ_ACTIONS and op.intent.startswith(('todo.','context.','automation.')) for op in operations):
            raise ExecutionError('unavailable','semantic_quoted_command')
        from .automation_actions import requested
        if not confirmed and any(op.intent in ('automation.run','automation.enable','automation.disable') and not requested(user_input.text,op,snapshot) for op in operations):
            raise ExecutionError('unavailable','semantic_quoted_command')
        if any(op.intent=='scene.invoke_exact' for op in operations) and not exact_scene(user_input.text,operations,snapshot):
            raise QueryError('scene_not_unique')
        if literal_binding(user_input.text,operations): refusal=None
        if not refusal and not exact_scene(user_input.text,operations,snapshot) and (read_only_question(user_input.text) or clearly_conversational(user_input.text, snapshot.payload)) and any(op.action not in READ_ACTIONS for op in operations):
            refusal = 'semantic_non_effect_request'
        if speech_only_request(user_input.text):
            refusal = 'semantic_non_effect_request'
        if refusal:
            raise ExecutionError('unavailable', refusal)
        self.stage = "authorization"
        spatial_context = origin_context(self.hass, user_input, options)
        for operation in operations:
            if operation.intent.endswith(".implicit_on"):
                category = operation.intent.removesuffix(".implicit_on")
                if category not in ("light", "fan") or operation.action != "turn_on" or operation.parameters.get("scope") != "bulk_area" or spatial_context[1] not in ("satellite", "device") or operation.parameters.get("area") != spatial_context[0] or any(snapshot.by_id.get(target, {}).get("domain") != category for target in operation.targets):
                    raise ExecutionError("denied", "implicit_origin_policy")
        user = await self.revalidate(identity, user_input, options)
        self.validate_options(options)
        prepared = tuple(self.prepare(operation, snapshot, user, options) for operation in operations)
        self.validate_plan(prepared)
        self.validate_bulk_snapshot(operations, snapshot, user, options)
        undo_record=session.dialogue if operations[0].intent=='context.undo' else None
        if undo_record:
            undo_record.undo(self.hass.states.get(snapshot.by_id[operations[0].targets[0]]['entity_id']),operations[0].targets)
        todo_bound={}
        for i,item in enumerate(prepared):
            if item.operation.action in MUTATIONS:
                if len(item.rows)!=1 or len(item.calls)!=1:raise QueryError('todo_single_list_required')
                async def authorize(item=item):
                    current=await self.revalidate(identity,user_input,options)
                    self.validate_options(options)
                    if origin_context(self.hass,user_input,options)!=spatial_context:raise ExecutionError('stale','origin_area_changed')
                    self.live(item.rows[0],item.operation.action,current,options)
                    self.live(item.rows[0],'todo_list',current,options)
                todo_bound[i]=await bind_todo(self.hass,item.calls[0],item.operation.action,user_input.context,authorize)
        clear_evidence={str(i):value[1] for i,value in todo_bound.items() if prepared[i].operation.action=='todo_remove_completed'}
        if confirmed and clear_evidence and session.todo_confirmation!=clear_evidence:
            raise QueryError('todo_completed_set_changed')
        if any(item.needs_confirmation for item in prepared) and not confirmed:
            session.dialogue=None
            session.todo_confirmation=clear_evidence
            session.confirmation = operations
            session.confirmation_generation = snapshot.payload["generation"]
            session.confirmation_origin = origin_area(self.hass, user_input, options)
            session.confirmation_origin_context = spatial_context
            session.confirmation_options = _options_hash(options)
            session.expires = time.monotonic() + min(30, _ttl(options))
            return self.result("confirmation_required", user_input, speech=f'Confirma remover {sum(value[1] for value in clear_evidence.values())} itens concluídos dessa lista?' if clear_evidence else 'Confirma solicitar o disparo dessa automação, respeitando suas condições?' if any(op.action=='automation_trigger' for op in operations) else "Esse comando altera um dispositivo de acesso ou proteção. Confirma?", followup=True)
        session.todo_confirmation={}
        candidate=None
        session.dialogue=None
        signature = hashlib.sha256(json.dumps([operation.as_dict() for operation in operations], sort_keys=True).encode()).hexdigest()
        if any(item.operation.action not in READ_ACTIONS for item in prepared) and session.signature == signature and time.monotonic() - session.last_execution < 2:
            return self.result("cancelled", user_input, speech="Evitei repetir a tentativa recente desse comando.")
        validation_ms = (time.perf_counter() - validation_start) * 1000
        if trace := CURRENT.get():
            trace.end('validation', 'accepted')
            trace.start('ha')
        completed = 0
        had_effects = False
        attempted_effect = False
        responses = []
        execution_start = time.perf_counter()
        try:
            for item_number,item in enumerate(prepared):
                # Renew authentication and rebind each entire operation immediately before execution.
                user = await self.revalidate(identity, user_input, options)
                self.validate_options(options)
                fresh = self.prepare(item.operation, snapshot, user, options)
                if fresh.calls != item.calls:
                    raise ExecutionError("stale", "parameters_changed")
                action = item.operation.action
                from .capabilities import TEMPORAL_ACTIONS
                if action in TEMPORAL_ACTIONS or (action.startswith("timer_") and not item.calls):
                    responses.append(await self.handle_temporal(item, snapshot, user_input, user, options))
                    completed += 1
                    continue
                if action in READ_ACTIONS:
                    async with asyncio.timeout(10):
                        responses.append(await self.query(item, snapshot, user_input, user, options, identity))
                elif action in ("music", "transfer"):
                    attempted_effect = True
                    session.signature, session.last_execution = signature, time.monotonic()
                    async with asyncio.timeout(10):
                        await self.music(item, snapshot, user_input, user, options, identity)
                    had_effects = True
                else:
                    for call_index, call in enumerate(item.calls):
                        # Includes the permission of a remote/script backend, separate from TV.
                        user = await self.revalidate(identity, user_input, options)
                        self.validate_options(options)
                        if origin_context(self.hass, user_input, options) != spatial_context:
                            raise ExecutionError("stale", "origin_area_changed")
                        current_calls = self.prepare(item.operation, snapshot, user, options).calls
                        self.validate_bulk_snapshot(operations, snapshot, user, options)
                        if len(current_calls) != len(item.calls) or current_calls[call_index] != call:
                            raise ExecutionError("stale", "parameters_changed")
                        if call.get("backend_row"):
                            self.live(call["backend_row"], call["bound_action"], user, options)
                        for row in item.rows:
                            self.live(row, call.get("bound_action", action) if action in ("binding", "camera_view") else action, user, options)
                            self.validate_scope(row, item.operation)
                        actual_data=call['data']
                        if undo_record:
                            undo_record.undo(self.hass.states.get(call['entity_id']),item.operation.targets)
                        if item.operation.intent in ('context.ellipsis','context.undo'):
                            from .contextual_catalog import build
                            if build(self.hass,user,options).payload['generation']!=snapshot.payload['generation']:
                                raise ExecutionError('stale','dialogue_changed')
                        if action in MUTATIONS:
                            async def authorize():
                                current=await self.revalidate(identity,user_input,options)
                                self.validate_options(options)
                                if origin_context(self.hass,user_input,options)!=spatial_context:raise ExecutionError('stale','origin_area_changed')
                                self.live(item.rows[0],action,current,options)
                                self.live(item.rows[0],'todo_list',current,options)
                            latest=await bind_todo(self.hass,call,action,user_input.context,authorize)
                            if latest!=todo_bound[item_number]:raise QueryError('todo_item_changed')
                            actual_data=latest[0]
                        # No await between the pre-effect snapshot and service dispatch.
                        # Earlier authentication/list reads can yield to external changes.
                        if len(prepared)==1 and len(item.calls)==1:
                            candidate=remember(item,self.hass.states.get(call['entity_id']),snapshot.payload['generation'],_options_hash(options),_ttl(options))
                        async with asyncio.timeout(10):
                            self.stage = "ha_execution"
                            attempted_effect = True
                            session.signature, session.last_execution = signature, time.monotonic()
                            await self.hass.services.async_call(call["domain"], call["service"], actual_data, blocking=True, context=user_input.context, target={"entity_id": [call["entity_id"]]})
                        had_effects = True
                        if action=='automation_trigger':
                            from .automation_actions import trigger_response
                            responses.append(trigger_response())
                completed += 1
        except asyncio.CancelledError:
            raise
        except (ExecutionError, IdentityError, QueryError, CapabilityError) as error:
            if attempted_effect:
                session.signature, session.last_execution = signature, time.monotonic()
            return self.result("partial_failure" if completed or had_effects else getattr(error, "code", "unavailable"), user_input,
                               speech=f"Concluí {completed} operações; o restante não foi executado." if completed else "Parte dos dispositivos respondeu; parei antes de continuar." if had_effects else "Não consegui concluir essa operação.", count=completed, reason=getattr(error, "reason", str(error)))
        except Exception:
            if attempted_effect:
                session.signature, session.last_execution = signature, time.monotonic()
            return self.result("partial_failure" if completed or had_effects else "execution_failed", user_input, speech=f"Concluí {completed} operações; houve uma falha na próxima." if completed else "Parte dos dispositivos respondeu; o serviço não confirmou o restante." if had_effects else "O serviço não confirmou a execução.", count=completed)
        session.last_targets = operations[-1].targets
        session.dialogue=acknowledged(candidate,self.hass.states.get(prepared[0].rows[0]['entity_id'])) if candidate and had_effects else None
        session.last_action = operations[-1].action
        target_areas={snapshot.by_id[t].get('area_id') for t in session.last_targets}
        session.last_area = next(iter(target_areas)) if len(target_areas)==1 else None
        session.signature, session.last_execution = signature, time.monotonic()
        speech = " ".join(responses) if responses else "Pronto." if completed == 1 else f"Pronto, executei {completed} operações."
        if len(speech.encode()) > 8192:
            speech = "A resposta excedeu o limite permitido. Peça uma consulta mais específica."
        session.response = speech
        return self.result("query_success" if responses and all(item.operation.action in READ_ACTIONS for item in prepared) else "success", user_input,
                           speech=speech, count=completed, timings={"validation_ms": validation_ms, "ha_execution_response_ms": (time.perf_counter() - execution_start) * 1000})

    def validate_plan(self, prepared: tuple[Prepared, ...]) -> None:
        affected = set()
        for item in prepared:
            action = item.operation.action
            if action in READ_ACTIONS:
                continue
            property_name = "power" if action in ("turn_on", "turn_off", "open", "close", "unlock", "lock", "activate") else "playback" if action in ("music", "play", "pause", "stop") else action
            if item.operation.intent.startswith('llm2.'):
                from .automation_contract import effect_property
                property_name=effect_property(action)
            for row in item.rows:
                key = (row["entity_id"], property_name)
                if key in affected:
                    raise ExecutionError("invalid_request", "contradictory_plan")
                affected.add(key)

    def validate_bulk_snapshot(self, operations: tuple[Operation, ...], snapshot: Snapshot, user: Any, options: dict) -> None:
        if any(self.is_bulk(operation) for operation in operations):
            from .contextual_catalog import build
            if build(self.hass, user, options).payload["generation"] != snapshot.payload["generation"]:
                raise ExecutionError("stale", "bulk_catalog_changed")
        for operation in operations:
            if "complete_spatial_set" not in operation.evidence:
                continue
            scope, area, category = (operation.parameters.get(key) for key in ("scope", "area", "category"))
            if scope not in ("bulk_area", "bulk_floor_group", "bulk_global"):
                raise ExecutionError("denied", "bulk_scope_missing")
            members = {area}
            if scope == "bulk_floor_group":
                groups = [group for group in snapshot.payload.get("groups", []) if group["group_id"] == area]
                if len(groups) != 1:
                    raise ExecutionError("stale", "floor_membership_changed")
                members = set(groups[0]["area_ids"])
            candidates = [row for row in snapshot.by_id.values() if row["attributes"].get("bulk_eligible") is True and (category is None or row["domain"] == category) and (scope == "bulk_global" or row["area_id"] in members)]
            expected = {row["registry_id"] for row in candidates if row["attributes"].get("available") is not False and operation.action in row["actions"]}
            excluded_targets = set(operation.parameters.get("excluded_targets", []))
            expected -= excluded_targets
            if expected != set(operation.targets):
                raise ExecutionError("denied", "bulk_target_set_changed")

    @staticmethod
    def is_bulk(operation: Operation) -> bool:
        return operation.parameters.get("scope") in ("bulk_area", "bulk_floor_group", "bulk_global") or "bulk_area" in operation.evidence or (operation.action in ("turn_on", "turn_off") and len(operation.targets) > 1)

    def read_live(self, row: dict, state: Any, params: dict, snapshot: Snapshot, user: Any, options: dict) -> dict:
        if params.get("metric") == "location" and (row["domain"] == "sensor" or params.get("scope") == "device_location"):
            from .device_location import read_location
            return read_location(self.hass, row, state, snapshot, user, options, self.live)
        if params.get("metric") != "location":
            value = read(self.hass, row, state, params)
            if params.get("metric") == "battery":
                value["label"] = row.get("attributes", {}).get("reference_device_name") or row.get("device_name") or row["name"]
                preference = options.get("entity_preferences", {}).get(row["entity_id"], {})
                if "max_age_seconds" in preference:
                    from .device_location import observation_age
                    attribute = preference.get("observed_at_attribute", "last_seen")
                    if attribute in ("last_changed", "last_updated", "last_reported"):
                        raise QueryError("measurement_stale")
                    try:
                        observation_age(self.hass, state.attributes.get(attribute), preference["max_age_seconds"])
                    except QueryError:
                        raise QueryError("measurement_stale") from None
            from homeassistant.helpers import area_registry
            room = area_registry.async_get(self.hass).areas.get(row.get("area_id"))
            if room is not None:
                value["area_name"] = room.name
                if params.get("area") == row.get("area_id"):
                    value["scope_label"] = room.name
            return value
        preferences = options.get("entity_preferences", {}).get(row["entity_id"], {})
        tracker = preferences.get("indoor_tracker") if type(preferences) is dict else None
        if tracker is None:
            if params.get("area"):
                raise QueryError("no_reliable_indoor_location")
            return read(self.hass, row, state, params)
        source = next((candidate for candidate in snapshot.by_id.values() if candidate["entity_id"] == tracker), None)
        if source is None or source["domain"] not in ("sensor", "device_tracker"):
            raise QueryError("indoor_tracker_missing")
        tracker_state = self.live(source, "query", user, options)
        from homeassistant.helpers import area_registry
        rooms = [room for aid, room in area_registry.async_get(self.hass).areas.items() if normalize(tracker_state.state) in (normalize(aid), normalize(room.name))]
        if len(rooms) != 1:
            raise QueryError("indoor_location_unknown")
        from types import SimpleNamespace
        return read(self.hass, row, SimpleNamespace(state=rooms[0].name, attributes={}), params)

    async def query(self, item: Prepared, snapshot: Snapshot, user_input: Any, user: Any, options: dict, identity: ResolvedIdentity) -> str:
        action, params = item.operation.action, item.operation.parameters
        if action=='history_query':
            from .advanced_queries import query
            expected_origin=origin_context(self.hass,user_input,options)
            async def authorize():
                current=await self.revalidate(identity,user_input,options)
                self.validate_options(options)
                if origin_context(self.hass,user_input,options)!=expected_origin:raise ExecutionError('stale','origin_area_changed')
                for row in item.rows:self.live(row,'history_query',current,options)
            return await query(self.hass,item.rows,params,authorize)
        if action == "local_time":
            return f"São {now(self.hass):%H:%M}."
        if action == "local_date":
            return f"Hoje é {now(self.hass):%d/%m/%Y}."
        if action == "query":
            if item.operation.intent in ('query.temperature_extremes','query.battery_ranking','query.power_ranking'):
                for row in item.rows:
                    state=self.live(row,action,user,options)
                    if params.get('metric')=='temperature' and row.get('attributes',{}).get('measurement_kind') in ('equipment','outdoor'):raise QueryError('no_compatible_sensor')
                    if params.get('metric')=='power' and state.attributes.get('state_class')!='measurement':raise QueryError('invalid_measurement')
                    if params.get('metric') in ('temperature','humidity') and state.attributes.get('state_class') not in (None,'measurement'):raise QueryError('invalid_measurement')
            values = [self.read_live(row, self.live(row, action, user, options), params, snapshot, user, options) for row in item.rows]
            if params.get("metric") == "location" and params.get("area"):
                area = params["area"]
                from homeassistant.helpers import area_registry
                room = area_registry.async_get(self.hass).areas.get(area)
                if room is None:
                    raise QueryError("no_reliable_indoor_location")
                same = all(normalize(str(value["value"])) == normalize(room.name) for value in values)
                return ("Sim. " if same else "Não. ") + render(values, params, options)
            return render(values, params, options)
        if action == "calendar":
            start, end = date_window(self.hass, params.get("date"))
            data, service = {"start_date_time": start, "end_date_time": end}, "get_events"
        elif action == "forecast":
            data, service = {"type": "daily"}, "get_forecasts"
        else:
            data, service = {"status": "needs_action"}, "get_items"
        raw = await self.hass.services.async_call(item.rows[0]["domain"], service, data, blocking=True, context=user_input.context,
                                                   target={"entity_id": [row["entity_id"] for row in item.rows]}, return_response=True)
        self.validate_options(options)
        current_user = await self.revalidate(identity, user_input, options)
        for row in item.rows:
            self.live(row, action, current_user, options)
        if type(raw) is not dict or set(raw) != {row["entity_id"] for row in item.rows}:
            raise QueryError("service_response_entities")
        field = {"calendar": "events", "todo_list": "items", "forecast": "forecast"}[action]
        if any(type(result) is not dict or type(result.get(field)) is not list or len(result[field]) > 32 or any(type(entry) is not dict for entry in result[field]) for result in raw.values()):
            raise QueryError("service_response_items")
        if sum(len(result[field]) for result in raw.values()) > 32:
            raise QueryError("service_response_limit")
        if action == "calendar":
            return calendar_response(raw, params)
        if action == "todo_list":
            titles = [entry.get("summary") for result in raw.values() for entry in result.get("items", []) if type(entry) is dict]
            if any(type(title) is not str for title in titles) or len(titles) > 32:
                raise QueryError("todo_response")
            return "; ".join(titles) + "." if titles else "A lista está vazia."
        forecasts = [entry for result in raw.values() for entry in result.get("forecast", []) if type(entry) is dict]
        if not forecasts:
            raise QueryError("forecast_empty")
        start, end = date_window(self.hass, params.get("date"))
        matches = [entry for entry in forecasts if start[:10] == str(entry.get("datetime", ""))[:10]]
        if not matches:
            raise QueryError("forecast_date_unavailable")
        readings = []
        for entry in matches:
            condition, temperature = entry.get("condition"), entry.get("temperature")
            if type(entry.get("datetime")) is not str or (condition is not None and type(condition) is not str) or (temperature is not None and not finite(temperature)):
                raise QueryError("forecast_reading")
            parts = [condition] if condition else []
            if temperature is not None:
                from .queries import decimal
                parts.append(f"{decimal(temperature)} graus")
            if not parts:
                raise QueryError("forecast_reading_missing")
            readings.append(", ".join(parts))
        return "; ".join(readings) + "."

    async def music(self, item: Prepared, snapshot: Snapshot, user_input: Any, user: Any, options: dict, identity: ResolvedIdentity) -> None:
        row = item.rows[0]
        if len(item.rows) != 1:
            raise ExecutionError("unavailable", "configured_music_group_required")
        if item.operation.action == "transfer":
            source = snapshot.by_id[item.operation.parameters["secondary_targets"][0]]
            self.live(source, "transfer", user, options)
            data = {"source_player": source["entity_id"], "auto_play": True}
            service = "transfer_queue"
        else:
            entry = entity_registry.async_get(self.hass).entities.get(row["entity_id"])
            config_entry_id = getattr(entry, "config_entry_id", None)
            if type(config_entry_id) is not str:
                raise ExecutionError("unavailable", "music_assistant_config_entry")
            query = item.operation.parameters["value"]
            media_type = "artist" if item.operation.intent == "media.play_artist" else "playlist" if item.operation.intent == "media.play_playlist" else "track"
            raw = await self.hass.services.async_call("music_assistant", "search", {"config_entry_id": config_entry_id, "name": query, "media_type": [media_type], "search_options": {"limit": 5}}, blocking=True, context=user_input.context, return_response=True)
            if type(raw) is not dict:
                raise ExecutionError("unavailable", "music_search_response")
            items = raw.get(media_type + "s", [])
            if type(items) is not list or len(items) > 25:
                raise ExecutionError("unavailable", "music_search_limit")
            provider = item.operation.parameters.get("provider")
            matches = [candidate for candidate in items if type(candidate) is dict and type(candidate.get("uri")) is str and type(candidate.get("name")) is str and normalize(candidate["name"]) == normalize(query) and (provider is None or candidate["uri"].startswith(provider + "://"))]
            if len(matches) != 1:
                raise ExecutionError("unavailable", "music_search_ambiguous_or_empty")
            data = {"media_id": matches[0]["uri"], "media_type": media_type, "enqueue": "replace"}
            service = "play_media"
        current_user = await self.revalidate(identity, user_input, options)
        self.validate_options(options)
        self.live(row, item.operation.action, current_user, options)
        if item.operation.action == "transfer":
            self.live(source, "transfer", current_user, options)
        await self.hass.services.async_call("music_assistant", service, data, blocking=True, context=user_input.context, target={"entity_id": [row["entity_id"]]})

    async def _execute_scheduled(self, job):
        """No neural queue, no retry; revalidate persisted authority at commit."""
        from types import SimpleNamespace
        from .contextual_catalog import build
        authority = getattr(job, 'authority', None)
        if type(authority) is not dict or job.target_action not in ('turn_on','turn_off','pause'):
            return False
        options = self.current_options()
        self.request_revision = self.options_revision
        if authority.get('options') != _schedule_options_hash(options):
            return False
        try:
            from homeassistant.core import Context
            context = Context(user_id=authority['user_id'] if authority.get('identity_source') == 'context' else None)
        except ImportError:
            context = SimpleNamespace(user_id=authority['user_id'])
        user_input = SimpleNamespace(context=context, conversation_id=authority['conversation_id'],
            satellite_id=authority.get('satellite_id'), device_id=authority.get('device_id'), text='', language='pt-BR')
        identity = await resolve_request_identity(self.hass, user_input, options)
        if identity.source != authority.get('identity_source') or identity.user_id != authority.get('user_id'):
            return False
        user_input = effective_input(user_input, identity)
        context = user_input.context
        preliminary = build(self.hass,identity.user,options)
        devices = {row['entity_id']:row.get('device_id') for row in preliminary.by_id.values() if row['entity_id'] in job.targets}
        resources=set(job.targets)|{'device:'+device for device in devices.values() if device}
        async with self.resource_locks.hold(resources):
            user = await self.revalidate(identity, user_input, options)
            snapshot = build(self.hass, user, options)
            if list(origin_context(self.hass, user_input, options)) != authority.get('origin'):
                return False
            by_entity = {row['entity_id']: row for row in snapshot.by_id.values()}
            rows = [by_entity.get(entity) for entity in job.targets]
            if any(row is None for row in rows) or [row['registry_id'] for row in rows] != authority.get('registry_ids'):
                return False
            if any(row.get('device_id') != devices.get(row['entity_id']) for row in rows):
                return False
            operation = Operation('scheduled.action', job.target_action, tuple(row['registry_id'] for row in rows), {})
            # All targets validated before any effect, with the same adapters as speech.
            prepared = self.prepare(operation, snapshot, user, options)
            if prepared.needs_confirmation:
                return False
            for call in prepared.calls:
                user = await self.revalidate(identity, user_input, options)
                if job.status == 'cancelled':
                    return False
                fresh = build(self.hass, user, options)
                if self.prepare(operation, fresh, user, options).calls != prepared.calls:
                    return False
                await self.hass.services.async_call(call['domain'], call['service'], call['data'],
                    blocking=True, context=context, target={'entity_id':[call['entity_id']]})
            return True

    async def handle_temporal(self, item: Prepared, snapshot: Snapshot, user_input: Any, user: Any, options: dict) -> str:
        op = item.operation
        action = op.action
        params = op.parameters
        if action == "clock_alarm_create":
            job = await self.scheduler.async_add_job(
                job_type="alarm",
                target_action="clock_alarm",
                time_str=params.get("time"),
                date_str=params.get("date"),
                recurrence=params.get("recurrence"),
                name=params.get("name"),
            )
            time_display = params.get("time", "")
            return f"Despertador definido para as {time_display}." if time_display else "Despertador configurado."

        if action == "clock_alarm_list":
            jobs = self.scheduler.list_jobs(job_type="alarm")
            if not jobs:
                return "Você não tem despertadores ativos."
            details = [f"{j.due_iso[11:16]}" + (f" ({j.name})" if j.name else "") for j in jobs]
            return f"Você tem {len(jobs)} despertador{'es' if len(jobs) > 1 else ''} ativo{'s' if len(jobs) > 1 else ''}: " + ", ".join(details) + "."

        if action == "clock_alarm_cancel":
            cancelled = await self.scheduler.async_cancel_jobs(
                job_type="alarm",
                name=params.get("name"),
                time_str=params.get("time"),
            )
            return "Despertador cancelado." if cancelled else "Nenhum despertador correspondente encontrado."

        if action == "clock_alarm_snooze":
            dur = params.get("duration", 600.0)
            job = await self.scheduler.async_snooze_job(job_type="alarm", duration_seconds=dur)
            mins = int(dur // 60)
            return f"Despertador adiado por {mins} minutos." if job else "Não há despertador tocando para adiar."

        if action == "clock_alarm_dismiss":
            job = await self.scheduler.async_dismiss_job(job_type="alarm")
            return "Despertador desligado." if job else "Não há despertador tocando."

        if action == "reminder_create":
            title = params.get("title") or "lembrete"
            job = await self.scheduler.async_add_job(
                job_type="reminder",
                target_action="reminder",
                delay_seconds=params.get("delay"),
                time_str=params.get("time"),
                date_str=params.get("date"),
                title=title,
            )
            return f"Lembrete criado: {title}."

        if action == "reminder_list":
            jobs = self.scheduler.list_jobs(job_type="reminder")
            if not jobs:
                return "Você não tem lembretes pendentes."
            titles = [j.title or "sem título" for j in jobs]
            return f"Você tem {len(jobs)} lembrete{'s' if len(jobs) > 1 else ''}: " + ", ".join(titles) + "."

        if action == "reminder_cancel":
            cancelled = await self.scheduler.async_cancel_jobs(
                job_type="reminder",
                name=params.get("title"),
            )
            return "Lembrete cancelado." if cancelled else "Nenhum lembrete correspondente encontrado."

        if action == "timer_start":
            dur = params.get("duration") or params.get("value") or 0.0
            job = await self.scheduler.async_add_job(
                job_type="timer",
                target_action="timer",
                delay_seconds=dur,
                name=params.get("name"),
            )
            mins = int(dur // 60) if dur >= 60 else int(dur)
            unit = "minutos" if dur >= 60 else "segundos"
            name_str = f" para {job.name}" if job.name else ""
            return f"Timer de {mins} {unit}{name_str} iniciado."

        if action == "timer_list":
            jobs = self.scheduler.list_jobs(job_type="timer")
            if not jobs:
                return "Não há timers ativos no momento."
            now_ts = time.time()
            details = []
            for j in jobs:
                rem = max(0, int(j.due_timestamp - now_ts))
                m, s = divmod(rem, 60)
                time_str = f"{m}m {s}s" if m else f"{s}s"
                details.append((f"{j.name}: " if j.name else "") + time_str)
            return "Timers ativos: " + ", ".join(details) + "."

        if action == "timer_query":
            jobs = self.scheduler.list_jobs(job_type="timer")
            name = params.get("name")
            if name:
                jobs = [j for j in jobs if j.name and j.name.lower() == name.lower()]
            if not jobs:
                return "Não há nenhum timer ativo no momento."
            rem = max(0, int(jobs[0].due_timestamp - time.time()))
            m, s = divmod(rem, 60)
            time_str = f"{m} minutos e {s} segundos" if m else f"{s} segundos"
            return f"Faltam {time_str} no timer."

        if action == "timer_change":
            delta = params.get("relative", 0.0)
            job = await self.scheduler.async_change_timer(name=params.get("name"), delta_seconds=delta)
            if job:
                return "Tempo do timer ajustado."
            return "Nenhum timer correspondente encontrado para alterar."

        if action == "timer_cancel":
            all_timers = op.intent == "timer.cancel_all"
            cancelled = await self.scheduler.async_cancel_jobs(
                job_type="timer",
                name=params.get("name"),
                all_of_type=all_timers,
            )
            return "Timer cancelado." if cancelled else "Nenhum timer ativo para cancelar."

        if action == "schedule_action":
            target_action = params.get("target_action", "turn_on")
            targets = [row["entity_id"] for row in item.rows]
            user_id = user_input.context.user_id
            session = self.sessions.get((user_id, request_origin(user_input), user_input.conversation_id))
            job = await self.scheduler.async_add_job(
                job_type="action",
                target_action=target_action,
                targets=targets,
                delay_seconds=params.get("delay"),
                time_str=params.get("time"),
                date_str=params.get("date"),
                parameters=params,
                authority={'user_id': user_id, 'identity_source': session.identity_source if session else 'context',
                    'conversation_id': user_input.conversation_id, 'satellite_id':getattr(user_input,'satellite_id',None),
                    'device_id':getattr(user_input,'device_id',None), 'options':_schedule_options_hash(options),
                    'origin':list(origin_context(self.hass,user_input,options)), 'registry_ids':[row['registry_id'] for row in item.rows]},
            )
            return "Ação agendada com sucesso."

        if action == "scheduled_action_list":
            jobs = self.scheduler.list_jobs(job_type="action")
            if not jobs:
                return "Não há tarefas agendadas."
            return f"Você tem {len(jobs)} ação{'ões' if len(jobs) > 1 else ''} agendada{'s' if len(jobs) > 1 else ''}."

        if action == "scheduled_action_cancel":
            cancelled = await self.scheduler.async_cancel_jobs(
                job_type="action",
                name=params.get("target_action"),
            )
            return "Agendamento cancelado." if cancelled else "Nenhuma tarefa agendada correspondente encontrada."

        return "Comando temporal concluído."


def _options_hash(options: dict) -> str:
    return hashlib.sha256(json.dumps(options, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _schedule_options_hash(options):
    limits={'performance_concurrency','performance_waiting','performance_model_waiting','performance_timeout',
            'performance_scheduler_concurrency','performance_scheduler_timeout','performance_scheduler_jobs','_phase5_supported'}
    return _options_hash({key:value for key,value in options.items() if key not in limits})


def _independent_command(text: str) -> bool:
    if text in ("pode", "pode confirmar", "confirmo", "sim", "isso"):
        return False
    words = text.split()
    while words and words[0] in ("por", "favor", "arandu"):
        words.pop(0)
    return bool(words) and (words[0] in ("liga", "ligue", "ligar", "aciona", "acione", "desliga", "desligue", "desligar", "apaga", "apague", "apagar", "acende", "acenda", "acender", "coloca", "coloque", "colocar", "bota", "deixa", "ajusta", "ajuste", "aumenta", "aumentar", "diminui", "diminuir", "qual", "quanto", "quantas", "quantos", "quais", "onde", "tem", "ativa", "ative", "desativa", "desative", "abre", "abra", "abrir", "fecha", "feche", "fechar", "tranca", "tranque", "trancar", "destranca", "destranque", "destrancar", "finaliza", "acabar", "toca", "toque", "quero", "pode", "poderia", "me") or " qual " in text or " quanto " in text)


def _ttl(options: dict) -> float:
    value = options.get("session_ttl", 60)
    return min(600, max(1, value)) if type(value) in (int, float) else 60


def _input_with_id(user_input: Any, conversation_id: str) -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(text=user_input.text, language=getattr(user_input, "language", "pt-BR"), context=user_input.context,
                           device_id=getattr(user_input, "device_id", None), satellite_id=getattr(user_input, "satellite_id", None), conversation_id=conversation_id)

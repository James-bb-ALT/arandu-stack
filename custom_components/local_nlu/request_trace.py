"""Bounded observational traces. No authority, credentials, audio or disk storage."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import copy
import re
import time
import uuid

CURRENT = ContextVar('arandu_request_trace', default=None)
STAGES = ('wake', 'stt', 'prepare', 'authorization', 'queue', 'nlu', 'decision', 'llm', 'validation', 'ha', 'tts')


def code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,96}', value) else None


def result_status(value):
    if value in ('success', 'query_success'):
        return 'completed'
    if value == 'missing_slot':
        return 'clarification_required'
    if value == 'confirmation_required':
        return 'confirmation_required'
    if value == 'cancelled':
        return 'cancelled'
    return 'error' if value in ('unavailable', 'execution_failed', 'partial_failure') else 'rejected'


def exception_status(error):
    return ('cancelled' if isinstance(error, asyncio.CancelledError) else
            'rejected' if getattr(error, 'code', None) in ('denied', 'stale', 'invalid_request') else 'error')


class RequestTrace:
    def __init__(self, publish=lambda data: None, options=None, origin='unknown'):
        self.publish = publish
        self.clock = time.perf_counter()
        self.running = {}
        self.done = False
        self.include_text = False
        self.data = dict(protocol='arandu-trace-v1', id=uuid.uuid4().hex, sequence=0,
                         started_at=datetime.now(timezone.utc).isoformat(), status='running',
                         origin=origin, result=None, reason=None, total_ms=None, text=None, stages={})
        for name in STAGES:
            self.data['stages'][name] = {'status': 'not_observed' if name in ('wake', 'stt', 'tts')
                                        else 'disabled' if name in ('decision', 'llm') and not (options or {}).get(name + '_enabled')
                                        else 'pending'}

    def emit(self):
        self.data['sequence'] += 1
        try:
            self.publish(copy.deepcopy(self.data))
        except Exception:
            pass  # Observability must never block or change command execution.

    def start(self, name):
        row = self.data['stages'][name]
        self.running[name] = time.perf_counter()
        row['status'] = 'running'
        row.setdefault('offset_ms', (self.running[name] - self.clock) * 1000)
        self.emit()

    def end(self, name, status, reason=None, confidence=None):
        row = self.data['stages'][name]
        started = self.running.pop(name, None)
        if started is not None:
            row['duration_ms'] = row.get('duration_ms', 0) + (time.perf_counter() - started) * 1000
            row['attempts'] = row.get('attempts', 0) + 1
        row['status'] = status
        if code(reason):
            row['reason'] = reason
        if type(confidence) in (int, float) and 0 <= confidence <= 1:
            row['confidence'] = confidence
        self.emit()

    def finish(self, result, text=None, include_text=False):
        if self.done:
            return
        self.done = True
        outcome = code(getattr(result, 'code', None)) or 'unavailable'
        status = result_status(outcome)
        for name in list(self.running):
            self.end(name, status, getattr(result, 'reason', None))
        for name, row in self.data['stages'].items():
            if row['status'] == 'pending':
                row['status'] = 'not_reached' if name in ('decision', 'llm') else 'not_observed'
        self.data.update(status=status, result=outcome, reason=code(getattr(result, 'reason', None)), total_ms=(time.perf_counter() - self.clock) * 1000)
        if include_text and isinstance(text, str):
            self.data['text'] = text[:512]
        self.emit()


class TracePublisher:
    """Latest snapshot per request; small background queue, bounded network time."""
    def __init__(self, hass, endpoint):
        self.hass, self.endpoint = hass, endpoint
        self.pending = {}
        self.task = None
        self.closed = False

    def submit(self, data):
        if self.closed:
            return
        self.pending[data['id']] = data
        while len(self.pending) > 32:
            self.pending.pop(next(iter(self.pending)))
        if self.task is None:
            self.task = asyncio.create_task(self.send())

    async def send(self):
        try:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            from .ai_lab import local_request
            while self.pending:
                key = next(iter(self.pending))
                data = self.pending.pop(key)
                try:
                    async with asyncio.timeout(1.5):
                        await local_request(async_get_clientsession(self.hass), self.endpoint, '/request-trace',
                                            payload=data, timeout=1)
                except Exception:
                    pass
        except Exception:
            self.pending.clear()
        finally:
            self.task = None

    def close(self):
        self.closed = True
        self.pending.clear()
        if self.task:
            self.task.cancel()


@contextmanager
def capture(hass, runtime, options, user_input):
    endpoint = options.get('stack_endpoint', '')
    publisher = getattr(runtime, '_trace_publisher', None)
    if endpoint and (publisher is None or publisher.endpoint != endpoint):
        if publisher:
            publisher.close()
        publisher = runtime._trace_publisher = TracePublisher(hass, endpoint)
    origin = 'satellite' if getattr(user_input, 'satellite_id', None) else 'device' if getattr(user_input, 'device_id', None) else 'unknown'
    trace = RequestTrace(publisher.submit if endpoint else lambda data: None, options, origin)
    token = CURRENT.set(trace)
    trace.emit()
    try:
        yield trace
    finally:
        trace.finish(None)
        CURRENT.reset(token)


async def observe(name, awaitable):
    trace = CURRENT.get()
    if trace is None:
        return await awaitable
    trace.start(name)
    try:
        result = await awaitable
    except BaseException as error:
        trace.end(name, exception_status(error), getattr(error, 'reason', None) or type(error).__name__)
        raise
    if name == 'prepare' and isinstance(result, tuple) and len(result) == 2 and result[1] is not None:
        problem = result[1]
        trace.end(name, result_status(problem.code), getattr(problem, 'reason', None) or problem.code)
        return result
    status = result.get('status') if isinstance(result, dict) else getattr(result, 'status', None)
    trace.end(name, {'plan': 'accepted', 'no_match': 'rejected', 'clarification': 'clarification_required',
                     'unavailable': 'error', 'invalid_request': 'rejected', 'stale': 'rejected'}.get(status, 'completed'),
              result.get('reason') if isinstance(result, dict) else getattr(result, 'reason', None))
    return result


def observe_execution(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        trace = CURRENT.get()
        if trace:
            trace.start('validation')
        try:
            result = await function(*args, **kwargs)
        except BaseException as error:
            if trace:
                for name in ('validation', 'ha'):
                    if name in trace.running:
                        trace.end(name, exception_status(error), getattr(error, 'reason', None) or type(error).__name__)
            raise
        if trace:
            for name in ('validation', 'ha'):
                if name in trace.running:
                    trace.end(name, result_status(result.code), getattr(result, 'reason', None))
        return result
    return wrapped

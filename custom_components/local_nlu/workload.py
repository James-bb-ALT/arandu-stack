"""Event-loop admission and ordered commit locks; no authority or retries."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time


class Saturated(Exception):
    pass


def bounded(options, name, default, low, high):
    value = options.get(name, default)
    return min(high, max(low, value)) if type(value) is int else default


@asynccontextmanager
async def observed_wait(name, manager):
    from .request_trace import CURRENT
    trace = CURRENT.get()
    if trace:
        trace.start(name)
    try:
        async with manager:
            if trace:
                trace.end(name, 'completed')
            yield
    except BaseException as error:
        if trace and name in trace.running:
            trace.end(name, 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error')
        raise


class Circuit:
    """Transport failures only; one half-open probe; no effect retry."""
    def __init__(self, failures=3, cooldown=15):
        self.limit, self.cooldown = failures, cooldown
        self.failures = 0
        self.until = 0
        self.probing = False

    @asynccontextmanager
    async def call(self):
        now = time.monotonic()
        if now < self.until or self.probing:
            raise ConnectionError('circuit_open')
        probe = bool(self.until)
        if probe:
            self.probing = True
        try:
            yield
        except Exception as error:
            if isinstance(error,(ConnectionError,OSError,TimeoutError)) or getattr(error,'code',None) in {'http_502','http_503','http_504'} or type(error).__module__.startswith('aiohttp.'):
                self.failures += 1
                if self.failures >= self.limit:
                    self.until = time.monotonic()+self.cooldown
            raise
        else:
            self.failures = 0
            self.until = 0
        finally:
            if probe:
                self.probing = False


class Admission:
    """Bound total in-flight work, including waiters; asyncio FIFO admission."""
    def __init__(self, concurrency=4, waiting=16):
        self.concurrency = concurrency
        self.waiting = waiting
        self.slots = asyncio.Semaphore(concurrency)
        self.inflight = self.active = self.rejected = self.completed = 0
        self.queue_ms = 0.0

    @asynccontextmanager
    async def enter(self):
        if self.inflight >= self.concurrency + self.waiting:
            self.rejected += 1
            raise Saturated('queue_saturated')
        self.inflight += 1
        started = time.monotonic()
        try:
            async with self.slots:
                self.queue_ms += (time.monotonic() - started) * 1000
                self.active += 1
                try:
                    yield
                    self.completed += 1
                finally:
                    self.active -= 1
        finally:
            self.inflight -= 1

    def status(self):
        return dict(concurrency=self.concurrency, waiting_limit=self.waiting,
                    active=self.active, waiting=self.inflight-self.active,
                    rejected=self.rejected, completed=self.completed, queue_ms=self.queue_ms)


class ResourceLocks:
    """Ref-count locks, sorted acquisition, cleanup also on queued cancellation."""
    def __init__(self):
        self.rows = {}

    @asynccontextmanager
    async def hold(self, resources):
        keys = sorted(set(resources))
        acquired = []
        for key in keys:
            row = self.rows.setdefault(key, [asyncio.Lock(), 0])
            row[1] += 1
        try:
            for key in keys:
                await self.rows[key][0].acquire()
                acquired.append(key)
            yield
        finally:
            for key in reversed(acquired):
                self.rows[key][0].release()
            for key in keys:
                self.rows[key][1] -= 1
                if not self.rows[key][1]:
                    del self.rows[key]

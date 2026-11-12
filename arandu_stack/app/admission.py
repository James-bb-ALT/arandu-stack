"""Bounded local transport admission; no retries or inference threads."""
import asyncio
from contextlib import asynccontextmanager
import time


class Busy(Exception):
    pass


class Gate:
    def __init__(self, concurrent=1, waiting=4):
        self.concurrent, self.waiting = concurrent, waiting
        self.slots = asyncio.Semaphore(concurrent)
        self.inflight = self.active = self.rejected = 0
        self.queue_ms = 0.0

    @asynccontextmanager
    async def enter(self, timeout=45):
        if self.inflight >= self.concurrent+self.waiting:
            self.rejected += 1
            raise Busy('queue_saturated')
        self.inflight += 1
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout), self.slots:
                self.queue_ms += (time.monotonic()-started)*1000
                self.active += 1
                try:
                    yield
                finally:
                    self.active -= 1
        finally:
            self.inflight -= 1

    def status(self):
        return dict(active=self.active, waiting=self.inflight-self.active,
                    concurrent_limit=self.concurrent, waiting_limit=self.waiting,
                    rejected=self.rejected, queue_ms=self.queue_ms)

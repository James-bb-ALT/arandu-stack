"""Persistent storage and lifecycle scheduler for alarms, timers, reminders, and scheduled actions."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any
import uuid
import hashlib
import math
import re
from types import SimpleNamespace
from .request_trace import RequestTrace

from .temporal_parser import calculate_due_datetime, get_now, next_recurrence_datetime

try:
    from homeassistant.helpers.storage import Store
except ImportError:
    class Store:  # type: ignore
        def __init__(self, hass: Any, version: int, key: str) -> None:
            self._data: Any = None
        async def async_load(self) -> Any:
            return self._data
        async def async_save(self, data: Any) -> None:
            self._data = data

try:
    from homeassistant.helpers.event import async_track_point_in_utc_time
except ImportError:
    def async_track_point_in_utc_time(hass: Any, action: Any, point_in_time: datetime) -> Any:  # type: ignore
        now_ts = datetime.now(timezone.utc).timestamp()
        delay = max(0.1, point_in_time.timestamp() - now_ts)
        loop = getattr(hass, "loop", None) or asyncio.get_event_loop()
        handle = loop.call_later(delay, lambda: getattr(hass, "async_create_task", asyncio.create_task)(action(point_in_time)))
        return handle.cancel

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "local_nlu_scheduled_jobs"
MAX_ACTIVE_JOBS = 50


@dataclass
class ScheduledJob:
    id: str
    job_type: str  # "alarm", "reminder", "timer", "action"
    target_action: str
    targets: list[str] = field(default_factory=list)
    due_timestamp: float = 0.0
    due_iso: str = ""
    recurrence: str | None = None
    name: str | None = None
    title: str | None = None
    parameters: dict = field(default_factory=dict)
    status: str = "pending"  # "pending", "completed", "cancelled", "missed", "ringing", "snoozed"
    created_at: float = field(default_factory=time.time)
    origin_area: str | None = None
    authority: dict | None = None
    delivery_status: str | None = None
    dispatch_delay_ms: float | None = None
    delivery_ms: float | None = None
    notification_status: str | None = None
    tts_status: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ScheduledJob:
        if type(data) is not dict or type(data.get('id')) is not str or not re.fullmatch(r'[a-zA-Z0-9-]{1,64}',data['id']):
            raise ValueError('invalid_job')
        if data.get('job_type') not in {'alarm','reminder','timer','action'} or data.get('status') not in {'pending','completed','cancelled','missed','ringing','snoozed','dispatching','interrupted','failed'}:
            raise ValueError('invalid_job_status')
        due=data.get('due_timestamp')
        if type(due) not in (int,float) or not math.isfinite(due) or not 0<=due<=253402300799:
            raise ValueError('invalid_job_deadline')
        if type(data.get('targets',[])) is not list or len(data.get('targets',[]))>64 or any(type(target) is not str or not re.fullmatch(r'[a-z0-9_]+\.[a-z0-9_]+',target) for target in data.get('targets',[])):
            raise ValueError('invalid_job_targets')
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ScheduledJobManager:
    def __init__(self, hass: Any, delivery_handler: Any = None, execution_handler: Any = None, publish_trace=None,
                 *, concurrency=2, deadline=20, max_active=50) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._jobs: dict[str, ScheduledJob] = {}
        self._delivery_handler = delivery_handler
        self._execution_handler = execution_handler
        self._unsub_next: Any = None
        self._lock = asyncio.Lock()
        self._loaded = False
        self._closed = False
        self._tasks = set()
        self.concurrency = max(1, min(4, concurrency))
        self.deadline = max(5, min(60, deadline))
        self.max_active = max(1, min(MAX_ACTIVE_JOBS, max_active))
        self._delivery_slots = asyncio.Semaphore(self.concurrency)
        self._publish_trace = publish_trace or (lambda row:None)

    async def async_load(self) -> None:
        """Load persisted jobs and re-arm timer loop."""
        async with self._lock:
            if self._loaded:
                return
            data = await self._store.async_load()
            if data and isinstance(data, dict) and "jobs" in data:
                if type(data['jobs']) is not list or len(data['jobs'])>2000:
                    raise ValueError('scheduled_storage_limit')
                now_ts = time.time()
                for job_dict in data["jobs"]:
                    try:
                        job = ScheduledJob.from_dict(job_dict)
                        if job.delivery_status == 'dispatching':
                            # An effect may have happened before the crash. Never replay it.
                            job.delivery_status = 'interrupted'
                            if job.status == 'dispatching':
                                job.status = 'interrupted'
                        # Honest bookkeeping: mark past pending jobs as missed if HA was offline
                        if job.status == "pending" and job.due_timestamp < now_ts - 60:
                            if job.recurrence:
                                # Re-schedule next occurrence
                                cur_dt = datetime.fromtimestamp(job.due_timestamp, tz=timezone.utc)
                                nxt = next_recurrence_datetime(cur_dt, job.recurrence)
                                if nxt:
                                    job.due_timestamp = nxt.timestamp()
                                    job.due_iso = nxt.isoformat()
                                    self._jobs[job.id] = job
                                    continue
                            job.status = "missed"
                        self._jobs[job.id] = job
                    except Exception as err:
                        _LOGGER.warning('Invalid scheduled job was not restored')
            self._loaded = True
            self._arm_next_timer()

    async def async_save(self) -> None:
        """Persist jobs to HA Store."""
        # Clean up very old finished jobs (> 7 days)
        cutoff = time.time() - 7 * 86400
        active_and_recent = {
            job_id: job.to_dict()
            for job_id, job in self._jobs.items()
            if job.status in ("pending", "ringing", "snoozed", "dispatching") or job.created_at > cutoff
        }
        active_ids = {job.id for job in self._jobs.values() if job.status in ('pending','ringing','snoozed','dispatching')}
        keep = active_ids | set(list(active_and_recent)[-256:])
        self._jobs = {key:job for key,job in self._jobs.items() if key in keep}
        active_and_recent = {key:value for key,value in active_and_recent.items() if key in keep}
        await self._store.async_save({"jobs": list(active_and_recent.values())})

    def count_active(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in ("pending", "ringing", "snoozed", "dispatching"))

    async def async_add_job(
        self,
        *,
        job_type: str,
        target_action: str,
        targets: list[str] | None = None,
        delay_seconds: float | None = None,
        time_str: str | None = None,
        date_str: str | None = None,
        recurrence: str | None = None,
        name: str | None = None,
        title: str | None = None,
        parameters: dict | None = None,
        origin_area: str | None = None,
        authority: dict | None = None,
    ) -> ScheduledJob:
        await self.async_load()
        if self._closed:
            raise ValueError('scheduler_closed')
        if self.count_active() >= self.max_active:
            raise ValueError("quota_exceeded")

        target_dt = calculate_due_datetime(
            delay_seconds=delay_seconds,
            time_str=time_str,
            date_str=date_str,
            recurrence=recurrence,
        )
        if target_dt is None:
            raise ValueError("invalid_schedule_time")

        job_id = str(uuid.uuid4())[:8]
        job = ScheduledJob(
            id=job_id,
            job_type=job_type,
            target_action=target_action,
            targets=list(targets or []),
            due_timestamp=target_dt.timestamp(),
            due_iso=target_dt.isoformat(),
            recurrence=recurrence,
            name=name,
            title=title,
            parameters=dict(parameters or {}),
            status="pending",
            created_at=time.time(),
            origin_area=origin_area,
            authority=authority,
        )
        async with self._lock:
            if self.count_active() >= self.max_active:
                raise ValueError('quota_exceeded')
            self._jobs[job.id] = job
            await self.async_save()
            self._arm_next_timer()
        return job

    async def async_cancel_jobs(
        self,
        *,
        job_type: str | None = None,
        name: str | None = None,
        time_str: str | None = None,
        all_of_type: bool = False,
    ) -> list[ScheduledJob]:
        await self.async_load()
        cancelled: list[ScheduledJob] = []
        async with self._lock:
            for job in self._jobs.values():
                if job.status not in ("pending", "ringing", "snoozed", "dispatching"):
                    continue
                if job_type and job.job_type != job_type:
                    continue
                candidate = job.title if job.job_type == 'reminder' else job.name
                if name and (not candidate or candidate.lower() != name.lower()):
                    continue
                if time_str and time_str not in job.due_iso:
                    continue
                job.status = "cancelled"
                cancelled.append(job)
                if not all_of_type and (name or time_str):
                    break
            if cancelled:
                await self.async_save()
                self._arm_next_timer()
        return cancelled

    def list_jobs(self, job_type: str | None = None) -> list[ScheduledJob]:
        active = [
            j for j in self._jobs.values()
            if j.status in ("pending", "ringing", "snoozed", "dispatching")
            and (job_type is None or j.job_type == job_type)
        ]
        active.sort(key=lambda j: j.due_timestamp)
        return active

    async def async_snooze_job(self, *, job_type: str = "alarm", duration_seconds: float = 600.0) -> ScheduledJob | None:
        await self.async_load()
        async with self._lock:
            # Look for currently ringing or imminent job of this type
            active = [
                j for j in self._jobs.values()
                if j.job_type == job_type and j.status in ("ringing", "pending")
            ]
            if not active:
                return None
            active.sort(key=lambda j: j.due_timestamp)
            target_job = active[0]
            new_due = time.time() + duration_seconds
            target_job.due_timestamp = new_due
            target_job.due_iso = datetime.fromtimestamp(new_due, tz=timezone.utc).isoformat()
            target_job.status = "snoozed"
            await self.async_save()
            self._arm_next_timer()
            return target_job

    async def async_dismiss_job(self, *, job_type: str = "alarm") -> ScheduledJob | None:
        await self.async_load()
        async with self._lock:
            active = [
                j for j in self._jobs.values()
                if j.job_type == job_type and j.status in ("ringing", "pending", "snoozed")
            ]
            if not active:
                return None
            active.sort(key=lambda j: j.due_timestamp)
            target_job = active[0]
            target_job.status = "completed"
            if target_job.recurrence:
                # Schedule next recurrence
                cur_dt = datetime.fromtimestamp(target_job.due_timestamp, tz=timezone.utc)
                nxt = next_recurrence_datetime(cur_dt, target_job.recurrence)
                if nxt:
                    next_job = ScheduledJob(
                        id=str(uuid.uuid4())[:8],
                        job_type=target_job.job_type,
                        target_action=target_job.target_action,
                        targets=list(target_job.targets),
                        due_timestamp=nxt.timestamp(),
                        due_iso=nxt.isoformat(),
                        recurrence=target_job.recurrence,
                        name=target_job.name,
                        title=target_job.title,
                        parameters=dict(target_job.parameters),
                        status="pending",
                        created_at=time.time(),
                        origin_area=target_job.origin_area,
                    )
                    self._jobs[next_job.id] = next_job
            await self.async_save()
            self._arm_next_timer()
            return target_job

    async def async_change_timer(self, *, name: str | None = None, delta_seconds: float = 0.0) -> ScheduledJob | None:
        await self.async_load()
        async with self._lock:
            timers = [
                j for j in self._jobs.values()
                if j.job_type == "timer" and j.status == "pending"
                and (name is None or (j.name and j.name.lower() == name.lower()))
            ]
            if not timers:
                return None
            timers.sort(key=lambda j: j.due_timestamp)
            target = timers[0]
            target.due_timestamp = max(time.time() + 1.0, target.due_timestamp + delta_seconds)
            target.due_iso = datetime.fromtimestamp(target.due_timestamp, tz=timezone.utc).isoformat()
            await self.async_save()
            self._arm_next_timer()
            return target

    def _arm_next_timer(self) -> None:
        """Find the earliest pending job and set an event timer."""
        if self._unsub_next:
            self._unsub_next()
            self._unsub_next = None
        if self._closed:
            return

        pending = [
            j for j in self._jobs.values()
            if j.status in ("pending", "snoozed")
        ]
        if not pending:
            return

        pending.sort(key=lambda j: j.due_timestamp)
        next_job = pending[0]
        now_ts = time.time()
        delay = max(0.1, next_job.due_timestamp - now_ts)
        target_utc = datetime.fromtimestamp(next_job.due_timestamp, tz=timezone.utc)

        async def _on_timer_fire(_now: Any) -> None:
            await self._async_handle_due_jobs()

        self._unsub_next = async_track_point_in_utc_time(self.hass, _on_timer_fire, target_utc)

    async def _async_handle_due_jobs(self) -> None:
        """Trigger all jobs that are due."""
        async with self._lock:
            if self._closed:
                return
            now_ts = time.time()
            due_jobs = [
                j for j in self._jobs.values()
                if j.status in ("pending", "snoozed") and j.due_timestamp <= now_ts + 1.0
            ]
            available = max(0, self.max_active-len(self._tasks))
            for index, job in enumerate(due_jobs):
                job.status = 'ringing' if job.job_type in ('alarm','reminder','timer') else 'dispatching'
                job.dispatch_delay_ms = max(0, (now_ts-job.due_timestamp)*1000)
                job.delivery_status = 'dispatching' if index < available else 'saturated'
            try:
                # Durable claim BEFORE spawning any side effect; no retry on failure.
                await self.async_save()
            except Exception:
                for job in due_jobs:
                    job.delivery_status = 'storage_error'
                self._arm_next_timer()
                raise
            for job in due_jobs:
                if job.delivery_status == 'dispatching':
                    task = self.hass.async_create_task(self._dispatch(job,job.due_timestamp))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            self._arm_next_timer()

    async def _dispatch(self, job, due_timestamp):
        started = time.monotonic()
        trace = RequestTrace(self._publish_trace)
        trace.data.update(job_ref=hashlib.sha256(job.id.encode()).hexdigest()[:32],job_kind=job.job_type,scheduled_at=job.due_iso)
        trace.start('queue')
        try:
            async with asyncio.timeout(self.deadline), self._delivery_slots:
                trace.end('queue','completed')
                trace.start('scheduler')
                if self._closed or job.status in ('cancelled','snoozed','completed') or job.due_timestamp != due_timestamp:
                    job.delivery_status = 'cancelled'
                    return
                handler = self._execution_handler if job.job_type == 'action' else self._delivery_handler
                if handler is None:
                    job.delivery_status = 'unavailable'
                elif job.job_type == 'action':
                    ok = await handler.async_execute_scheduled_action(job)
                    job.delivery_status = 'delivered' if ok else 'rejected'
                    if job.status != 'cancelled':
                        job.status = 'completed' if ok else 'failed'
                else:
                    delivered = await handler.async_deliver_notification(job)
                    job.delivery_status = 'error' if delivered is False else 'delivered'
        except asyncio.CancelledError:
            job.delivery_status = 'cancelled'
            raise
        except TimeoutError:
            job.delivery_status = 'timeout'
        except Exception:
            job.delivery_status = 'error'
            _LOGGER.warning('Scheduled delivery failed; no automatic retry')
        finally:
            if job.job_type == 'action' and job.status == 'dispatching':
                # An uncertain/failed effect is terminal; never retain a quota slot
                # forever and never make it eligible for an automatic replay.
                job.status = 'interrupted' if job.delivery_status == 'cancelled' else 'failed'
            job.delivery_ms = (time.monotonic()-started)*1000
            if not self._closed:
                async with self._lock:
                    try:
                        await self.async_save()
                    except Exception:
                        job.delivery_status = 'storage_error'
                        _LOGGER.warning('Scheduled completion could not be persisted; no retry')
            trace.finish(SimpleNamespace(code='cancelled' if job.delivery_status=='cancelled' else 'success' if job.delivery_status=='delivered' else 'unavailable',reason=job.delivery_status))

    def diagnostics(self):
        return {'active':self.count_active(),'dispatch_tasks':len(self._tasks),'concurrency':self.concurrency,'task_limit':self.max_active,
            'delivery_counts':{state:sum(job.delivery_status==state for job in self._jobs.values()) for state in ('dispatching','delivered','rejected','interrupted','cancelled','error','timeout','saturated','storage_error')},
            'recent_delays_ms':[job.dispatch_delay_ms for job in list(self._jobs.values())[-50:] if job.dispatch_delay_ms is not None],
            'playback':'not_observed'}

    def close(self) -> None:
        self._closed = True
        for task in tuple(self._tasks):
            task.cancel()
        if self._unsub_next:
            self._unsub_next()
            self._unsub_next = None

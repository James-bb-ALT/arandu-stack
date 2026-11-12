"""HA-simulated race, admission, scheduled recovery and telemetry proofs."""
import asyncio
import copy
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import Client, hass_, input_, operation, plan, add
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.workload import Admission, ResourceLocks, Saturated, Circuit
from custom_components.local_nlu.scheduled_jobs import ScheduledJobManager


class Workload(unittest.IsolatedAsyncioTestCase):
    def runtime(self, response=None, options=None):
        runtime = ContextualRuntime(hass_(),Client(response or plan(operation())), lambda: options or {})
        self.addCleanup(runtime.close)
        return runtime

    async def test_independent_session_passes_slow_interpretation(self):
        runtime = self.runtime()
        entered, release = asyncio.Event(),asyncio.Event()
        async def interpret(payload):
            if payload['text']=='lenta':
                entered.set()
                await release.wait()
            return plan(operation())
        runtime.client.async_interpret_v4=interpret
        slow=asyncio.create_task(runtime.process(input_('lenta',conversation_id='one')))
        await asyncio.wait_for(entered.wait(),1)
        fast=await asyncio.wait_for(runtime.process(input_(conversation_id='two')),1)
        self.assertEqual(fast.code,'success')
        self.assertFalse(slow.done())
        release.set()
        self.assertEqual((await slow).code,'success')

    async def test_concurrent_confirmation_is_single_use_and_bound_to_origin(self):
        runtime=self.runtime(plan(operation('unlock',['reg_lock'])), {'sensitive_entities':['lock.front']})
        add(runtime.hass,'reg_lock','lock.front','Porta',{})
        runtime.hass.services.available.add(('lock','unlock'))
        pending=await runtime.process(input_('Destranca a porta',satellite_id='sat.one'))
        self.assertEqual(pending.code,'confirmation_required')
        wrong=await asyncio.gather(
            runtime.process(input_('Sim',satellite_id='sat.two')),
            runtime.process(input_('Sim',user_id='other',satellite_id='sat.one')))
        self.assertTrue(all(result.code!='success' for result in wrong))
        self.assertEqual(runtime.hass.services.calls,[])
        confirmed=await asyncio.gather(*(
            runtime.process(input_('Sim',satellite_id='sat.one')) for _ in range(2)))
        self.assertEqual(sum(result.code=='success' for result in confirmed),1)
        self.assertEqual(len(runtime.hass.services.calls),1)

    async def test_relative_updates_re_read_under_resource_lock(self):
        runtime=self.runtime(plan(operation('percentage',['reg_fan'],{'relative':1})))
        original=runtime.hass.services.async_call
        values=[]
        async def call(domain,service,data,**kwargs):
            await asyncio.sleep(.01)
            values.append(data['percentage'])
            runtime.hass.states._states['fan.ventilador'].attributes['percentage']=data['percentage']
            await original(domain,service,data,**kwargs)
        runtime.hass.services.async_call=call
        results=await asyncio.gather(*(runtime.process(input_('aumenta o ventilador',conversation_id=str(i))) for i in range(2)))
        self.assertEqual([r.code for r in results],['success','success'])
        self.assertEqual(values,[60,70])
        self.assertEqual(runtime.resource_locks.rows,{})

    async def test_distinct_devices_commit_without_global_lock(self):
        runtime=self.runtime()
        entered,release=asyncio.Event(),asyncio.Event()
        async def interpret(payload):
            return plan(operation(targets=['reg_fan'] if payload['text']=='liga o ventilador' else ['reg_light']))
        runtime.client.async_interpret_v4=interpret
        original=runtime.hass.services.async_call
        async def call(domain,service,data,**kwargs):
            if domain=='light':
                entered.set()
                await release.wait()
            await original(domain,service,data,**kwargs)
        runtime.hass.services.async_call=call
        slow=asyncio.create_task(runtime.process(input_(conversation_id='one')))
        await asyncio.wait_for(entered.wait(),1)
        result=await asyncio.wait_for(runtime.process(input_('liga o ventilador',conversation_id='two')),1)
        self.assertEqual(result.code,'success')
        release.set()
        await slow

    async def test_cancel_one_conversation_does_not_cancel_other_at_same_origin(self):
        runtime=self.runtime()
        entered,release=asyncio.Event(),asyncio.Event()
        async def interpret(payload):
            entered.set()
            await release.wait()
            return plan(operation())
        runtime.client.async_interpret_v4=interpret
        first=asyncio.create_task(runtime.process(input_(conversation_id='one')))
        await entered.wait()
        other=asyncio.create_task(runtime.process(input_(conversation_id='two')))
        await asyncio.sleep(.01)
        self.assertEqual((await runtime.process(input_('cancela',conversation_id='one'))).code,'cancelled')
        self.assertEqual((await first).code,'cancelled')
        self.assertFalse(other.done())
        release.set()
        self.assertEqual((await other).code,'success')
        self.assertEqual(runtime.cancelled_tasks,set())

    async def test_saturation_explicit_and_timeout_cleans_every_lock(self):
        runtime=self.runtime(options={'performance_concurrency':1,'performance_waiting':0})
        entered=asyncio.Event()
        async def interpret(payload):
            entered.set()
            await asyncio.Event().wait()
        runtime.client.async_interpret_v4=interpret
        runtime.total_timeout=.05
        task=asyncio.create_task(runtime.process(input_()))
        await entered.wait()
        rejected=await runtime.process(input_(conversation_id='other'))
        self.assertEqual(rejected.reason,'queue_saturated')
        self.assertEqual((await task).reason,'request_timeout')
        self.assertEqual(runtime.admission.inflight,0)
        self.assertEqual(runtime.session_locks.rows,{})
        self.assertEqual(runtime.active_requests,{})
        self.assertEqual(runtime.hass.services.calls,[])

    async def test_ordered_lock_cancellation_releases_references(self):
        locks=ResourceLocks()
        async with locks.hold(['b']):
            async def wait():
                async with locks.hold(['b','a']):
                    self.fail('cancelled waiter entered')
            task=asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(locks.rows,{})

    async def test_transport_saturation_never_amplifies_load_with_llm(self):
        from custom_components.local_nlu.ai_lab import StageError
        from custom_components.local_nlu.contextual_catalog import build
        from custom_components.local_nlu.intelligence import fallback
        runtime=self.runtime()
        snapshot=build(runtime.hass,runtime.hass.auth.user,{})
        for failure in (Saturated('queue_saturated'),StageError('http_429','inference','busy')):
            request=AsyncMock(side_effect=failure)
            proposed,trace=await fallback('Liga o ventilador',snapshot,
                {'decision_enabled':True,'llm_enabled':True},request)
            self.assertEqual(proposed,())
            self.assertEqual(request.await_count,1)
            self.assertEqual(trace['decision']['reason'],'queue_saturated')
            self.assertNotIn('llm',trace)

    async def test_circuit_recovery_one_probe_and_no_retries(self):
        circuit=Circuit(failures=1,cooldown=.01)
        with self.assertRaises(ConnectionError):
            async with circuit.call():
                raise ConnectionError()
        with self.assertRaises(ConnectionError):
            async with circuit.call():
                self.fail('open circuit entered')
        await asyncio.sleep(.02)
        async with circuit.call():
            with self.assertRaises(ConnectionError):
                async with circuit.call():
                    self.fail('second half-open probe')
        self.assertEqual(circuit.failures,0)

    async def test_scheduler_due_during_nlu_and_duplicate_callbacks(self):
        runtime=self.runtime()
        runtime.hass.async_create_task=asyncio.create_task
        runtime.delivery_handler.async_deliver_notification=AsyncMock()
        entered,release=asyncio.Event(),asyncio.Event()
        async def interpret(payload):
            entered.set()
            await release.wait()
            return plan(operation())
        runtime.client.async_interpret_v4=interpret
        task=asyncio.create_task(runtime.process(input_()))
        await entered.wait()
        jobs=[await runtime.scheduler.async_add_job(job_type='timer',target_action='timer',delay_seconds=10,name=name) for name in ('pizza','bolo')]
        for job in jobs:
            job.due_timestamp=time.time()
        await asyncio.gather(runtime.scheduler._async_handle_due_jobs(),runtime.scheduler._async_handle_due_jobs())
        await asyncio.gather(*tuple(runtime.scheduler._tasks))
        self.assertFalse(task.done())
        self.assertEqual(runtime.delivery_handler.async_deliver_notification.await_count,2)
        self.assertTrue(all(job.delivery_status=='delivered' for job in jobs))
        release.set()
        await task

    async def test_storage_failure_never_dispatches(self):
        runtime=self.runtime()
        runtime.hass.async_create_task=asyncio.create_task
        runtime.delivery_handler.async_deliver_notification=AsyncMock()
        job=await runtime.scheduler.async_add_job(job_type='timer',target_action='timer',delay_seconds=10)
        job.due_timestamp=time.time()
        runtime.scheduler._store.async_save=AsyncMock(side_effect=OSError())
        with self.assertRaises(OSError):
            await runtime.scheduler._async_handle_due_jobs()
        self.assertEqual(runtime.scheduler._tasks,set())
        runtime.delivery_handler.async_deliver_notification.assert_not_awaited()

    async def test_scheduled_action_revalidates_permission_and_legacy_is_refused(self):
        runtime=self.runtime(plan(operation('schedule_action',['reg_fan'],{'delay':100,'target_action':'turn_on'})))
        result=await runtime.process(input_('liga o ventilador'))
        self.assertEqual(result.code,'success')
        job=runtime.scheduler.list_jobs('action')[0]
        self.assertIsNotNone(job.authority)
        self.assertTrue(await runtime.execution_handler.async_execute_scheduled_action(job))
        self.assertEqual(len(runtime.hass.services.calls),1)
        runtime.hass.auth.user.is_active=False
        with self.assertRaises(Exception):
            await runtime.execution_handler.async_execute_scheduled_action(job)
        self.assertEqual(len(runtime.hass.services.calls),1)
        job.authority=None
        self.assertFalse(await runtime.execution_handler.async_execute_scheduled_action(job))

    async def test_restart_does_not_replay_an_uncertain_claim(self):
        runtime=self.runtime()
        job=await runtime.scheduler.async_add_job(job_type='action',target_action='turn_on',delay_seconds=10,targets=['fan.ventilador'])
        job.status='dispatching';job.delivery_status='dispatching'
        persisted={'jobs':[job.to_dict()]}
        runtime.scheduler.close()
        restored=ScheduledJobManager(runtime.hass)
        self.addCleanup(restored.close)
        restored._store.async_load=AsyncMock(return_value=persisted)
        await restored.async_load()
        self.assertEqual(restored._jobs[job.id].delivery_status,'interrupted')
        self.assertEqual(restored._jobs[job.id].status,'interrupted')
        await restored._async_handle_due_jobs()
        self.assertEqual(restored._tasks,set())

    async def test_action_timeout_releases_quota_without_replaying(self):
        runtime=self.runtime()
        scheduler=runtime.scheduler
        scheduler.deadline=.01
        async def blocked(job):
            await asyncio.sleep(10)
        scheduler._execution_handler=SimpleNamespace(async_execute_scheduled_action=AsyncMock(side_effect=blocked))
        job=await scheduler.async_add_job(job_type='action',target_action='turn_on',delay_seconds=10,targets=['fan.ventilador'])
        job.status='dispatching';job.delivery_status='dispatching'
        await scheduler._dispatch(job,job.due_timestamp)
        self.assertEqual(job.status,'failed')
        self.assertEqual(job.delivery_status,'timeout')
        self.assertEqual(scheduler.count_active(),0)
        job.due_timestamp=time.time()-1
        await scheduler._async_handle_due_jobs()
        self.assertEqual(scheduler._tasks,set())
        self.assertEqual(scheduler._execution_handler.async_execute_scheduled_action.await_count,1)

    async def test_restart_arms_pending_jobs_before_any_voice_request(self):
        runtime=self.runtime()
        job=await runtime.scheduler.async_add_job(job_type='timer',target_action='timer',delay_seconds=60,name='pizza')
        restored=ScheduledJobManager(runtime.hass)
        self.addCleanup(restored.close)
        restored._store.async_load=AsyncMock(return_value={'jobs':[job.to_dict()]})
        await restored.async_load()
        self.assertIsNotNone(restored._unsub_next)
        restored.close()
        self.assertIsNone(restored._unsub_next)
        await restored._async_handle_due_jobs()
        self.assertEqual(restored._tasks,set())

    async def test_named_cancel_cannot_consume_unnamed_or_other_timer(self):
        runtime=self.runtime()
        await runtime.scheduler.async_add_job(job_type='timer',target_action='timer',delay_seconds=60)
        await runtime.scheduler.async_add_job(job_type='timer',target_action='timer',delay_seconds=60,name='pizza')
        self.assertEqual(await runtime.scheduler.async_cancel_jobs(job_type='timer',name='bolo'),[])
        self.assertEqual(len(runtime.scheduler.list_jobs('timer')),2)
        cancelled=await runtime.scheduler.async_cancel_jobs(job_type='timer',name='pizza')
        self.assertEqual([job.name for job in cancelled],['pizza'])

    async def test_repeated_work_releases_all_request_and_resource_bookkeeping(self):
        runtime=self.runtime()
        for batch in range(5):
            results=await asyncio.gather(*(runtime.process(input_(conversation_id=f'{batch}-{i}')) for i in range(12)))
            self.assertTrue(all(result.code=='success' for result in results))
        self.assertEqual(runtime.active_requests,{})
        self.assertEqual(runtime.queued_requests,{})
        self.assertEqual(runtime.cancelled_tasks,set())
        self.assertEqual(runtime.session_locks.rows,{})
        self.assertEqual(runtime.resource_locks.rows,{})
        self.assertEqual(runtime.nlu_admission.inflight,0)
        self.assertLessEqual(len(runtime.sessions),128)

    async def test_delay_and_exclusion_are_not_erased_from_an_immediate_plan(self):
        runtime=self.runtime()
        for text in ['Liga a luz daqui a 15 minutos','Liga a luz depois de dez minutos','Não liga a luz','Desliga tudo, menos a TV']:
            result=await runtime.process(input_(text))
            self.assertNotEqual(result.code,'success')
        self.assertEqual(runtime.hass.services.calls,[])

    async def test_typed_delay_must_match_text(self):
        runtime=self.runtime(plan(operation('schedule_action',['reg_fan'],{'delay':600,'target_action':'turn_on'})))
        self.assertNotEqual((await runtime.process(input_('liga o ventilador daqui a 15 minutos'))).code,'success')
        self.assertEqual(runtime.scheduler.list_jobs('action'),[])
        runtime.client.response=plan(operation('schedule_action',['reg_fan'],{'delay':900,'target_action':'turn_on'}))
        self.assertEqual((await runtime.process(input_('liga o ventilador depois de quinze minutos',conversation_id='other'))).code,'success')

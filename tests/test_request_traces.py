"""FIXTURE_TECNICA: trace real execution seams; no model, HAOS or satellite."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'arandu_stack/app'))
from request_traces import TraceStore
from custom_components.local_nlu.request_trace import CURRENT, RequestTrace, TracePublisher, observe
import test_intelligence_routing as routing
from test_intelligence_routing import operation, plan, input_
from test_control import control
from aiohttp.test_utils import TestClient, TestServer


def fixture():
    trace = RequestTrace(options={'decision_enabled': True, 'llm_enabled': True})
    trace.start('nlu')
    trace.end('nlu', 'accepted')
    trace.finish(SimpleNamespace(code='success'))
    return trace.data


class Store(unittest.TestCase):
    def test_bounded_ordered_snapshots_and_no_text_by_default(self):
        store = TraceStore()
        first = fixture(); first['text'] = 'FIXTURE_PRIVATE'
        store.ingest(first)
        newer = copy.deepcopy(first); newer['sequence'] += 1; newer['result'] = 'query_success'
        self.assertTrue(store.ingest(newer))
        self.assertFalse(store.ingest(first))
        self.assertEqual(store.view()['requests'][0]['result'], 'query_success')
        self.assertNotIn('FIXTURE_PRIVATE', json.dumps(store.view()))
        for _ in range(55):
            store.ingest(fixture())
        self.assertEqual(len(store.view()['requests']), 50)
        self.assertNotIn(first['id'], store.rows)

    def test_text_opt_in_redaction_and_purge(self):
        store = TraceStore(lambda value: value.replace('FIXTURE_SECRET', '[OCULTO]'))
        store.configure(True)
        row = fixture(); row['text'] = 'FIXTURE_SECRET'
        store.ingest(row)
        self.assertEqual(store.view()['requests'][0]['text'], '[OCULTO]')
        store.configure(False)
        self.assertIsNone(store.view()['requests'][0]['text'])

    def test_schema_rejects_credentials_nan_and_unbounded_content(self):
        for change in ('extra', 'nan', 'reason', 'text', 'stage'):
            row = fixture()
            if change == 'extra': row['token'] = 'FIXTURE_SECRET'
            elif change == 'nan': row['stages']['nlu']['duration_ms'] = float('nan')
            elif change == 'reason': row['stages']['nlu']['reason'] = 'http://user:secret@fixture'
            elif change == 'text': row['text'] = 'x' * 513
            else: row['stages']['nlu']['audio'] = 'bytes'
            with self.subTest(change=change), self.assertRaises(ValueError):
                TraceStore().ingest(row)


class RuntimeTraces(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = routing.Routing.asyncSetUp
    infer = routing.Routing.infer
    policy = routing.Routing.policy
    async def test_traced_nlu_short_circuits_both_models(self):
        self.policy(True, True)
        self.client.response = plan(operation())
        events = []
        trace = RequestTrace(events.append, self.options)
        token = CURRENT.set(trace)
        try:
            result = await self.runtime.process(input_())
            trace.finish(result)
        finally:
            CURRENT.reset(token)
        self.assertEqual(result.code, 'success')
        self.assertEqual(self.calls, [])
        self.assertEqual(trace.data['stages']['nlu']['status'], 'accepted')
        self.assertEqual(trace.data['stages']['ha']['status'], 'completed')
        self.assertEqual(trace.data['stages']['llm']['status'], 'not_reached')
        self.assertTrue(any(event['stages']['nlu']['status'] == 'running' for event in events))
        self.assertTrue(TraceStore().ingest(trace.data))

    async def test_rejected_decision_then_llm_is_one_trace(self):
        self.policy(True, True)
        self.decision_status = 'uncalibrated'
        trace = RequestTrace(options=self.options)
        token = CURRENT.set(trace)
        try:
            result = await self.runtime.process(input_())
            trace.finish(result)
        finally:
            CURRENT.reset(token)
        self.assertEqual(self.calls, ['decision', 'llm'])
        self.assertEqual(trace.data['stages']['decision']['status'], 'uncalibrated')
        self.assertEqual(trace.data['stages']['llm']['status'], 'accepted')
        self.assertEqual(trace.data['stages']['tts']['status'], 'not_observed')
        for name in ('nlu', 'decision', 'llm', 'validation', 'ha'):
            self.assertGreaterEqual(trace.data['stages'][name]['duration_ms'], 0)
        self.assertTrue(TraceStore().ingest(trace.data))

    async def test_invalid_nlu_plan_is_an_error_not_an_accepted_stage(self):
        self.policy(True, True)
        self.client.response = {'version':4,'status':'plan','operations':[]}
        trace = RequestTrace(options=self.options); token = CURRENT.set(trace)
        try:
            result = await self.runtime.process(input_()); trace.finish(result)
        finally: CURRENT.reset(token)
        self.assertEqual(result.code, 'unavailable')
        self.assertEqual(trace.data['stages']['nlu']['status'], 'error')
        self.assertFalse(self.hass.services.calls)

    async def test_accepted_decision_skips_llm_and_clarification_is_distinct(self):
        self.policy(True, True)
        trace = RequestTrace(options=self.options); token = CURRENT.set(trace)
        try:
            result = await self.runtime.process(input_()); trace.finish(result)
        finally: CURRENT.reset(token)
        self.assertEqual(self.calls, ['decision'])
        self.assertEqual(trace.data['stages']['decision']['status'], 'accepted')
        self.assertEqual(trace.data['stages']['decision']['confidence'], .9)
        self.assertEqual(trace.data['stages']['llm']['status'], 'not_reached')
        trace = RequestTrace(); trace.finish(SimpleNamespace(code='missing_slot'))
        self.assertEqual(trace.data['status'], 'clarification_required')

    async def test_prepare_failure_and_permission_rejection_are_visible(self):
        trace = RequestTrace(); token = CURRENT.set(trace)
        try:
            async def prepare():
                return None, SimpleNamespace(code='unavailable', reason='backend_unavailable')
            await observe('prepare', prepare())
            self.assertEqual(trace.data['stages']['prepare']['status'], 'error')
            result = await self.runtime.process(input_(user_id=None)); trace.finish(result)
        finally: CURRENT.reset(token)
        self.assertEqual(result.code, 'denied')
        self.assertEqual(trace.data['stages']['authorization']['status'], 'rejected')
        self.assertFalse(self.hass.services.calls)


class Delivery(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_contexts_do_not_mix_and_cancel_is_visible(self):
        async def run(outcome):
            trace = RequestTrace()
            token = CURRENT.set(trace)
            try:
                async def backend():
                    await asyncio.sleep(0)
                    return {'status': outcome}
                await observe('nlu', backend())
                trace.finish(SimpleNamespace(code='success' if outcome == 'plan' else 'no_match'))
                return trace.data
            finally:
                CURRENT.reset(token)
        first, second = await asyncio.gather(run('plan'), run('no_match'))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['stages']['nlu']['status'], 'accepted')
        self.assertEqual(second['stages']['nlu']['status'], 'rejected')
        trace = RequestTrace(); token = CURRENT.set(trace)
        try:
            async def cancelled(): raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError): await observe('llm', cancelled())
            self.assertEqual(trace.data['stages']['llm']['status'], 'cancelled')
        finally: CURRENT.reset(token)

    async def test_publisher_coalesces_and_failure_does_not_escape(self):
        helper = ModuleType('homeassistant.helpers.aiohttp_client')
        helper.async_get_clientsession = lambda hass: None
        received = []
        async def send(*args, **kwargs):
            received.append(kwargs['payload'])
            raise ConnectionError('FIXTURE_TECHNICAL_FAILURE')
        with patch.dict(sys.modules, {'homeassistant.helpers.aiohttp_client': helper}), \
                patch('custom_components.local_nlu.ai_lab.local_request', send):
            publisher = TracePublisher(None, 'http://127.0.0.1:8770')
            first = fixture(); last = copy.deepcopy(first); last['sequence'] += 1
            publisher.submit(first); publisher.submit(last)
            await publisher.task
            self.assertEqual(received, [last])
            self.assertFalse(publisher.pending)
            publisher.close()
            publisher.submit(first)
            self.assertFalse(publisher.pending)

    async def test_ingress_controls_and_internal_telemetry_boundary(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control.LogStore, 'configure_logging'):
            app = control.create_app(Path(directory), local=True)
            app.on_startup.clear(); app.on_cleanup.clear()
            async with TestClient(TestServer(app)) as client:
                host = {'Host': '127.0.0.1:8770'}
                state = await (await client.get('/api/state', headers=host)).json()
                response = await client.post('/request-trace', json=fixture(), headers=host)
                self.assertEqual(response.status, 403)
                internal = {**host, 'X-Arandu-Lab': '1'}
                response = await client.post('/request-trace', json=fixture(), headers={**internal, 'Origin': 'http://fixture'})
                self.assertEqual(response.status, 403)
                self.assertEqual((await client.post('/request-trace', json=fixture(), headers=internal)).status, 200)
                response = await client.post('/api/traces/options', json={'include_text': True}, headers=host)
                self.assertEqual(response.status, 403)
                csrf = {**host, 'X-Arandu-CSRF': state['csrf']}
                self.assertEqual((await client.post('/api/traces/options', json={'include_text': True}, headers=csrf)).status, 200)
                payload = await (await client.get('/api/traces', headers=host)).json()
                self.assertTrue(payload['include_text'])
                self.assertEqual(len(payload['requests']), 1)
                self.assertEqual((await client.post('/api/traces/clear', json={}, headers=csrf)).status, 200)
                payload = await (await client.get('/api/traces', headers=host)).json()
                self.assertFalse(payload['requests'])

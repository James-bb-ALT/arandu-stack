import asyncio
from contextlib import suppress
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop
from wyoming.error import Error
from wyoming.event import async_read_event, async_write_event
from wyoming.info import Describe, Info
from wyoming.server import AsyncServer

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from contextual_lexical import ContextualResolver, LexicalError
from home_assistant_context import refresh_loop
from inventory_sync import build_inventory, HAWebSocket, sync_inventory
from server import Engine, Handler, Metrics


class FakeEngine:
    def __init__(self, text):
        self.text = text

    def recognize(self, audio):
        return self.text, .01, 0.0


class MemoryMetrics:
    def __init__(self):
        self.entries = []

    def log(self, **entry):
        self.entries.append(entry)


class TestNetworkIntegration(unittest.IsolatedAsyncioTestCase):
    async def request(self, engine, resolver):
        metrics = MemoryMetrics()
        server = AsyncServer.from_uri('tcp://127.0.0.1:0')
        await server.start(partial(Handler, engine, resolver, metrics, 0, 'full'))
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            await async_write_event(Describe().event(), writer)
            info = await asyncio.wait_for(async_read_event(reader), 3)
            self.assertEqual(Info.from_event(info).asr[0].version, '2.2.0')
            await async_write_event(Transcribe().event(), writer)
            await async_write_event(AudioChunk(rate=16000, width=2, channels=1,
                                               audio=np.zeros(16000, dtype='<i2').tobytes()).event(), writer)
            await async_write_event(AudioStop().event(), writer)
            response = await asyncio.wait_for(async_read_event(reader), 3)
            await asyncio.wait_for(reader.read(), 3)
            return response, metrics
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()
            await server._server.wait_closed()

    async def test_real_wyoming_transport_with_new_corrector(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')
        event, metrics = await self.request(FakeEngine('liga o ventulador de rafael'), resolver)
        transcript = Transcript.from_event(event)
        self.assertEqual(transcript.text.casefold(), 'liga o ventilador de rafael')
        self.assertTrue(transcript.context['arandu']['all_tokens_allowed'])
        self.assertIn('fan.ventilador_rafael', transcript.context['arandu']['entity_hints'])
        self.assertEqual(metrics.entries[0]['outcome'], 'ok')

    async def test_low_confidence_metadata_survives_wire(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')
        event, _ = await self.request(FakeEngine('destranca a porda'), resolver)
        transcript = Transcript.from_event(event)
        self.assertEqual(transcript.text, 'destranca a porta')
        self.assertTrue(transcript.context['arandu']['low_confidence'])
        self.assertFalse(transcript.context['arandu']['confidence_is_calibrated_probability'])

    async def test_lexical_failure_sends_error_without_transcript(self):
        resolver = Mock()
        resolver.apply.side_effect = LexicalError('missing_resource', 'Recurso indisponível')
        event, metrics = await self.request(FakeEngine('liga a luz'), resolver)
        self.assertTrue(Error.is_type(event.type))
        self.assertEqual(Error.from_event(event).code, 'missing_resource')
        self.assertEqual(metrics.entries[0]['outcome'], 'failure')

    async def test_refresh_failure_keeps_stt_available_and_redacts_error(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')
        old = resolver._snapshot
        entered = asyncio.Event()
        async def fetch(_):
            entered.set()
            raise RuntimeError('secret-token-do-not-log')
        with self.assertLogs('arandu.context', level='WARNING') as logs:
            task = asyncio.create_task(refresh_loop(resolver, .001, fetch))
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.sleep(.005)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.assertIs(resolver._snapshot, old)
        self.assertEqual(resolver.apply('liga o ventulador')['text'], 'liga o ventilador')
        self.assertNotIn('secret-token', '\n'.join(logs.output))

    async def test_ws_auth_error_does_not_echo_server_payload(self):
        ws = SimpleNamespace(recv=AsyncMock(side_effect=[json.dumps({'type': 'auth_required'}),
                             json.dumps({'type': 'auth_invalid', 'message': 'secret-token'})]),
                             send=AsyncMock(), close=AsyncMock())
        with patch('inventory_sync.websockets.connect', AsyncMock(return_value=ws)):
            with self.assertRaises(RuntimeError) as error:
                async with HAWebSocket('secret-token'):
                    pass
            self.assertNotIn('secret-token', str(error.exception))
            ws.close.assert_awaited_once()

    async def test_sync_atomic_file_and_optional_floors(self):
        class FakeHA:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            async def call(self, command):
                if command == 'config/floor_registry/list':
                    raise RuntimeError('unsupported')
                if command == 'homeassistant/expose_entity/list':
                    return {'exposed_entities': {}}
                return []
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'secret'}), patch('inventory_sync.HAWebSocket', return_value=FakeHA()):
            path = Path(folder)/'inventory.json'
            await sync_inventory(path)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['floors'], [])
            self.assertFalse(path.with_suffix('.tmp').exists())
            old = path.read_bytes()
            with patch('inventory_sync.HAWebSocket', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):
                    await sync_inventory(path)
            self.assertEqual(path.read_bytes(), old)


class TestContracts(unittest.TestCase):
    def test_int8_loader_and_inference_contract_preserved(self):
        recognizer = Mock()
        stream = SimpleNamespace(accept_waveform=Mock(), result=SimpleNamespace(text='  teste  '))
        recognizer.create_stream.return_value = stream
        factory = Mock(return_value=recognizer)
        sherpa = SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_nemo_ctc=factory))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'sherpa_onnx': sherpa}):
            path = Path(folder)
            with (path/'model.int8.onnx').open('wb') as f:
                f.truncate(10_000_000)
            (path/'tokens.txt').write_text('test', encoding='utf-8')
            engine = Engine(path, 2)
            kwargs = factory.call_args.kwargs
            self.assertEqual(kwargs['provider'], 'cpu')
            self.assertEqual(kwargs['decoding_method'], 'greedy_search')
            self.assertEqual(kwargs['sample_rate'], 16000)
            self.assertEqual(kwargs['feature_dim'], 80)
            audio = np.zeros(16000, dtype=np.float32)
            text, infer, wait = engine.recognize(audio)
            self.assertEqual(text, 'teste')
            stream.accept_waveform.assert_called_once_with(16000, audio)
            recognizer.decode_stream.assert_called_once_with(stream)

    def test_inventory_aliases_floors_and_states_only_automations(self):
        inv = build_inventory([{'area_id': 'a', 'name': 'Sala', 'floor_id': 'f'}], [],
              [{'entity_id': 'light.one', 'aliases': ['Sol'], 'assist_aliases': ['Lua'],
                'area_id': 'a', 'options': {'conversation': {'should_expose': False}}}],
              [{'entity_id': 'light.one', 'attributes': {'friendly_name': 'Luz', 'assist_aliases': ['Estrela']}},
               {'entity_id': 'automation.noite', 'attributes': {'friendly_name': 'Modo noite'}}], {},
              [{'floor_id': 'f', 'name': 'Superior', 'aliases': ['Cima']}])
        self.assertEqual(inv['entities'][0]['floor'], 'Superior')
        self.assertEqual(inv['entities'][0]['assist_aliases'], ['Lua', 'Estrela'])
        self.assertFalse(inv['entities'][0]['exposed_to_assist'])
        self.assertEqual(inv['automations'][0]['name'], 'Modo noite')

    def test_metrics_redact_text_but_record_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'metrics.jsonl'
            metrics = Metrics(path, debug_text=False)
            metrics.log(duration_s=1, inference_s=.1, correction_ms=2, queue_s=0,
                        after_stop_s=.2, raw='private-name', final='private-name', changes=0,
                        padding_ms=0, outcome='ok', lexical={'confidence': .4, 'tokens': [{'original': 'private-name'}]})
            entry = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(entry['lexical']['confidence'], .4)
            self.assertNotIn('private-name', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

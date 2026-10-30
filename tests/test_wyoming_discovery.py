"""Manual regressions; fake sockets only, no audio, models or Supervisor."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'arandu_stack/app'))
from discovery import ready, announce_stack
from wyoming_gateway import WyomingGateway, read_frame


class Discovery(unittest.IsolatedAsyncioTestCase):
    async def test_ready_accepts_wyoming_length_framed_tts_info(self):
        data = json.dumps({'tts': [{'name': 'fixture'}]}).encode()
        reader = AsyncMock()
        reader.readline.return_value = json.dumps({'type': 'info', 'data_length': len(data)}).encode() + b'\n'
        reader.readexactly.return_value = data
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        with patch('wyoming_gateway.asyncio.open_connection', AsyncMock(return_value=(reader, writer))):
            self.assertTrue(await ready(10212, 'tts'))
        writer.write.assert_called_once_with(b'{"type":"describe"}\n')
        writer.close.assert_called_once()

    async def test_does_not_announce_wrong_service(self):
        reader = AsyncMock()
        reader.readline.return_value = b'{"type":"info","data":{"asr":[{}]}}\n'
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        with patch('wyoming_gateway.asyncio.open_connection', AsyncMock(return_value=(reader, writer))):
            self.assertFalse(await ready(10212, 'tts'))

    async def test_local_mode_never_calls_supervisor(self):
        with patch.dict('os.environ', {}, clear=True), patch('discovery.ClientSession') as session:
            await announce_stack(Mock(), Mock())
        session.assert_not_called()

    async def test_stt_and_tts_share_the_single_supervisor_discovery_slot(self):
        for services in ({'asr': [{'name': 'stt'}]}, {'asr': [{'name': 'stt'}], 'tts': [{'name': 'tts'}]}):
            client = MagicMock()
            client.__aenter__.return_value = client
            def request(method, url, **kwargs):
                response = MagicMock()
                response.__aenter__.return_value = response
                response.json = AsyncMock(return_value={'result': 'ok', 'data': {'hostname': 'fixture_arandu_stack'}})
                return response
            client.request.side_effect = request
            gateway = Mock(info=AsyncMock(return_value=services), running=lambda key: False)
            with patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'fixture'}), patch('discovery.ClientSession', return_value=client), patch('discovery.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await announce_stack(gateway, Mock())
            posts = [call for call in client.request.call_args_list if call.args[0] == 'POST']
            self.assertEqual(len(posts), 1)
            if posts:
                self.assertEqual(posts[0].kwargs['json'], {'service': 'wyoming', 'config': {'uri': 'tcp://fixture-arandu-stack:10212'}})

    async def test_gateway_combines_only_running_modules(self):
        manager = Mock(processes={'stt': Mock(returncode=None), 'tts': Mock(returncode=None), 'wake': Mock(returncode=1)})
        gateway = WyomingGateway(manager, Mock())
        async def describe(port):
            return {'asr': [{'name': 'stt'}]} if port == 10350 else {'tts': [{'name': 'tts'}]}
        with patch('wyoming_gateway.describe_backend', side_effect=describe) as backend:
            self.assertEqual(await gateway.info(), {'asr': [{'name': 'stt'}], 'tts': [{'name': 'tts'}]})
            self.assertEqual(backend.call_count, 2)

    async def test_frame_preserves_audio_payload_bytes(self):
        reader = asyncio.StreamReader()
        raw = b'{"type":"audio-chunk","data_length":2,"payload_length":4}\n{}\x00\x01\x02\x03'
        reader.feed_data(raw)
        reader.feed_eof()
        kind, data, forwarded = await read_frame(reader)
        self.assertEqual((kind, data, forwarded), ('audio-chunk', {}, raw))

    async def test_partial_info_does_not_register_tts_without_loading_stt(self):
        manager = Mock(processes={'stt': Mock(returncode=None), 'tts': Mock(returncode=None)})
        gateway = WyomingGateway(manager, Mock())
        with patch.object(gateway, 'info', AsyncMock(return_value={'tts': [{'name': 'tts'}]})):
            with self.assertRaisesRegex(ValueError, 'ainda não estão prontos'):
                await gateway.description()


class PackagedDatasets(unittest.TestCase):
    def test_all_lexical_resources_are_present(self):
        root = Path(__file__).parents[1] / 'arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/datasets'
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        import hashlib
        for item in manifest.values():
            self.assertEqual(hashlib.sha256((root / item['file']).read_bytes()).hexdigest(), item['sha256'])
        for name in ('functional_ptbr.txt', 'supplemental_ptbr.txt'):
            self.assertTrue((root / name).read_text(encoding='utf-8').strip())


if __name__ == '__main__':
    unittest.main()

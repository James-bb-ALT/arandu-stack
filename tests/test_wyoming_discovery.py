"""Manual regressions; fake sockets only, no audio, models or Supervisor."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'arandu_stack/app'))
from discovery import ready, announce


class Discovery(unittest.IsolatedAsyncioTestCase):
    async def test_ready_accepts_wyoming_length_framed_tts_info(self):
        data = json.dumps({'tts': [{'name': 'fixture'}]}).encode()
        reader = AsyncMock()
        reader.readline.return_value = json.dumps({'type': 'info', 'data_length': len(data)}).encode() + b'\n'
        reader.readexactly.return_value = data
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        with patch('discovery.asyncio.open_connection', AsyncMock(return_value=(reader, writer))):
            self.assertTrue(await ready(10212, 'tts'))
        writer.write.assert_called_once_with(b'{"type":"describe"}\n')
        writer.close.assert_called_once()

    async def test_does_not_announce_wrong_service(self):
        reader = AsyncMock()
        reader.readline.return_value = b'{"type":"info","data":{"asr":[{}]}}\n'
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        with patch('discovery.asyncio.open_connection', AsyncMock(return_value=(reader, writer))):
            self.assertFalse(await ready(10212, 'tts'))

    async def test_local_mode_never_calls_supervisor(self):
        with patch.dict('os.environ', {}, clear=True), patch('discovery.ClientSession') as session:
            await announce('tts', 10212, Mock(returncode=None), Mock())
        session.assert_not_called()

    async def test_only_tts_publishes_the_single_supervisor_discovery_slot(self):
        for key, port in [('tts', 10212), ('stt', 10350), ('wake', 10400)]:
            client = MagicMock()
            client.__aenter__.return_value = client
            def request(method, url, **kwargs):
                response = MagicMock()
                response.__aenter__.return_value = response
                response.json = AsyncMock(return_value={'result': 'ok', 'data': {'hostname': 'fixture_arandu_stack'}})
                return response
            client.request.side_effect = request
            with patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'fixture'}), patch('discovery.ClientSession', return_value=client), patch('discovery.ready', AsyncMock(return_value=True)):
                await announce(key, port, Mock(returncode=None), Mock())
            posts = [call for call in client.request.call_args_list if call.args[0] == 'POST']
            self.assertEqual(len(posts), 1 if key == 'tts' else 0)
            if posts:
                self.assertEqual(posts[0].kwargs['json'], {'service': 'wyoming', 'config': {'uri': 'tcp://fixture-arandu-stack:10212'}})


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

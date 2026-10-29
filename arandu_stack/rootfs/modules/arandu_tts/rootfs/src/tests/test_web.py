import io
import json
import subprocess
import wave

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from arandu_tts.config import Config
from arandu_tts.engine import PCMChunk
from arandu_tts.voices import catalogue
from arandu_tts.web import convert_reference, create_app


class FakeEngine:
    pocket_loaded = True
    def __init__(self): self.seen = []
    async def stream(self, text, voice):
        self.seen.append((text, voice.name, voice.backend))
        yield PCMChunk(b'\x01\x00' * 24000, 24000, 2, 1)
    def warm_pocket(self): pass
    def prepare_pocket_voice(self, wav, target):
        target.write_bytes(b'fake-profile')


def wav_sample():
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b'\x00\x00' * 24000 * 2)
    return buf.getvalue()


@pytest.fixture
async def cli(tmp_path):
    (tmp_path / 'pt_BR-test.onnx').write_bytes(b'test')
    (tmp_path / 'pt_BR-test.onnx.json').write_text(json.dumps({'language': {'code': 'pt_BR'}}))
    engine = FakeEngine()
    app = create_app(Config(voices_dir=tmp_path, web_token='secret'), engine)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, engine, tmp_path
    finally:
        await client.close()


def auth(method='GET'):
    h={'X-Arandu-Token': 'secret'}
    if method != 'GET': h['X-Arandu-UI']='1'
    return h


@pytest.mark.asyncio
async def test_status_web_and_security(cli):
    client, engine, tmp = cli
    assert (await client.get('/api/status')).status == 401
    r = await client.get('/api/status', headers=auth())
    d = await r.json()
    assert d['piper_available']
    assert {v['id'] for v in d['voices']} == {'pt_BR-test', 'pocket-rafael'}
    assert (await client.post('/api/warmup', headers=auth())).status == 403
    assert (await client.post('/api/warmup', headers=auth('POST'))).status == 200
    assert (await client.get('/')).status == 200


@pytest.mark.asyncio
async def test_generate_both_engines_and_metrics(cli):
    client, engine, _ = cli
    for backend, voice in [('piper','pt_BR-test'), ('pocket','pocket-rafael')]:
        r = await client.post('/api/synthesize', json={'text':'Arandu.', 'engine': backend, 'voice': voice},
                              headers=auth('POST'))
        assert r.status == 200, await r.text()
        assert r.content_type == 'audio/wav'
        assert float(r.headers['X-Arandu-Rtf']) >= 0
        assert float(r.headers['X-Arandu-Audio-Seconds']) == 1
        with wave.open(io.BytesIO(await r.read())) as wav:
            assert wav.getframerate() == 24000
            assert wav.getnframes() == 24000
    assert engine.seen == [('Arandu.','pt_BR-test','piper'),('Arandu.','pocket-rafael','pocket')]
    r = await client.post('/api/synthesize', json={'text':'a', 'engine':'piper', 'voice':'pocket-rafael'},
                          headers=auth('POST'))
    assert r.status == 400


@pytest.mark.asyncio
async def test_voice_upload_and_delete(cli):
    client, engine, tmp = cli
    f = FormData()
    f.add_field('name', 'minha_voz')
    f.add_field('consent', 'true')
    f.add_field('audio', wav_sample(), filename='audio.wav', content_type='audio/wav')
    r = await client.post('/api/voices', data=f, headers=auth('POST'))
    assert r.status == 200, await r.text()
    assert (await r.json())['voice'] == 'pocket-minha_voz'
    assert (tmp / 'pocket' / 'minha_voz.safetensors').exists()
    assert (tmp / 'pocket' / 'minha_voz.wav').exists()
    r = await client.delete('/api/voices/minha_voz', headers=auth('DELETE'))
    assert r.status == 200
    assert 'pocket-minha_voz' not in catalogue(tmp)
    assert not (tmp / 'pocket' / 'minha_voz.wav').exists()


@pytest.mark.asyncio
async def test_consent_required(cli):
    client, engine, tmp = cli
    f = FormData()
    f.add_field('name', 'outra')
    f.add_field('consent', 'false')
    f.add_field('audio', wav_sample(), filename='voice.wav')
    r = await client.post('/api/voices', data=f, headers=auth('POST'))
    assert r.status == 400
    assert 'pocket-outra' not in catalogue(tmp)

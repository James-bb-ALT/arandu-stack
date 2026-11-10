"""Opt-in acoustic integration. Set ARANDU_STT_TEST_DATA and ARANDU_STT_TEST_WAV.

No downloads occur in this suite. The operator supplies installed models and audio.
Synthetic fixtures verify the engine/transport, never human speech accuracy.
"""
import asyncio
import base64
from functools import partial
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'arandu_stack/app'))
from modules import ModuleManager
from stt_lab import STTLab, SOURCE, decode_audio


@unittest.skipUnless(os.environ.get('ARANDU_STT_TEST_DATA') and os.environ.get('ARANDU_STT_TEST_WAV'), 'Opt-in: local models and WAV required')
class RealAcoustics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lab=STTLab(ModuleManager(Path(os.environ['ARANDU_STT_TEST_DATA']),local=True))
        self.audio=Path(os.environ['ARANDU_STT_TEST_WAV']).read_bytes()
        self.models=os.environ.get('ARANDU_STT_TEST_MODELS','whisper_base').split(',')

    async def test_offline_models_transcribe_same_audio_and_release_children(self):
        os.environ['HF_HUB_OFFLINE']='1'
        try:
            result=await self.lab.test({'model':self.models[0],'models':self.models,'audio':base64.b64encode(self.audio).decode()},True)
        finally:
            os.environ.pop('HF_HUB_OFFLINE',None)
        self.assertEqual(len(result['results']),len(self.models))
        for row in result['results']:
            self.assertEqual(row['state'],'ready',row)
            self.assertTrue(row['raw_transcript'])
            self.assertGreater(row['inference_ms'],0)
            self.assertGreater(row['model_load_ms'],0)
            self.assertEqual(row['audio_sha256'],result['audio_sha256'])
        self.assertFalse(self.lab.children)

    async def test_silence_does_not_hallucinate(self):
        output=io.BytesIO()
        with wave.open(output,'wb') as file:
            file.setnchannels(1);file.setsampwidth(2);file.setframerate(16000)
            file.writeframes(b'\0\0'*16000)
        result=await self.lab.test({'model':self.models[0],'audio':base64.b64encode(output.getvalue()).decode()})
        self.assertEqual(result['results'][0]['state'],'ready')
        self.assertEqual(result['results'][0]['final_transcript'],'')

    async def test_real_engine_over_wyoming_keeps_legacy_service_id(self):
        from wyoming.asr import Transcribe, Transcript
        from wyoming.audio import AudioChunk, AudioStop
        from wyoming.event import async_read_event, async_write_event
        from wyoming.info import Describe, Info
        from wyoming.server import AsyncServer
        from contextual_lexical import ContextualResolver
        from model_catalog import location
        spec=importlib.util.spec_from_file_location('arandu_real_stt',SOURCE/'server.py')
        service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
        model=self.models[0]
        engine=service.Engine(location(self.lab.root,model),2,model)
        resolver=ContextualResolver(self.lab.root.parent/'arandu_ha_inventory.json',SOURCE/'resources',overrides={'closed_vocabulary':False})
        metrics=type('MemoryMetrics',(),{'log':lambda self,**kwargs:None})()
        server=AsyncServer.from_uri('tcp://127.0.0.1:0')
        await server.start(partial(service.Handler,engine,resolver,metrics,0,'normalize_only'))
        port=server._server.sockets[0].getsockname()[1]
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        try:
            await async_write_event(Describe().event(),writer)
            info=Info.from_event(await asyncio.wait_for(async_read_event(reader),10))
            self.assertEqual(info.asr[0].models[0].name,'arandu-fastconformer-ptbr-int8')
            pcm,_=await decode_audio(self.audio)
            await async_write_event(Transcribe(name='arandu-fastconformer-ptbr-int8').event(),writer)
            await async_write_event(AudioChunk(rate=16000,width=2,channels=1,audio=pcm).event(),writer)
            await async_write_event(AudioStop().event(),writer)
            response=await asyncio.wait_for(async_read_event(reader),60)
            self.assertTrue(Transcript.is_type(response.type),response)
            self.assertTrue(Transcript.from_event(response).text)
        finally:
            writer.close();await writer.wait_closed()
            await server.stop();await server._server.wait_closed()
            engine.backend.close()

"""TTS PCM/backpressure tests use synthetic frames, not acoustic quality claims."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'arandu_stack/rootfs/modules/arandu_tts/rootfs/src'))
from arandu_tts.config import Config
from arandu_tts.engine import SpeechEngine,PCMChunk
from arandu_tts.voices import VoiceSpec


class Audio(unittest.IsolatedAsyncioTestCase):
    async def test_pcm_order_complete_and_two_voices_not_mixed(self):
        engine=SpeechEngine(Config())
        def stream(text,voice):
            for i in range(30):
                yield PCMChunk(bytes([int(voice.name),i])*100,16000,2,1)
        engine.stream_sync=stream
        async def consume(name):
            chunks=[]
            async for chunk in engine.stream('fixture',VoiceSpec(name,'pt_BR','piper')):
                chunks.append(chunk.data)
                await asyncio.sleep(.001)
            return chunks
        first,second=await asyncio.gather(consume('1'),consume('2'))
        self.assertEqual(first,[bytes([1,i])*100 for i in range(30)])
        self.assertEqual(second,[bytes([2,i])*100 for i in range(30)])
        self.assertEqual(engine.recent['playback'],'not_observed')
        self.assertEqual(engine._native_jobs,0)

    async def test_slow_consumer_bounds_buffers_and_cancel_releases_worker(self):
        engine=SpeechEngine(Config())
        produced=[]
        def stream(text,voice):
            for i in range(10000):
                produced.append(i)
                yield PCMChunk(b'\x01\x00'*2048,16000,2,1)
        engine.stream_sync=stream
        output=engine.stream('fixture',VoiceSpec('1','pt_BR','piper'))
        await anext(output)
        await asyncio.sleep(.05)
        self.assertLessEqual(len(produced),11)
        await output.aclose()
        for _ in range(30):
            if not engine._native_jobs:
                break
            await asyncio.sleep(.01)
        self.assertEqual(engine._native_jobs,0)
        self.assertEqual(engine._requests,0)

    async def test_tts_saturation_is_explicit(self):
        for limit in (0, 4):
            engine=SpeechEngine(Config(queue_limit=limit))
            engine._requests=1+limit
            with self.assertRaisesRegex(RuntimeError,'queue_saturated'):
                await anext(engine.stream('fixture',VoiceSpec('1','pt_BR','piper')))

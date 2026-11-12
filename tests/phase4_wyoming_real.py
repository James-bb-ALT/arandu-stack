"""Opt-in real audio roundtrip on isolated ports; never connects to Home Assistant."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import wave

import numpy as np
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioStart, AudioChunk, AudioStop
from wyoming.event import async_read_event, async_write_event
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice


async def transcribe(port, pcm):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    started = time.perf_counter()
    try:
        await async_write_event(Describe().event(), writer)
        info = Info.from_event(await asyncio.wait_for(async_read_event(reader), 10))
        public_id = info.asr[0].models[0].name
        assert public_id == 'arandu-fastconformer-ptbr-int8'
        await async_write_event(Transcribe(name=public_id).event(), writer)
        await async_write_event(AudioStart(rate=16000, width=2, channels=1).event(), writer)
        for offset in range(0, len(pcm), 4096):
            await async_write_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm[offset:offset+4096]).event(), writer)
        await async_write_event(AudioStop().event(), writer)
        event = await asyncio.wait_for(async_read_event(reader), 60)
        assert event and Transcript.is_type(event.type), event
        text = Transcript.from_event(event).text
        assert text
        return {'port': port, 'public_id': public_id, 'transcript': text,
                'wall_ms': (time.perf_counter()-started)*1000}
    finally:
        writer.close()
        await writer.wait_closed()


async def synthesize(port, text, path):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    started = time.perf_counter()
    first_ms = None
    audio = bytearray()
    spec = None
    chunks = 0
    try:
        await async_write_event(Synthesize(text=text, voice=SynthesizeVoice(name='pt_BR-faber-medium')).event(), writer)
        async with asyncio.timeout(60):
            while event := await async_read_event(reader):
                assert event.type != 'error', event
                if AudioStart.is_type(event.type):
                    spec = AudioStart.from_event(event)
                elif AudioChunk.is_type(event.type):
                    if first_ms is None:
                        first_ms = (time.perf_counter()-started)*1000
                    chunk = AudioChunk.from_event(event)
                    audio.extend(chunk.audio)
                    chunks += 1
                elif AudioStop.is_type(event.type):
                    break
            else:
                raise AssertionError('TTS closed without AudioStop')
        assert spec and spec.width == 2 and spec.channels == 1 and len(audio) > 3200
        values = np.frombuffer(audio, dtype='<i2').astype(np.int32)
        assert np.any(values)
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(spec.channels)
            output.setsampwidth(spec.width)
            output.setframerate(spec.rate)
            output.writeframes(audio)
        return {'port': port, 'chunks': chunks, 'samples': len(values), 'rate': spec.rate,
                'first_protocol_chunk_ms': first_ms, 'wall_ms': (time.perf_counter()-started)*1000,
                'clipped_fraction': float(np.mean(np.abs(values) >= 32767)),
                'pcm_sha256': hashlib.sha256(audio).hexdigest(), 'complete': True,
                'physical_playback': 'not_observed'}
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    root = Path(os.environ['ARANDU_WYOMING_OUTPUT'])
    root.mkdir(parents=True, exist_ok=True)
    wav = Path(os.environ['ARANDU_STT_TEST_WAV'])
    pcm = subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','1',
        '-i',str(wav),'-ac','1','-ar','16000','-f','s16le','pipe:1'])
    base = os.environ.get('ARANDU_DECISION_STACK_URL','http://127.0.0.1:18744')
    status = json.load(urllib.request.urlopen(base+'/api/stt/models'))
    assert status['configured'] == status['active']['model'] == 'orukeet_int8'
    result = {'kind':'REAL_MODELS_ISOLATED_WYOMING','physical_ha_actions':0,
        'input_sha256':hashlib.sha256(wav.read_bytes()).hexdigest(),
        'active_stt':{key:status['active'][key] for key in ('model','backend','compute','public_service_id')},
        'stt':[], 'tts':[]}
    for port in (10354,10244):
        result['stt'].append(await transcribe(port,pcm))
    for index,text in enumerate(('Pronto.','O timer de pizza de quinze minutos terminou. Não desligue a televisão. O lembrete de tomar água está pronto.')):
        result['tts'].append(await synthesize(10244,text,root/f'tts-{index}.wav'))
    (root/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())

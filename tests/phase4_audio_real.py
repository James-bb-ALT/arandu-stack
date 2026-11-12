"""Opt-in real Piper/Orukeet on project-authored synthetic speech; no HA effects."""
import asyncio
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else Path(__file__).parents[1]
OUTPUT=Path(sys.argv[1]);OUTPUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'arandu_stack/rootfs/modules/arandu_tts/rootfs/src'))
from arandu_tts.engine import SpeechEngine
from arandu_tts.config import Config
from arandu_tts.voices import catalogue
import numpy as np
import psutil

TEXTS=['Pronto.','Não liga a luz da sala.','Desliga tudo, exceto a TV.','Coloca o ventilador em cem por cento.',
       'Timer para pizza de quinze minutos.','Acorda às sete da manhã.','Pause a música da sala.',
       'O timer de pizza de quinze minutos terminou. Não desligue a televisão. Aumente menos dez por cento e pause a música da sala.']


async def main():
    voice_dir=Path(os.environ.get('ARANDU_TTS_VOICES',str(ROOT/'arandu_stack/assets/voices')))
    extra={'piper_thread_budget':os.environ.get('ARANDU_PIPER_THREAD_BUDGET')=='1'} if hasattr(Config,'piper_thread_budget') else {}
    engine=SpeechEngine(Config(voices_dir=voice_dir,auto_download=False,preload=False,piper_threads=2,**extra))
    voice=catalogue(engine.cfg.voices_dir,pocket_enabled=False)['pt_BR-faber-medium']
    report={'kind':'REAL_MODELS_SYNTHETIC_SPEECH','hardware':'Ryzen 5700X Windows; not i5/HAOS','voice':voice.name,
            'stt_model':'orukeet_int8','physical_playback':'not_observed','physical_ha_actions':0,'tts':[],'stt':[]}
    before_threads=psutil.Process().num_threads()
    for i,text in enumerate(TEXTS):
        for repeat in range(4 if i in (0,len(TEXTS)-1) else 1):
            cpu=sum(psutil.Process().cpu_times()[:2]);started=time.perf_counter();first=None;chunks=[]
            async for chunk in engine.stream(text,voice):
                if first is None:first=(time.perf_counter()-started)*1000
                chunks.append(chunk)
            elapsed=(time.perf_counter()-started)*1000
            pcm=b''.join(chunk.data for chunk in chunks);rate=chunks[0].rate
            values=np.frombuffer(pcm,dtype='<i2')
            report['tts'].append({'case':i,'repeat':repeat,'first_protocol_chunk_ms':first,'total_ms':elapsed,
                'audio_ms':len(values)/rate*1000,'clipped_fraction':float(np.mean(np.abs(values.astype(np.int32))>=32767)),
                'rss_mb':psutil.Process().memory_info().rss/1048576,'samples':len(values)})
            report['tts'][-1]['pcm_sha256']=hashlib.sha256(pcm).hexdigest()
            report['tts'][-1]['cpu_process_ms']=(sum(psutil.Process().cpu_times()[:2])-cpu)*1000
            if repeat==0:
                with wave.open(str(OUTPUT/f'{i}.wav'),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate);out.writeframes(pcm)
    options=engine._loaded_model.session.get_session_options()
    report['ort']={'intra_threads':options.intra_op_num_threads,'inter_threads':options.inter_op_num_threads,
                   'process_threads_delta':psutil.Process().num_threads()-before_threads,'providers':engine._loaded_model.session.get_providers()}
    del engine;gc.collect()
    source=ROOT/'arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt';sys.path.insert(0,str(source))
    spec=importlib.util.spec_from_file_location('phase4_stt_server',source/'server.py')
    server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
    from stt_processing import evaluate,correct
    from contextual_lexical import ContextualResolver
    resolver=ContextualResolver(OUTPUT/'empty-inventory.json',source/'resources',overrides={'closed_vocabulary':False})
    engine=server.Engine(Path(os.environ['ARANDU_STT_MODEL_PATH']),2,'orukeet_int8')
    report['stt_load_ms']=engine.backend.load_ms
    try:
        for i,text in enumerate(TEXTS):
            audio_path=Path(os.environ.get('ARANDU_STT_AUDIO_SOURCE',str(OUTPUT)))/f'{i}.wav'
            pcm=subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','1','-i',str(audio_path),'-ac','1','-ar','16000','-f','s16le','pipe:1'])
            audio=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
            samples=[];raw=''
            for repeat in range(4):
                cpu=sum(psutil.Process().cpu_times()[:2]);started=time.perf_counter();raw,infer,_=engine.recognize(audio)
                fixed=correct(resolver,raw,'normalize_only')
                samples.append({'wall_ms':(time.perf_counter()-started)*1000,'inference_ms':infer*1000,'correction_ms':fixed['correction_ms'],
                    'cpu_process_ms':(sum(psutil.Process().cpu_times()[:2])-cpu)*1000})
            report['stt'].append({'case':i,'reference':text,'raw':raw,'audio_ms':len(audio)/16,'measurements':samples,
                'audio_sha256':hashlib.sha256(audio_path.read_bytes()).hexdigest(),'profile':'normalize_only','final':fixed['text'],
                'metrics':evaluate(text,raw,fixed['text']),'rss_mb':psutil.Process().memory_info().rss/1048576})
    finally:engine.backend.close()
    (OUTPUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ort':report['ort'],'stt_cases':len(report['stt']),'output':str(OUTPUT/'report.json')}))


if __name__=='__main__':asyncio.run(main())

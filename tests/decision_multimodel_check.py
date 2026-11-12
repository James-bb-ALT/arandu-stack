"""Opt-in real-weight benchmark. Requirement/internal technical fixtures, no HA effects."""
import json
import os
from pathlib import Path
import time
from arandu_decision.adapter import Encoder
from arandu_decision.contracts import MODELS
from arandu_decision.backends import gpu_probe,BackendUnavailable
from decision_v2_check import CASES,catalog,percentiles


def main():
    engine=Encoder(Path(os.environ['ARANDU_DECISION_MODELS']),2);data=catalog()
    output={'representative':False,'label_source':'Approved Phase 2 examples / FIXTURE_TECNICA; not representative speech gold',
        'hardware':'Ryzen 7 5700X / Docker Linux; not i5-6500T','threads':2,'repetitions':3,'warmup':1,
        'gpu_probe':gpu_probe(),'source_overrides':os.environ.get('ARANDU_SOURCE_OVERRIDES')=='1','physical_actions':0,'combinations':{},
        'process_environment':{k:os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_WAIT_POLICY','KMP_BLOCKTIME','NUMEXPR_NUM_THREADS','TOKENIZERS_PARALLELISM')}}
    for key in MODELS:
        for backend in ['cpu','gpu_intel']:
            combo=key+':'+backend
            if backend=='gpu_intel' and not output['gpu_probe']['available']:
                output['combinations'][combo]={'status':'unsupported','reason':output['gpu_probe']['reason'],'gpu_inference':'not_run'};continue
            engine.unload();engine.model_key=key;engine.backend_name=backend
            started=time.perf_counter()
            try:
                engine.load(key,backend);cold=(time.perf_counter()-started)*1000
                reference=None
                if backend=='cpu':
                    import numpy as np
                    texts=[MODELS[key]['prefixes']['query']+'Liga a iluminação',MODELS[key]['prefixes']['query']+'Desliga o ventilador do quarto']
                    expected=engine.model.model.encode(texts,normalize_embeddings=True,batch_size=32,show_progress_bar=False)
                    actual=engine.model.encode(texts)
                    np.testing.assert_allclose(actual,expected,rtol=1e-5,atol=1e-6)
                    reference={'passed':True,'max_abs_error':float(np.abs(actual-expected).max()),'reference':'SentenceTransformer.encode same pinned weights/template'}
                cases=[];measured=[]
                for text,intent,entity,area in CASES:
                    for repetition in range(4):
                        begin=time.perf_counter();result=engine.infer(text,data,key,{'algorithm':'structured-v3','backend':backend},context={'origin_area':area})
                        elapsed=(time.perf_counter()-begin)*1000
                        if repetition:measured.append(elapsed)
                    p=result.get('proposal') or {};pair=(p.get('intent'),(p.get('target_entities') or [None])[0])
                    cases.append({'text':text,'expected_intent':intent,'expected_entity':entity,'correct':pair==(intent,entity),
                        'false_activation':intent is None and bool(p) and not p.get('requires_clarification'),
                        'status':result['decision_status'],'result':result})
                # Force diagnostic embedding ranking on safe positive labels. This is not an execution bypass.
                engine.cache.clear();engine.query_cache.clear();semantic=[]
                for text,intent,entity,area in [c for c in CASES if c[1] is not None]:
                    for repetition in range(4):
                        begin=time.perf_counter()
                        result=engine.infer(text,data,key,{'algorithm':'structured-v3','backend':backend},context={'origin_area':area},semantic_only=True)
                        row={'text':text,'warmup':repetition==0,'cache_mode':'repeated_query','wall_ms':(time.perf_counter()-begin)*1000,**engine.measurements,
                            'ranking_ms':result.get('ranking_ms'),'cache':result.get('cache'),'memory_bytes':result.get('memory_bytes'),'cpu_ms':result.get('cpu_ms'),
                            'top':(result.get('ranking') or [{}])[0],'confidence':result.get('confidence')}
                        row['intent_correct']=row['top'].get('intent')==intent;row['entity_correct']=row['top'].get('entity_id')==entity
                        row['area_correct']=row['top'].get('area_id')==next(e['area_id'] for e in data['entities'] if e['entity_id']==entity)
                        semantic.append(row)
                    for repetition in range(3):
                        engine.query_cache.clear();begin=time.perf_counter()
                        result=engine.infer(text,data,key,{'algorithm':'structured-v3','backend':backend},context={'origin_area':area},semantic_only=True)
                        row={'text':text,'warmup':False,'cache_mode':'query_miss','wall_ms':(time.perf_counter()-begin)*1000,**engine.measurements,
                            'ranking_ms':result.get('ranking_ms'),'cache':result.get('cache'),'memory_bytes':result.get('memory_bytes'),'cpu_ms':result.get('cpu_ms'),
                            'top':(result.get('ranking') or [{}])[0],'confidence':result.get('confidence')}
                        row['intent_correct']=row['top'].get('intent')==intent;row['entity_correct']=row['top'].get('entity_id')==entity
                        row['area_correct']=row['top'].get('area_id')==next(e['area_id'] for e in data['entities'] if e['entity_id']==entity)
                        semantic.append(row)
                warm=[s for s in semantic if not s['warmup']]
                output['combinations'][combo]={'status':'pass','dimension':MODELS[key]['dimension'],'cold_start_ms':cold,
                    'cpu_reference_equivalence':reference,
                    'policy_cases':len(cases),'policy_correct':sum(c['correct'] for c in cases),'false_activations':sum(c['false_activation'] for c in cases),
                    'policy_latency':percentiles(measured),'semantic_warm_latency':percentiles([r['wall_ms'] for r in warm]),
                    'semantic_query_miss_latency':percentiles([r['wall_ms'] for r in warm if r['cache_mode']=='query_miss']),
                    'semantic_query_hit_latency':percentiles([r['wall_ms'] for r in warm if r['cache_mode']=='repeated_query']),
                    'semantic_intent_correct':sum(r['intent_correct'] for r in warm),'semantic_entity_correct':sum(r['entity_correct'] for r in warm),
                    'semantic_area_correct':sum(r['area_correct'] for r in warm),'semantic_samples':len(warm),
                    'max_sampled_rss_bytes':max(r['memory_bytes'] for r in semantic),'cases':cases,'diagnostic_embedding_rows':semantic}
                print(combo,output['combinations'][combo]['policy_correct'],'/',len(cases),flush=True)
            except Exception as exc:
                output['combinations'][combo]={'status':'fail','error':type(exc).__name__+': '+str(exc)}
                print(combo,'FAIL',type(exc).__name__,str(exc),flush=True)
            finally:engine.unload()
            Path(os.environ['ARANDU_DECISION_OUTPUT']).write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    Path(os.environ['ARANDU_DECISION_OUTPUT']).write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


if __name__=='__main__':main()

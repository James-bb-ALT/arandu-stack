"""Real E5 A/B, approved requirement examples. No downloads, HA or calibration gold."""
import json
import os
from pathlib import Path
import tempfile
import time

from arandu_decision.adapter import Encoder


def catalog():
    rows=[]
    for entity,domain,name,aliases,area,actions in [
        ('fan.sala','fan','Ventilador',[],'sala',['turn_on','turn_off','query']),
        ('fan.quarto','fan','Ventilador',[],'quarto',['turn_on','turn_off','query']),
        ('light.sala','light','Luz',['Iluminação'],'sala',['turn_on','turn_off','query']),
        ('light.cozinha','light','Luz',['Iluminação'],'cozinha',['turn_on','turn_off','query']),
        ('switch.tv','switch','TV',['Televisão'],'quarto',['turn_on','turn_off','query']),
        ('switch.tv_sala','switch','TV',['Televisão'],'sala',['turn_on','turn_off','query']),
        ('climate.sala','climate','Ar-condicionado',[],'sala',['turn_on','turn_off','query']),
        ('sensor.speed','sensor','Velocidade do ventilador',[],'sala',['query']),
    ]:
        rows.append({'entity_id':entity,'registry_id':entity.replace('.','_'),'domain':domain,'name':name,
                     'aliases':aliases,'area_id':area,'actions':actions,'attributes':{'available':True}})
    return {'areas':[{'area_id':a,'names':[a.title()]} for a in ['sala','quarto','cozinha']],'entities':rows}


CASES=[
 ('Liga o ventilador da sala','turn_on','fan.sala','sala'),
 ('Apaga a luz da cozinha','turn_off','light.cozinha','sala'),
 ('Desliga a TV do quarto','turn_off','switch.tv','sala'),
 ('Acende a iluminação daqui','turn_on','light.sala','sala'),
 ('Coloca o ventilador pra funcionar','turn_on','fan.sala','sala'),
 ('Quero a luz da sala acesa','turn_on','light.sala','sala'),
 ('Dá pra apagar a luz','turn_off','light.sala','sala'),
 ('Deixa a TV ligada','turn_on','switch.tv_sala','sala'),
 ('Dá uma ligada no ventilador aqui','turn_on','fan.sala','sala'),
 ('Acnde o ventilador','turn_on','fan.sala','sala'),
 ('Deslga a luz','turn_off','light.sala','sala'),
 ('Liga o ventilado do quarto','turn_on','fan.quarto','sala'),
 ('Desliga a televisão da sala','turn_off','switch.tv_sala','quarto'),
 ('Liga o ventilador aqui','turn_on','fan.sala','sala'),
 ('Apaga a luz desse cômodo','turn_off','light.sala','sala'),
 ('Liga a luz de lá',None,None,None),
 ('Liga o ventilador',None,None,None),
 ('Liga a sala',None,None,None),
 ('Liga aquilo',None,None,None),
 ('Desliga o aparelho',None,None,None),
 ('Não liga o ventilador',None,None,'sala'),
 ('Jamais desligue a luz',None,None,'sala'),
 ('Liga tudo, menos a TV',None,None,'sala'),
 ('Se estiver quente, liga o ar',None,None,'sala'),
 ('Deixa a sala mais clara',None,None,'sala'),
 ('Diminui um pouco o ventilador',None,None,'sala'),
 ('Coloca a temperatura em 23 graus',None,None,'sala'),
 ('Fecha a cortina até a metade',None,None,'sala'),
 ('Apaga as luzes da sala e da cozinha',None,None,'sala'),
]


def percentiles(values):
    values=sorted(values)
    return {f'p{p}_ms':values[min(len(values)-1,round((len(values)-1)*p/100))] for p in [50,95,99]}


def main():
    engine=Encoder(Path(os.environ['ARANDU_DECISION_MODELS']),2)
    data=catalog();results={}
    cold=time.perf_counter();engine.load('e5-small');load_ms=(time.perf_counter()-cold)*1000
    for algorithm in ['baseline-v2','structured-v3']:
        engine.cache.clear();engine.query_cache.clear()
        samples=[];latencies=[];correct=0;false_activations=0;resolved=0;rejected=0;rank_correct=0;positive=0
        for text,intent,entity,area in CASES:
            timings=[]
            for _ in range(3):
                started=time.perf_counter()
                result=engine.infer(text,data,'e5-small',{'algorithm':algorithm},context={'origin_area':area})
                timings.append((time.perf_counter()-started)*1000)
            p=result.get('proposal');pair=(p.get('intent'),(p.get('target_entities') or [None])[0]) if p else (None,None)
            correct+=int(pair==(intent,entity))
            false_activations+=int(intent is None and p is not None and not p['requires_clarification'])
            resolved+=int(result['decision_status'] in {'resolved','accepted'})
            rejected+=int(p is None)
            ranked=engine.infer(text,data,'e5-small',{'algorithm':algorithm},context={'origin_area':area},semantic_only=True)
            top=(ranked.get('ranking') or [{}])[0]
            if intent is not None:
                positive+=1
                rank_correct+=int((top.get('intent'),top.get('entity_id'))==(intent,entity))
            latencies.extend(timings[1:])
            samples.append({'text':text,'expected_intent':intent,'expected_entity':entity,'origin_area':area,
                            'decision_status':result['decision_status'],'proposal':p,'reason':result['reason'],
                            'confidence':result.get('confidence'),'ranking_top':top,'cache':result.get('cache'),
                            'timings_ms':timings,'load_ms':result.get('load_ms'),
                            'preparation_ms':result.get('preparation_ms'),'retrieval_ms':result.get('retrieval_ms'),
                            'inference_ms':result.get('inference_ms'),'memory_bytes':result.get('memory_bytes')})
        results[algorithm]={'n':len(CASES),'correct_joint_or_abstention':correct,'false_activations':false_activations,
                            'resolved':resolved,'abstentions':rejected,'positive_ranking_correct':rank_correct,
                            'positive_n':positive,'warm_latency':percentiles(latencies),'samples':samples}
    output={'provenance':'Approved requirements / FIXTURE_TECNICA, internal conformance only',
            'representative':False,'statistical_calibration_validated':False,'hardware':'Ryzen 7 5700X, Docker Linux, not i5-6500T',
            'threads':2,'cold_load_ms':load_ms,'real_e5':True,'ha_actions':0,'algorithms':results}
    # Technical scaling fixture: every entity remains eligible, including the last row.
    large=catalog()
    for i in range(248):
        large['entities'].append({'entity_id':f'light.fixture_{i}','domain':'light','name':f'Luminária auxiliar {i}',
            'aliases':[],'area_id':'sala','actions':['turn_on','turn_off','query'],'attributes':{'available':True}})
    scaling={}
    for algorithm in ['baseline-v2','structured-v3']:
        engine.cache.clear();engine.query_cache.clear();samples=[]
        for _ in range(31):
            begin=time.perf_counter()
            result=engine.infer('Acende a luminária auxiliar 247',large,'e5-small',{'algorithm':algorithm},
                                context={'origin_area':'sala'},semantic_only=True)
            samples.append({'wall_ms':(time.perf_counter()-begin)*1000,'cache':result.get('cache'),
                'memory_bytes':result.get('memory_bytes'),'eligible_pairs':result.get('eligible_pairs'),
                'top':(result.get('ranking') or [{}])[0]})
        scaling[algorithm]={'catalog_entities':len(large['entities']),'cold':samples[0],
            'warm':percentiles([s['wall_ms'] for s in samples[1:]]),'last':samples[-1]}
    cpu=time.process_time();wall=time.perf_counter();time.sleep(1)
    output.update(large_catalog=scaling,resident_idle={'wall_seconds':time.perf_counter()-wall,
        'process_cpu_seconds':time.process_time()-cpu})
    # Exercise the actual calibration implementation with real E5. These numbered
    # strings are infrastructure fixtures, never representative linguistic gold.
    previous_root=engine.root
    with tempfile.TemporaryDirectory() as directory:
        engine.root=Path(directory)
        (engine.root/'e5-small').symlink_to(previous_root/'e5-small',target_is_directory=True)
        legacy=engine.root/'e5-small-calibration.json';legacy.write_text('{"fixture":"preserve"}')
        def labels(split,n):
            return [{'text':f'Liga o ventilador da sala FIXTURE_TECNICA {split} {i}',
                'expected_intent':'turn_on','expected_entities':['fan.sala'],'requires_clarification':False,
                'context':{'origin_area':'sala'}} for i in range(n)]
        calibration=engine.calibrate(data,'e5-small',labels('train',50),labels('validation',30),
            algorithm='structured-v3',provenance={'kind':'technical_fixture','representative':False})
        assert calibration['validated'] is False
        assert legacy.read_text()=='{"fixture":"preserve"}'
        assert (engine.root/'e5-small-structured-v3-calibration.json').is_file()
        output['real_e5_calibration_infrastructure']={'passed':True,'train_n':50,'validation_n':30,
            'legacy_preserved':True,'artifact':calibration}
    engine.root=previous_root
    Path(os.environ['ARANDU_DECISION_OUTPUT']).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:{a:b for a,b in v.items() if a!='samples'} for k,v in results.items()}))
    engine.unload()


if __name__=='__main__':main()

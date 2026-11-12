"""Real model A/B on approved requirement/technical fixtures, never calibration gold."""
import asyncio
import json
import os
from pathlib import Path
import time

from arandu_llm.adapter import Llama
from arandu_llm.service import retrieve


def catalog():
    rows=[]
    for e,d,n,aliases,area,actions in [
        ('light.sala','light','Luz',['Iluminação'],'sala',['turn_on','turn_off','brightness','color','color_temperature','query']),
        ('light.cozinha','light','Luz',[],'cozinha',['turn_on','turn_off','brightness','query']),
        ('climate.sala','climate','Ar',['Ar-condicionado'],'sala',['turn_on','turn_off','temperature','hvac_mode','fan_mode','query']),
        ('fan.sala','fan','Ventilador',[],'sala',['turn_on','turn_off','percentage','oscillate','direction','query']),
        ('switch.tv','switch','TV',['Televisão'],'sala',['turn_on','turn_off','query']),
        ('cover.sala','cover','Persiana',[],'sala',['open','close','position','query']),
        ('media_player.sala','media_player','Som',[],'sala',['play','pause','volume','next','previous','query']),
        ('vacuum.casa','vacuum','Aspirador',[],'sala',['vacuum_start','vacuum_pause','vacuum_dock','query']),
        ('sensor.quarto','sensor','Temperatura',[],'quarto',['query']),
        ('scene.noite','scene','Cena noite',[],'sala',['activate']),
        ('automation.noite','automation','Automação noite',[],'sala',['automation_enable','automation_disable']),
    ]:
        rows.append({'entity_id':e,'registry_id':e.replace('.','_'),'domain':d,'name':n,'aliases':aliases,
            'area_id':area,'actions':actions,'device_class':'temperature' if d=='sensor' else None,
            'attributes':{'available':True,'hvac_modes':['cool','heat','auto'],'fan_modes':['low','high']}})
    return {'areas':[{'area_id':a,'names':[a.title()]} for a in ['sala','cozinha','quarto']],'entities':rows}


def op(action,entity,**params):return {'action':action,'entity_id':entity,'parameters':params}


CASES=[
    ('Coloca a luz da sala em 40%',[op('brightness','light.sala',value=40)]),
    ('Deixa o ar em 23 graus',[op('temperature','climate.sala',value=23)]),
    ('Liga a televisão',[op('turn_on','switch.tv')]),
    ('Aumenta a iluminação em 10%',[op('brightness','light.sala',value=10,relative=1)]),
    ('Abre a persiana até a metade',[op('position','cover.sala',value=50)]),
    ('Coloca a iluminação azul',[op('color','light.sala',value='azul')]),
    ('Coloca o ventilador em 50%',[op('percentage','fan.sala',value=50)]),
    ('Liga a TV e apaga a luz da sala',[op('turn_on','switch.tv'),op('turn_off','light.sala')]),
    ('Coloca o ar em 23 graus e liga o ventilador',[op('temperature','climate.sala',value=23),op('turn_on','fan.sala')]),
    ('Apaga a luz da sala e da cozinha',[op('turn_off','light.sala'),op('turn_off','light.cozinha')]),
    ('Pausa o som',[op('pause','media_player.sala')]),
    ('Inicia o aspirador',[op('vacuum_start','vacuum.casa')]),
    ('Coloca o ar no modo frio',[op('hvac_mode','climate.sala',value='cool')]),
    ('Coloca o ar no modo ventilação alta',[op('fan_mode','climate.sala',value='high')]),
    ('Oscila o ventilador',[op('oscillate','fan.sala',value=True)]),
    ('Coloca a direção do ventilador para frente',[op('direction','fan.sala',value='forward')]),
    ('Coloca a luz em 3000 kelvin',[op('color_temperature','light.sala',value=3000)]),
    ('Coloca o volume do som em 30%',[op('volume','media_player.sala',value=30)]),
    ('Avance a faixa do som',[op('next','media_player.sala')]),
    ('Manda o aspirador para a base',[op('vacuum_dock','vacuum.casa')]),
    ('Ativa a cena noite',[op('activate','scene.noite')]),
    ('Ativa a automação noite',[op('automation_enable','automation.noite')]),
    ('Qual a temperatura do quarto?',[op('query','sensor.quarto',metric='temperature')]),
    ('Aumenta mais dois',[op('temperature','climate.sala',value=2,relative=1)]),
    ('Desliga o da cozinha',[op('turn_off','light.cozinha')]),
    ('Diminui o ventilador um pouco',[]),('Não liga nenhuma luz',[]),
    ('Se estiver quente liga o ar',[]),('Liga a TV ou o ventilador',[]),
    ('Desliga tudo menos a TV',[]),('Abre o portão e desativa o alarme',[]),
    ('Coloca a luz em 140%',[]),('Liga o ventilador do banheiro',[]),
    ('Liga a TV então desliga a TV',[]),('Coloca a iluminação em 40 watts',[]),
    ('O que é uma lâmpada?','conversation'),('Conta uma piada curta','conversation'),
]


def prediction(result):
    if result.get('plan'):
        return [{k:o[k] for k in ('action','entity_id','parameters')} for o in result['plan']['operations']]
    p=result.get('proposal') or {}
    if p.get('intent')=='conversation':return 'conversation'
    if not p or p.get('requires_clarification') or p.get('intent') is None:return []
    return [op(p['intent'],e) for e in p['target_entities']]


def percentiles(values):
    s=sorted(values)
    return {f'p{p}_ms':s[min(len(s)-1,round((len(s)-1)*p/100))] for p in [50,95,99]} if s else {}


def quality(rows):
    """Whole-plan dimensions; refusals do not count as correct parameter decoding."""
    positive=[r for r in rows if isinstance(r['expected'],list) and r['expected']]
    refused=[r for r in rows if r['expected']==[]]
    generated=[r for r in rows if r.get('result',{}).get('generated_tokens')]
    scores={}
    for label,key in [('intent','action'),('entity','entity_id'),('parameters','parameters')]:
        scores[label]={'correct':sum(isinstance(r['actual'],list) and len(r['actual'])==len(r['expected']) and
            all(a[key]==e[key] for a,e in zip(r['actual'],r['expected'])) for r in positive),'total':len(positive)}
    scores.update(correct_abstention={'correct':sum(r['actual']==[] and not r['error'] for r in refused),'total':len(refused)},
        llm_generated_requests=len(generated),early_refusals=len(rows)-len(generated),
        confirmations=sum(bool(r.get('result',{}).get('confirmation_required')) for r in rows),
        generated_responses_accepted=sum(not r['error'] for r in generated))
    return scores


async def main():
    data=catalog();cases=CASES[:2] if os.environ.get('ARANDU_LLM_QUICK') else CASES
    output={'representative':False,'provenance':'Approved Phase 3 examples / FIXTURE_TECNICA',
        'hardware':'Ryzen 7 5700X / Docker Linux, not i5-6500T','threads':2,
        'source_overrides':os.environ.get('ARANDU_SOURCE_OVERRIDES')=='1','ha_actions':0,'runs':{},
        'process_environment':{k:os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_WAIT_POLICY','KMP_BLOCKTIME','NUMEXPR_NUM_THREADS','TOKENIZERS_PARALLELISM')}}
    for key,version in [('tucano',1),('tucano',2),('qwen3',2)]:
        adapter=Llama(Path(os.environ['ARANDU_LLM_MODELS']),'/usr/local/bin/llama-server',2,
            settings={'context_size':4096,'max_tokens':384,'temperature':0,'llm_model':key,'contract_version':version})
        rows=[]
        try:
            for text,expected in cases:
                before=time.perf_counter()
                try:
                    context={'origin_area':'sala','temperature_unit':'°C'}
                    if text=='Aumenta mais dois':context.update(last_targets=['climate_sala'],last_area='sala',last_action='temperature')
                    if text=='Desliga o da cozinha':context.update(last_targets=['light_sala'],last_area='sala',last_action='turn_on')
                    body={'text':text,'llm_model':key,'contract_version':version,'context':context}
                    if version==1:body['decision']=retrieve(text,data,body['context'])
                    result=await adapter.infer(body,data);actual=prediction(result)
                    row={'text':text,'expected':expected,'actual':actual,'correct':actual==expected,
                        'false_activation':expected==[] and bool(actual),'result':result,'error':None}
                except Exception as exc:
                    row={'text':text,'expected':expected,'actual':[],'correct':False,
                        'false_activation':False,'error':type(exc).__name__+': '+str(exc)}
                row['wall_ms']=(time.perf_counter()-before)*1000;rows.append(row)
                print(key,version,text,row['correct'],row['error'],round(row['wall_ms']),flush=True)
        finally:await adapter.unload()
        generated=[r for r in rows if r.get('result',{}).get('generated_tokens') and r['result'].get('load_ms')==0]
        output['runs'][f'{key}-v{version}']={'samples':len(rows),'correct':sum(r['correct'] for r in rows),
            'false_activations':sum(r['false_activation'] for r in rows),'errors':sum(r['error'] is not None for r in rows),
            'wall_latency':percentiles([r['wall_ms'] for r in rows]),
            'warm_generation_samples':len(generated),'warm_generation_latency':percentiles([r['result']['inference_ms'] for r in generated]),
            'peak_rss_bytes':max((r.get('result',{}).get('memory_bytes') or 0 for r in rows),default=0),
            'quality':quality(rows),'rows':rows}
        Path(os.environ['ARANDU_LLM_OUTPUT']).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':asyncio.run(main())

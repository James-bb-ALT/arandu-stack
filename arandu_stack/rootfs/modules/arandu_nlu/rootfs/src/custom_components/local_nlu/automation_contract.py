"""LLM automation v2: independently reproducible evidence, never an executor.

Keep identical in the passive LLM package. Catalog values are untrusted data.
Only closed action/parameter mappings may cross the HA boundary.
"""
from __future__ import annotations

import json
import math
import re

from .semantic_context import normalized, semantic_refusal, clearly_conversational, speech_only_request, POLICY

VERSION = 2
ACTIONS = frozenset(('turn_on','turn_off','query','brightness','color','color_temperature',
    'temperature','hvac_mode','fan_mode','percentage','oscillate','direction','open','close',
    'position','play','pause','stop','next','previous','volume','activate',
    'vacuum_start','vacuum_pause','vacuum_dock','automation_enable','automation_disable'))
CATEGORIES = {'light':('luz','luzes','lampada','iluminacao','luminaria'),
    'fan':('ventilador','ventoinha'), 'climate':('ar','ar condicionado','split','termostato'),
    'tv':('tv','televisao','televisor'), 'cover':('cortina','persiana','estore'),
    'media_player':('som','musica','alto falante'), 'vacuum':('aspirador','robo'),
    'scene':('cena',), 'script':('script',),'automation':('automacao','automacoes')}
COLORS = {'azul','verde','vermelho','amarelo','branco','laranja','roxo','rosa'}
NUMBERS = {'zero':0,'um':1,'uma':1,'dois':2,'duas':2,'tres':3,'quatro':4,'cinco':5,
    'seis':6,'sete':7,'oito':8,'nove':9,'dez':10,'vinte':20,'trinta':30,'quarenta':40,
    'cinquenta':50,'sessenta':60,'setenta':70,'oitenta':80,'noventa':90,'cem':100}
MODE_ALIASES = {'frio':'cool','resfriar':'cool','quente':'heat','aquecer':'heat',
    'automatico':'auto','ventilar':'fan_only','seco':'dry','alto':'high','alta':'high',
    'baixo':'low','baixa':'low','medio':'medium','media':'medium'}
FRAME=frozenset('coloca coloque deixa deixe ajusta ajuste define defina poe liga ligue acende acenda desliga desligue apaga apague aumenta aumente diminui diminua reduz reduza abaixa sobe eleva abre abra fecha feche reproduz reproduza toca toque pausa pause para pare avance volta ativa ative inicia inicie limpa limpe manda mande retorna retorne oscila oscile faz'.split())
FRAME_PATTERN=r'^(?:por favor )?(?:agora )?(?:'+'|'.join(sorted(FRAME))+r')\b'


def covered(clause,row,action,catalog):
    """Unknown modifiers never disappear behind an otherwise plausible plan."""
    q=normalized(clause)
    for label in sorted([row['name'],*row.get('aliases',[]),row.get('device_name') or ''],key=len,reverse=True):
        n=normalized(label)
        if n:q=re.sub(r'\b'+re.escape(n)+r'\b',' ',q)
    for area in catalog['areas']:
        for label in area['names']:
            n=normalized(label)
            if n:q=re.sub(r'\b'+re.escape(n)+r'\b',' ',q)
    allowed=set(FRAME)|set('a o as os um uma de da do das dos na no nas nos em para pra por favor agora daqui aqui la daquele desse comodo ela ele isso mesmo mesma'.split())
    for names in CATEGORIES.values():
        for name in names:allowed.update(name.split())
    numeric=action in {'brightness','percentage','temperature','color_temperature','position','volume'}
    if numeric:
        allowed.update(NUMBERS);allowed.update('e mais menos fraca fraco metade brilho velocidade volume temperatura graus kelvin k c f celsius fahrenheit por cento posicao ate'.split())
    if action=='color':allowed.update(COLORS);allowed.update({'cor'})
    if action in {'hvac_mode','fan_mode'}:allowed.update(MODE_ALIASES);allowed.update({'modo','ventilacao'})
    if action=='oscillate':allowed.update({'oscilacao','oscilar','oscile'})
    if action=='direction':allowed.update({'direcao','frente','reverso','reversa'})
    if action in {'play','pause','stop','next','previous','volume'}:allowed.update({'faixa','reproducao'})
    if action=='vacuum_dock':allowed.update({'base','doca','recarregar'})
    if action=='query':allowed.update({'qual','quais','quanto','estado','temperatura','umidade','esta'})
    return all(w in allowed or numeric and w.isdecimal() for w in q.split())


def contains(text, name):
    return bool(name) and ' '+name+' ' in ' '+text+' '


def effect_property(action):
    if action in {'turn_on','turn_off','open','close','activate','automation_enable','automation_disable'}:return 'power'
    if action in {'play','pause','stop'}:return 'playback'
    if action in {'vacuum_start','vacuum_pause','vacuum_dock'}:return 'vacuum_motion'
    return action


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def clarification(reason, analysis=None):
    return {'version':VERSION,'type':'clarification','operations':[],
        'question':'Pode especificar a ação, o dispositivo e o valor?','response_text':None,
        'reason':reason,'analysis':analysis or {}}


def quantities(text):
    raw=re.findall(r'(?<!\w)[+-]?\d+(?:[.,]\d+)?',text)
    if raw:
        return [float(n.replace(',','.')) for n in raw]
    words=normalized(text).split();values=[];i=0
    while i<len(words):
        w=words[i]
        if w in NUMBERS and (w not in {'um','uma'} or i==len(words)-1 or i and words[i-1] in {'mais','menos','em'}):
            value=NUMBERS[w]
            if value>=20 and i+2<len(words) and words[i+1]=='e' and words[i+2] in NUMBERS and 0<NUMBERS[words[i+2]]<10:
                value+=NUMBERS[words[i+2]];i+=2
            values.append(value)
        i+=1
    return values


def parse_action(text, context):
    q=normalized(text);words=set(q.split());values=quantities(text)
    if sum(w in POLICY['effects'] for w in q.split())>1:return None,{},'multiple_actions_in_clause'
    params={};action=None
    relative=1 if words & {'aumenta','aumente','aumentar','sobe','eleva','mais'} else -1 if words & {'diminui','diminua','diminuir','reduz','reduza','abaixa','menos','fraca','fraco'} else None
    if re.match(r'^(qual|quais|quanto|consulta|consulte)\b',q):
        metric='temperature' if 'temperatura' in words else 'humidity' if 'umidade' in words else None
        return 'query', {'metric':metric} if metric else {}, None
    if not re.match(FRAME_PATTERN,q):
        return None,{},'action_not_explicit'
    power=re.match(r'^(?:por favor )?(?:agora )?(liga|ligue|acende|acenda|desliga|desligue|apaga|apague)\b',q)
    if power:
        if '%' in text or words & {'brilho','graus','temperatura','velocidade','volume','kelvin'}:
            return None,{},'parameterized_power_requires_clarification'
        if values and values!=context.get('name_quantities',[]):return None,{},'unresolved_quantity'
        action='turn_off' if power[1] in {'desliga','desligue','apaga','apague'} else 'turn_on'
        if words & {'automacao','automacoes'}:action='automation_disable' if action=='turn_off' else 'automation_enable'
        return action,{},None
    if words & {'brilho','iluminacao','luz','luzes','lampada'} and (values or relative or words & COLORS):
        action='color' if words & COLORS else 'brightness'
    if words & {'temperatura','graus','ar','split','termostato'} and values:
        action='temperature'
    if words & {'kelvin'} or re.search(r'\d\s*[kK]\b',text):
        action='color_temperature'
    if words & {'ventilador','ventoinha','velocidade'} and (values or relative):
        action='percentage'
    if words & {'persiana','cortina','posicao','estore'} and (values or 'metade' in words):
        action='position'
    if words & {'volume'} and (values or relative):
        action='volume'
    if words & {'modo'}:
        action='fan_mode' if words & {'ventilacao','ventoinha'} else 'hvac_mode'
    if words & {'oscila','oscilar','oscilacao'}:
        action='oscillate';params={'value':not bool(words & {'para','pare','desativa','desative'})}
    if words & {'direcao'}:
        action='direction';params={'value':'reverse' if words & {'reversa','reverso'} else 'forward' if 'frente' in words else None}
    if action is None and relative:
        action=context.get('last_action') if context.get('last_action') in {'temperature','brightness','percentage','volume','position','color_temperature'} else context.get('last_numeric_action')
    if action is None and values:action=context.get('named_numeric_action')
    if action is None:
        for verbs,candidate in [({'liga','ligue','acende','acenda'},'turn_on'),
            ({'desliga','desligue','apaga','apague'},'turn_off'),({'abre','abra','abrir'},'open'),
            ({'fecha','feche','fechar'},'close'),({'reproduz','reproduza','toca','toque'},'play'),
            ({'pausa','pause'},'pause'),({'para','pare'},'stop'),({'proxima','avance'},'next'),
            ({'anterior','volta'},'previous'),({'ativa','ative'},'activate')]:
            if words & verbs: action=candidate;break
        if words & {'aspirador','robo'}:
            if words & {'inicia','inicie','limpa','limpe'}: action='vacuum_start'
            elif words & {'pausa','pause'}: action='vacuum_pause'
            elif words & {'base','recarregar','doca'}: action='vacuum_dock'
    if words & {'automacao','automacoes'}:
        if action in {'activate','turn_on'}:action='automation_enable'
        elif action=='turn_off':action='automation_disable'
    if action is None:
        return None,{},'action_not_grounded'
    if action in {'brightness','temperature','percentage','volume','position','color_temperature'}:
        # Never derive a numeric increment from vague language or an interval.
        if len(values)>1 or 'entre' in words or ('ate' in words and values and not (action=='position' and relative is None)):
            return action,{},'range_or_multiple_values'
        if 'metade' in words and action in {'position','brightness','percentage','volume'} and not values:
            values=[50]
        if len(values)!=1:
            return action,{},'quantity_required'
        value=values[0]
        if not finite(value) or abs(value)>1_000_000:return action,{},'invalid_quantity'
        if words & {'watt','watts','volts','metro','metros','centimetros','rpm','hz'}:
            return action,{},'unsupported_unit'
        if action!='temperature' and (words & {'fahrenheit','celsius'} or re.search(r'\d\s*°?\s*[cCfF]\b',text)):
            return action,{},'unsupported_unit'
        if action=='temperature':
            unit='°F' if 'fahrenheit' in words or re.search(r'\d\s*°?\s*[fF]\b',text) else '°C' if 'celsius' in words or re.search(r'\d\s*°?\s*[cC]\b',text) else None
            if unit and unit!=context.get('temperature_unit'):return action,{},'temperature_unit_mismatch'
            if '%' in text or contains(q,'por cento'):return action,{},'unsupported_unit'
        if relative:
            if not 0<value<=1000:return action,{},'invalid_increment'
            params={'value':value,'relative':relative}
        else:
            params={'value':value}
        if action in {'brightness','percentage','volume','position'} and not relative and not 0<=value<=100:
            return action,{},'out_of_range'
    elif action=='color':
        colors=sorted(words & COLORS)
        if len(colors)!=1:return action,{},'color_required'
        params={'value':colors[0]}
    elif action in {'hvac_mode','fan_mode'}:
        mode=next((MODE_ALIASES[w] for w in q.split() if w in MODE_ALIASES),None)
        if mode is None:return action,{},'mode_required'
        params={'value':mode}
    if any(v is None for v in params.values()):return action,{},'parameter_required'
    return action,params,None


def targets(text, action, catalog, context):
    q=normalized(text);words=set(q.split());areas=catalog['areas']
    explicit=[a['area_id'] for a in areas if any(contains(q,normalized(n)) for n in a['names'])]
    if len(explicit)>1:return [],'multiple_areas',[]
    area=explicit[0] if explicit else context.get('last_area') if contains(q,'de la') else context.get('origin_area')
    if contains(q,'de la') and (not context.get('last_targets') or area not in {a['area_id'] for a in areas}):
        return [],'missing_context',[]
    rows=[];diagnostic=[]
    category=next((c for c,names in CATEGORIES.items() if any(contains(q,n) for n in names)),None)
    history=set(context.get('last_targets') or [])
    if category is None and explicit and re.search(r'\b[oa] (?:do|da|de)\b',q):
        prior=[r for r in catalog['entities'] if r['entity_id'] in history or r.get('registry_id') in history]
        if len(prior)==1:
            row=prior[0];description=normalized(' '.join([row['name'],*row.get('aliases',[])]))
            category=next((c for c,names in CATEGORIES.items() if row['domain']==c or any(contains(description,n) for n in names)),None)
    # Only complete labels may explain a spatial preposition in an entity name.
    masked=q
    for row in catalog['entities']:
        for name in [row['name'],*row.get('aliases',[])]:
            n=normalized(name)
            if contains(q,n):masked=re.sub(r'\b'+re.escape(n)+r'\b','alvo',masked)
    for a in areas:
        for n in a['names']:masked=re.sub(r'\b'+re.escape(normalized(n))+r'\b','area',masked)
    if action in {'hvac_mode','fan_mode','direction','position','percentage'}:
        masked=re.sub(r'\b(?:no|na) (?:modo|direcao|posicao|velocidade)\b','parametro',masked)
    if re.search(r'\b(?:no|na|do|da)\s+(?!alvo\b|area\b|comodo\b|dispositivo\b)\w+',masked):
        # Category phrases ("no ventilador") are not unknown room names.
        rest=re.sub(r'\b(?:no|na|do|da)\s+(?:'+ '|'.join(re.escape(n) for names in CATEGORIES.values() for n in names)+r')\b','',masked)
        if re.search(r'\b(?:no|na|do|da)\s+(?!alvo\b|area\b|comodo\b)\w+',rest):return [],'unknown_area',[]
    pronoun=bool(words & {'ela','ele','isso','mesmo','mesma'}) or (category is None and bool(words & {'mais','menos'}))
    if pronoun and not explicit and history:
        historical=[r for r in catalog['entities'] if r['entity_id'] in history or r.get('registry_id') in history]
        if len(historical)==1:area=historical[0].get('area_id')
    for row in catalog['entities']:
        if row.get('attributes',{}).get('available') is not True:continue
        if action not in row['actions'] or (area is not None and row.get('area_id')!=area):continue
        labels=[normalized(n) for n in [row['name'],*row.get('aliases',[]),row.get('device_name') or '']]
        exact=max((len(n.split()) for n in labels if contains(q,n)),default=0)
        head=any(n.split() and n.split()[0] in words for n in labels)
        metadata=normalized(' '.join(labels+[row['domain'],row.get('device_class') or '']))
        matches_category=category and (category==row['domain'] or any(contains(metadata,n) for n in CATEGORIES[category]))
        score=100+exact if exact else 20 if matches_category and head else 10 if matches_category else 0
        metric= 'temperature' if 'temperatura' in words else 'humidity' if 'umidade' in words else None
        if action=='query' and metric and row.get('device_class')==metric:score=max(score,30)
        if pronoun and (row['entity_id'] in history or row.get('registry_id') in history):score=max(score,50)
        if score:
            item={'entity_id':row['entity_id'],'score':score,'area_id':row.get('area_id'),'evidence':'name/category/capability/area/history'}
            diagnostic.append(item);rows.append((score,row))
    if not rows:return [],'target_not_retrieved',diagnostic
    best=max(s for s,_ in rows);winners=[r for s,r in rows if s==best]
    if len(winners)>1 and len({r.get('device_id') for r in winners})==1 and winners[0].get('device_id'):
        preferred=[r for r in winners if r.get('preferred') is True]
        if len(preferred)==1:winners=preferred
    return winners,None if len(winners)==1 else 'ambiguous_target',sorted(diagnostic,key=lambda r:(-r['score'],r['entity_id']))[:8]


def analyze(text, catalog, context=None):
    context=dict(context or {});context.pop('last_numeric_action',None)
    history=set(context.get('last_targets') or [])
    prior=[r for r in catalog['entities'] if r['entity_id'] in history or r.get('registry_id') in history]
    if len(prior)==1:
        numeric=set(prior[0]['actions']) & {'brightness','percentage','temperature','color_temperature','position','volume'}
        if len(numeric)==1 and context.get('last_action') in {None,'turn_on','turn_off','query'}:
            context['last_numeric_action']=next(iter(numeric))
        if prior[0]['domain']=='light' and re.search(r'\b(?:fraca|fraco|clara|claro)\b',normalized(text)) and 'brightness' in numeric:
            context['last_numeric_action']='brightness'
    refusal=semantic_refusal(text,catalog)
    if refusal:return clarification(refusal)
    q=normalized(text)
    if clearly_conversational(text,catalog) or re.match(r'^(como (?:faco|fazer)|quem inventou|o que e)\b',q):
        return {'version':VERSION,'type':'conversation','operations':[],'question':None,'response_text':None,'reason':'conversation','analysis':{}}
    if speech_only_request(text):return clarification('speech_not_action')
    if re.search(r'\b(?:ou|talvez|caso|quando|depois|entao|seguida|antes)\b',q) or re.search(r'(?<!\d),|,(?!\d)',text):
        return clarification('unsupported_alternative_or_dependency')
    # Mask conjunctions in complete registered names before splitting operations.
    protected=text
    protected=re.sub(r'\b(vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa)\s+e\s+(um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove)\b',r'\1 __NUMAND__ \2',protected,flags=re.I)
    for row in catalog['entities']:
        for name in [row['name'],*row.get('aliases',[])]:
            if re.search(r'\s+e\s+',name,re.I):protected=protected.replace(name,name.replace(' e ',' __AND__ '))
    clauses=[c.strip().replace('__AND__','e').replace('__NUMAND__','e') for c in re.split(r'\s+e\s+|\s*;\s*',protected,flags=re.I) if c.strip()]
    if not 1<=len(clauses)<=4:return clarification('operation_limit')
    operations=[];analysis=[];previous=None
    for index,clause in enumerate(clauses):
        clause_context=dict(context);clause_context.pop('named_numeric_action',None);clause_context.pop('name_quantities',None)
        clause_query=normalized(clause)
        explicit=[a['area_id'] for a in catalog['areas'] if any(contains(clause_query,normalized(n)) for n in a['names'])]
        scope=explicit[0] if len(explicit)==1 else context.get('origin_area')
        named=[];name_quantities=[]
        for row in catalog['entities']:
            if row.get('attributes',{}).get('available') is not True or scope is not None and row.get('area_id')!=scope:continue
            labels=[n for n in [row['name'],*row.get('aliases',[])] if contains(clause_query,normalized(n))]
            if labels:
                name_quantities.extend(quantities(max(labels,key=len)))
                family={'light':'brightness','fan':'percentage','climate':'temperature','cover':'position','media_player':'volume'}.get(row['domain'])
                if family in row['actions']:named.append(family)
        if len(set(named))==1:clause_context['named_numeric_action']=named[0]
        clause_context['name_quantities']=name_quantities
        action,params,error=parse_action(clause,clause_context)
        # Explicit coordinated room ellipsis inherits only the immediately stated
        # action and device category, never arbitrary historical parameters.
        if error in {'action_not_grounded','action_not_explicit'} and previous and any(contains(normalized(clause),normalized(n)) for a in catalog['areas'] for n in a['names']):
            prefix=previous
            clause=prefix+' '+clause
            action,params,error=parse_action(clause,context)
        if error:return clarification(error,{'clauses':analysis,'action':action})
        found,error,candidates=targets(clause,action,catalog,context)
        analysis.append({'clause':clauses[index],'action':action,'parameters':params,'candidates':candidates})
        if error:return clarification(error,{'clauses':analysis})
        row=found[0]
        if not covered(clause,row,action,catalog):return clarification('unparsed_constraint',{'clauses':analysis})
        operations.append({'action':action,'entity_id':row['entity_id'],'parameters':params,'evidence':index})
        cat=next((c for c,names in CATEGORIES.items() if any(contains(normalized(clause),n) for n in names)),None)
        verb={'turn_on':'liga','turn_off':'apaga'}.get(action)
        previous=verb+' '+CATEGORIES[cat][0] if verb and cat else None
    affected=set()
    for op in operations:
        prop=effect_property(op['action'])
        key=(op['entity_id'],prop)
        if key in affected:return clarification('contradictory_plan',{'clauses':analysis})
        affected.add(key)
    return {'version':VERSION,'type':'automation','operations':operations,'question':None,
        'response_text':None,'reason':'grounded_typed_plan','analysis':{'clauses':analysis}}


def validate(value, text, catalog, context=None, allowed=None):
    if type(value) is not dict or set(value)!={'version','type','operations','question','response_text'}:
        raise ValueError('LLM v2 envelope inválido')
    if type(value['version']) is not int or value['version']!=VERSION or value['type'] not in {'automation','conversation','clarification'}:
        raise ValueError('LLM v2 versão/tipo inválido')
    expected=analyze(text,catalog,context)
    entities_by_id={row.get('entity_id'):row for row in catalog.get('entities',()) if isinstance(row,dict) and row.get('entity_id')}
    if value['type']=='automation':
        if value['question'] is not None or value['response_text'] is not None:
            raise ValueError('Automação não deve misturar texto livre')
        ops = value.get('operations')
        if type(ops) is not list or not 1 <= len(ops) <= 4:
            raise ValueError('Operações ausentes ou acima do limite')
        if expected['type']=='automation' and ops!=expected['operations']:
            raise ValueError('Plano/valor/alvo diverge das evidências')
        allowed_ids = set(allowed) if allowed is not None else set(entities_by_id)
        for op in ops:
            if type(op) is not dict:
                raise ValueError('Operação inválida')
            action = op.get('action')
            if action not in ACTIONS:
                raise ValueError('Ação não permitida')
            targets = op.get('targets') if isinstance(op.get('targets'), list) else ([op.get('entity_id')] if op.get('entity_id') else [])
            if not targets or any(t not in allowed_ids or t not in entities_by_id for t in targets):
                raise ValueError('Alvo não autorizado no catálogo')
            for t in targets:
                caps = entities_by_id[t].get('capabilities') or entities_by_id[t].get('actions') or ()
                if caps and action not in caps:
                    raise ValueError('Capacidade incompatível com o alvo')
        for actual,wanted in zip(value['operations'],expected['operations']):
            if type(actual.get('evidence')) is not int:raise ValueError('Índice de evidência inválido')
            for key,operand in actual['parameters'].items():
                if type(operand) is bool and type(wanted['parameters'][key]) is not bool:
                    raise ValueError('Booleano não é quantidade')
                if key=='relative' and type(operand) is not int:raise ValueError('Direção relativa inválida')
        if allowed is not None and any(op['entity_id'] not in allowed for op in value['operations']):
            raise ValueError('Alvo fora da recuperação')
    else:
        if value['operations']!=[]:raise ValueError('Conversa/esclarecimento não pode conter operações')
        if value['type']=='conversation' and expected['type']!='conversation':raise ValueError('Conversa sem fundamento')
        field='response_text' if value['type']=='conversation' else 'question'
        if type(value[field]) is not str or not 1<=len(value[field])<=1000:raise ValueError('Resposta textual inválida')
        if value['response_text' if field=='question' else 'question'] is not None:raise ValueError('Tipos de resposta misturados')
    # A similarity, model confidence or arbitrary parameter is never accepted.
    return json.loads(json.dumps(value,allow_nan=False))


def schema(catalog, ids, analysis=None):
    if analysis and analysis['type']=='automation':
        choices=[]
        for index,clause in enumerate(analysis['analysis']['clauses']):
            params={}
            for key,value in clause['parameters'].items():
                if key=='relative' or key=='metric':spec={'const':value}
                elif type(value) is bool:spec={'type':'boolean'}
                elif finite(value):
                    # Grounded quantities are decoder constraints, not estimates.
                    # This prevents fraction/unit conversion hallucinations.
                    spec={'type':'number','enum':[value]}
                else:spec={'type':'string','enum':[value]}
                params[key]=spec
            choices.append({'type':'object','additionalProperties':False,'properties':{
                'action':{'const':clause['action']},
                'entity_id':{'type':'string','enum':sorted({r['entity_id'] for r in clause['candidates']})},
                'parameters':{'type':'object','additionalProperties':False,'properties':params,'required':list(params)},
                'evidence':{'const':index}},'required':['action','entity_id','parameters','evidence']})
        return {'type':'object','additionalProperties':False,'properties':{
            'version':{'const':2},'type':{'const':'automation'},
            'operations':{'type':'array','minItems':len(choices),'maxItems':len(choices),'prefixItems':choices},
            'question':{'type':'null'},'response_text':{'type':'null'}},
            'required':['version','type','operations','question','response_text']}
    return {'type':'object','additionalProperties':False,'properties':{
        'version':{'type':'integer','const':VERSION},'type':{'type':'string','enum':['automation','clarification']},
        'operations':{'type':'array','maxItems':4,'items':{'type':'object','additionalProperties':False,
            'properties':{'action':{'type':'string','enum':sorted(ACTIONS)},
                'entity_id':{'type':'string','enum':sorted(ids) or ['__no_candidate__']},
                'parameters':{'type':'object','additionalProperties':False,'properties':{
                    'value':{'type':['number','string','boolean']},'relative':{'type':'integer','enum':[-1,1]},
                    'metric':{'type':'string','enum':['temperature','humidity']}}},
                'evidence':{'type':'integer','minimum':0,'maximum':3}},
            'required':['action','entity_id','parameters','evidence']}},
        'question':{'type':['string','null'],'maxLength':512},'response_text':{'type':'null'}},
        'required':['version','type','operations','question','response_text']}

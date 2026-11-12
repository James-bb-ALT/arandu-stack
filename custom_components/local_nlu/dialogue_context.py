"""Ephemeral, single-effect antecedents and compare-before-undo snapshots."""
from dataclasses import dataclass
import copy
import math
import re
import time

from .contextual_protocol import Operation, normalize
from .queries import QueryError


def context_request(text):
    value=normalize(text)
    return bool(re.fullmatch(r'(?:agora )?(?:a|o) (?:da|do) .+?(?: tambem)?|(?:o outro|a outra) tambem|faz igual n[oa] .+|(?:volta|volte) como estava|(?:desfaz|desfaca)(?: a ultima mudanca)?',value))


def reserved(text):
    from .todo_actions import canonical_head
    value=canonical_head(normalize(text))
    return context_request(text) or value in ('faz igual','o outro','a outra','agora a','agora o') or value.startswith(('faz igual ','o outro ','a outra ','desfaz ','desfaca ')) or bool(re.search(r'\b(?:automacao|automacoes|lista)\b',value)) or value.startswith((
        'o que falta comprar','marca ','adiciona ','anota ','inclui ','remove ','tira ','quanto consumiu','quanto tempo ','a umidade ','a temperatura ',
        'qual comodo esta mais ','qual aparelho ','qual esta puxando mais ','modo ','cria uma rotina','cria rotina','cria uma automacao')) or ' ficou mais quente' in value


def exact_scene(text, operations, snapshot):
    if text.lstrip().startswith(('"',"'",'“','‘','`','«')):return False
    if len(operations)!=1 or operations[0].intent!='scene.invoke_exact' or operations[0].action!='activate':
        return False
    value=normalize(text)
    labels={value,value.removeprefix('modo ')}
    matches=[row['registry_id'] for row in snapshot.by_id.values() if row['domain']=='scene'
        and 'activate' in row['actions'] and labels.intersection(normalize(n) for n in (row['name'],*row.get('aliases',[])))]
    return len(matches)==1 and operations[0].targets==(matches[0],)


@dataclass
class Dialogue:
    generation: str
    fingerprint: str
    expires: float
    operation: Operation
    inverse: Operation | None
    expected: dict

    def valid(self,generation,fingerprint):
        return self.generation==generation and self.fingerprint==fingerprint and time.monotonic()<self.expires

    def antecedent(self):
        return {'generation':self.generation,'action':self.operation.action,'targets':list(self.operation.targets),'parameters':copy.deepcopy(self.operation.parameters)}

    def undo(self,state,targets):
        if self.inverse is None:raise QueryError('undo_unavailable')
        if targets!=self.operation.targets or not matches(state,self.expected):
            raise QueryError('undo_state_changed')
        return self.inverse


def matches(state,expected):
    if state is None or state.state in ('unknown','unavailable'): return False
    for key,value in expected.items():
        actual=state.state if key=='state' else state.attributes.get(key)
        if type(value) in (int,float):
            if type(actual) not in (int,float) or not math.isfinite(actual) or abs(actual-value)>(1.1 if key=='brightness' else .0001): return False
        elif actual!=value: return False
    return True


def remember(item,state,generation,fingerprint,ttl):
    """Only one reversible, fully validated service call. No scenes or scheduling."""
    op=item.operation
    if len(op.targets)!=1 or len(item.calls)!=1 or op.intent=='context.undo' or state is None:
        return None
    domain=item.rows[0]['domain'];action=op.action;data=item.calls[0]['data']
    attrs=state.attributes;inverse_action=None;inverse_params={};expected={};replay={}
    if action in ('turn_on','turn_off') and domain in ('light','fan') and state.state in ('on','off'):
        inverse_action='turn_on' if state.state=='on' else 'turn_off'
        expected={'state':'on' if action=='turn_on' else 'off'}
    elif (domain,action) in (('light','brightness'),('fan','percentage')):
        attribute='brightness' if domain=='light' else 'percentage'
        field='brightness_pct' if domain=='light' else 'percentage'
        old=attrs.get(attribute)
        if type(old) not in (int,float) or not math.isfinite(old) or state.state not in ('on','off'):return None
        value=data.get(field)
        if type(value) not in (int,float):return None
        inverse_action=action if state.state=='on' else 'turn_off'
        inverse_params={'value':round(old*100/255) if domain=='light' else old} if inverse_action==action else {}
        expected={attribute:round(value*255/100) if domain=='light' else value,'state':'on' if value>0 else 'off'}
        replay={'value':value}
    elif (domain,action) in (('media_player','volume'),('climate','temperature')):
        field='volume_level' if action=='volume' else 'temperature'
        old=attrs.get(field);value=data.get(field)
        if type(old) not in (int,float) or not math.isfinite(old) or type(value) not in (int,float):return None
        inverse_action=action;inverse_params={'value':old*100 if action=='volume' else old}
        expected={field:value};replay={'value':value*100 if action=='volume' else value}
    elif domain=='media_player' and action in ('mute','unmute') and type(attrs.get('is_volume_muted')) is bool:
        inverse_action='mute' if attrs['is_volume_muted'] else 'unmute'
        expected={'is_volume_muted':action=='mute'}
    context_only=action in ('turn_on','turn_off') and domain in ('media_player','climate','switch','input_boolean') and state.state not in ('unknown','unavailable')
    if inverse_action is None and not context_only:return None
    return Dialogue(generation,fingerprint,time.monotonic()+min(300,ttl),
        Operation('context.ellipsis',action,op.targets,replay),Operation('context.undo',inverse_action,op.targets,inverse_params) if inverse_action else None,expected)


def acknowledged(record,state):
    if record is None or not matches(state,record.expected):return None
    if record.operation.action in ('turn_on','turn_off'):
        for key in ('brightness','percentage','color_temp_kelvin','rgb_color','effect','oscillating','direction','preset_mode'):
            value=state.attributes.get(key)
            if isinstance(value,(str,bool,int,float,tuple,list)):
                record.expected[key]=copy.deepcopy(value)
    return record


def validate_copy(utterance,record,operation,snapshot,options):
    if record is None or operation.action!=record.operation.action or operation.parameters!=record.operation.parameters:
        raise QueryError('context_insufficient')
    source=snapshot.by_id.get(record.operation.targets[0])
    if source is None:raise QueryError('context_insufficient')
    text=normalize(utterance)
    if text in ('o outro tambem','a outra tambem'):
        area=source['area_id'];exclude=source['registry_id']
    else:
        match=re.fullmatch(r'(?:agora )?(?:a|o) (?:da|do) (.+?)(?: tambem)?|faz igual n[oa] (.+)',text)
        if not match:raise QueryError('context_insufficient')
        label=match[1] or match[2]
        areas=[a['area_id'] for a in snapshot.payload['areas'] if any(normalize(n)==label for n in a['names'])]
        if len(areas)!=1:raise QueryError('context_insufficient')
        area=areas[0];exclude=None
    candidates=tuple(sorted(row['registry_id'] for row in snapshot.by_id.values() if row['domain']==source['domain']
        and row.get('area_id')==area and row['registry_id']!=exclude and operation.action in row['actions']))
    if len(candidates)!=1 or operation.targets!=candidates:raise QueryError('context_insufficient')
    if any(options.get('entity_preferences',{}).get(snapshot.by_id[t]['entity_id'],{}).get('critical') is True
        for t in (*record.operation.targets,*operation.targets)):raise QueryError('context_insufficient')

"""Literal item binding to unique HA UIDs, with bounded read-before-commit."""
import asyncio
import hashlib
import json
import re

from .contextual_protocol import normalize, text
from .queries import QueryError


MUTATIONS=frozenset(('todo_add','todo_complete','todo_remove','todo_update','todo_remove_completed'))


def canonical_head(value):
    verb,separator,rest=value.partition(' ')
    aliases={'adicione':'adiciona','adicionar':'adiciona','anote':'anota','anotar':'anota','coloque':'coloca','colocar':'coloca',
        'inclua':'inclui','incluir':'inclui','remova':'remove','remover':'remove','tire':'tira','tirar':'tira',
        'marque':'marca','marcar':'marca','troque':'troca','trocar':'troca','renomeie':'renomeia','renomear':'renomeia','limpe':'limpa','limpar':'limpa'}
    return aliases.get(verb,verb)+separator+rest


async def bind(hass, call, action, context, authorize):
    if action=='todo_add':
        return call['data'],None
    await authorize()
    async with asyncio.timeout(5):
        raw=await hass.services.async_call('todo','get_items',{},blocking=True,
            context=context,target={'entity_id':[call['entity_id']]},return_response=True)
    await authorize()
    if type(raw) is not dict or set(raw)!={call['entity_id']}:
        raise QueryError('todo_response_entities')
    result=raw[call['entity_id']]
    if type(result) is not dict or type(result.get('items')) is not list or len(result['items'])>256:
        raise QueryError('todo_response_limit')
    if len(json.dumps(raw,ensure_ascii=False).encode())>131072:
        raise QueryError('todo_response_bytes')
    items=result['items']
    if any(type(item) is not dict or not text(item.get('uid'),128) or not text(item.get('summary'),256)
        or item.get('status') not in ('needs_action','completed') for item in items):
        raise QueryError('todo_response_item')
    if len({i['uid'] for i in items})!=len(items):
        raise QueryError('todo_duplicate_uid')
    if action=='todo_remove_completed':
        selected=sorted((i['uid'],i['summary']) for i in items if i['status']=='completed')
        if not selected: raise QueryError('todo_no_completed_items')
        digest=hashlib.sha256(json.dumps(selected,ensure_ascii=False).encode()).hexdigest()
        return {},(digest,len(selected))
    selected=[i for i in items if normalize(i['summary'])==normalize(call['data']['item'])]
    if len(selected)!=1:
        raise QueryError('todo_item_not_unique')
    return {**call['data'],'item':selected[0]['uid']},selected[0]['summary']


def literal_binding(utterance, operations):
    """Independent whole-clause binding before exempting literal list content."""
    if len(operations)!=1 or operations[0].action not in MUTATIONS or not operations[0].intent.startswith('todo.'):
        return False
    op=operations[0]
    value=canonical_head(normalize(utterance))
    match=re.fullmatch(r'(adiciona|anota|coloca|inclui|remove|tira) (.+?) (?:na|da|a) lista(?: de .+| .+)?',value)
    if match:
        expected='todo_add' if match[1] in ('adiciona','anota','coloca','inclui') else 'todo_remove'
        return op.action==expected and normalize(op.parameters.get('value',''))==match[2]
    match=re.fullmatch(r'(adiciona|anota|inclui|remove|tira) (.+)',value)
    if match and 'lista' not in match[2].split():
        expected='todo_add' if match[1] in ('adiciona','anota','inclui') else 'todo_remove'
        return op.action==expected and normalize(op.parameters.get('value',''))==match[2]
    match=re.fullmatch(r'(?:troca|renomeia) (.+?) (?:por|para) (.+?)(?: (?:na|da) lista(?: de .+| .+)?)?',value)
    if match:
        return op.action=='todo_update' and normalize(op.parameters.get('old_item',''))==match[1] and normalize(op.parameters.get('new_item',''))==match[2]
    return False

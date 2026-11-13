"""Existing automation execution; conditions are never bypassed."""
import re
from .contextual_protocol import normalize


def requested(utterance,operation,snapshot):
    if utterance.lstrip().startswith(('"',"'",'“','‘','`','«')):return False
    match=re.fullmatch(r'(\w+) (?:a )?automacao (.+?)(?: agora)?',normalize(utterance))
    verbs={'automation_enable':{'ativa','ative','ativar','habilita','habilite','habilitar','liga','ligue','ligar'},
        'automation_disable':{'desativa','desative','desativar','desabilita','desabilite','desabilitar','desliga','desligue','desligar'},
        'automation_trigger':{'executa','execute','executar','roda','rode','rodar'}}
    if not match or match[1] not in verbs.get(operation.action,set()):return False
    def label(value):
        return ' '.join(word for word in normalize(value).split() if word not in {'a','o','as','os','de','do','da','dos','das','em','no','na','automacao'})
    candidates=tuple(sorted(row['registry_id'] for row in snapshot.by_id.values() if row['domain']=='automation'
        and operation.action in row['actions'] and any(label(name)==label(match[2]) for name in (row['name'],*row.get('aliases',[])))))
    return len(candidates)==1 and candidates==operation.targets


def trigger_response():
    return 'Solicitei o disparo da automação respeitando suas condições. As ações resultantes não foram observadas.'

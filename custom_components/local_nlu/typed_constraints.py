"""Independent binding of deterministic temporal/exclusion constraints."""
import re
from .semantic_context import semantic_refusal, normalized, understood_action


def number(value):
    if value.isascii() and value.isdigit():
        return int(value)
    from .voice_controls import volume
    _, result = volume('volume '+value+' por cento')
    return result


def duration(value):
    total = 0
    while value:
        match = re.match(r'(.+?) (segundos?|minutos?|horas?)(?: e |$)',value)
        if not match:
            return None
        amount = number(match[1])
        if amount is None:
            return None
        total += amount*(3600 if match[2].startswith('hora') else 60 if match[2].startswith('minuto') else 1)
        value = value[match.end():]
    return total if 0<total<=604800 else None


def operational_refusal(text, catalog, operations):
    """Allow only independently bound typed constraints from deterministic NLU.

    Neural fallback keeps semantic_refusal unchanged. An ordinary immediate
    action can never erase a clock/delay or an excluded target.
    """
    refusal = semantic_refusal(text, catalog)
    if refusal is None or len(operations) != 1:
        return refusal
    op = operations[0]
    if re.search(r'[+-]\s*\d|\d[.,]\d',text):
        return refusal
    value = normalized(text)
    if op.action == 'schedule_action' and op.parameters.get('target_action') in ('turn_on','turn_off','pause'):
        # Restrict this independent binding to exact numeric delays or clocks.
        delay = re.search(r'\b(?:daqui a|daqui|depois de|apos|em) (.+)$',value)
        clock = re.search(r'\b(?:(hoje|amanha) )?as (.+)$',value)
        matched = None
        if delay:
            seconds=duration(delay[1])
            if seconds is not None and op.parameters.get('delay') == seconds and not any(k in op.parameters for k in ('time','date','recurrence')):
                matched = delay
        elif clock:
            body=clock[2]
            period=re.search(r' (da manha|da tarde|da noite)$',body)
            if period:
                body=body[:period.start()]
            body=re.sub(r' horas?$','',body)
            parts=body.split()
            hour=number(parts[0]) if parts else None
            minute=int(parts[1]) if len(parts)==2 and parts[1].isascii() and parts[1].isdigit() else 0
            valid=len(parts)==1 or (len(parts)==2 and parts[1].isascii() and parts[1].isdigit())
            if hour is not None and period and period[1] in ('da tarde','da noite') and hour<12:
                hour+=12
            expected = f'{hour:02d}:{minute:02d}' if hour is not None and 0<=hour<24 and 0<=minute<60 and valid else None
            if expected and op.parameters.get('time') == expected and op.parameters.get('date') == clock[1] and not any(k in op.parameters for k in ('delay','recurrence')):
                matched = clock
        if matched:
            remainder = value[:matched.start()].strip()
            action=understood_action(remainder,catalog)
            if action is None and remainder.startswith(('pausa ','para ','pare ')):
                action='pause'
            if semantic_refusal(remainder,catalog) is None and action == op.parameters['target_action']:
                return None
    if refusal == 'semantic_exclusion' and op.action in ('turn_on','turn_off') and 'complete_spatial_set' in op.evidence:
        match = re.search(r'\b(?:menos|exceto) (.+)$', value)
        if match:
            clause = re.sub(r'^(?:a|o|as|os) ', '', match[1])
            matches = [row for row in catalog.get('entities',[]) if clause in {normalized(name) for name in (row['name'],*row.get('aliases',[]))}]
            excluded = op.parameters.get('excluded_targets')
            if len(matches)==1 and excluded == [matches[0]['registry_id']] and matches[0]['registry_id'] not in op.targets and semantic_refusal(value[:match.start()],catalog) is None:
                return None
    return refusal

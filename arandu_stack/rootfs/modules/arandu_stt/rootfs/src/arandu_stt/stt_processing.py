"""Shared lexical safety and reference metrics. Never invokes NLU or HA actions."""
import re
import unicodedata


def normalized(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', text))


def action(text):
    words = set(normalized(text).split())
    for value, tokens in [('off', {'desliga', 'desligue', 'desligar', 'apaga', 'apague', 'apagar'}),
                          ('on', {'liga', 'ligue', 'ligar', 'acende', 'acenda', 'acender'})]:
        if words & tokens:
            return value
    return None


def correct(resolver, raw, profile):
    result = resolver.apply(raw, profile)
    final = result['text']
    before, after = normalized(raw).split(), normalized(final).split()
    def critical(words):
        result = []
        for word in words:
            if word in ('nao', 'nunca', 'jamais') or word.isdigit():
                result.append(word)
            elif action(word):
                result.append(action(word))
        return result
    if critical(before) != critical(after):
        # An uncertain lexical rewrite must not change an explicit action or negation.
        result = {**result, 'text': raw, 'changes': [], 'safety_preserved_raw': True}
    return result


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (left != right)))
        previous = current
    return previous[-1]


def evaluate(reference, raw, final, entity_reference=None):
    if not reference or not normalized(reference):
        return {'evaluated': False, 'entity_correct': None, 'action_correct': None, 'negation_preserved': None}
    ref = normalized(reference)
    result = {'evaluated': True, 'entity_correct': None,
              'entity_note': 'Não avaliado: exige anotação de entidade; STT não executa NLU'}
    for label, text in [('raw', raw), ('final', final)]:
        value = normalized(text)
        result['wer_' + label] = distance(ref.split(), value.split()) / len(ref.split())
        result['cer_' + label] = distance(ref, value) / len(ref)
        result['negation_preserved_' + label] = ('nao' in ref.split()) == ('nao' in value.split())
        result['action_correct_' + label] = action(value) == action(ref) if action(ref) else None
    if entity_reference and normalized(entity_reference):
        target = ' ' + normalized(entity_reference) + ' '
        for label, text in [('raw', raw), ('final', final)]:
            result['entity_correct_' + label] = target in (' ' + normalized(text) + ' ')
        result.update(entity_correct=result['entity_correct_final'], entity_note='Comparação literal com entidade anotada; não executa NLU')
    result.update(action_correct=result['action_correct_final'], negation_preserved=result['negation_preserved_final'])
    return result

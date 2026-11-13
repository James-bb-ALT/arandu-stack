"""Decision field contract shared with HA. Evidence never grants permission."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from .semantic_context import normalized, semantic_refusal, hypothesis_allowed, understood_action

ALGORITHM = 'structured-v3'
CALIBRATION = 'arandu-structured-v3'
CATEGORIES = {
    'light': ('luz', 'luzes', 'lampada', 'lampadas', 'iluminacao', 'luminaria'),
    'fan': ('ventilador', 'ventiladores', 'ventoinha'),
    'tv': ('tv', 'televisao', 'televisor'),
    'climate': ('ar condicionado', 'ar', 'split', 'termostato'),
    'cover': ('cortina', 'persiana', 'estore'),
}
ARTICLES = {'a', 'o', 'as', 'os', 'de', 'do', 'da', 'dos', 'das', 'no', 'na', 'em', 'um', 'uma'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def semantic_catalog(catalog):
    """Only descriptor/ranking inputs; never dynamic sensor readings or generation."""
    return {'algorithm': ALGORITHM,
            'areas': sorted([{'area_id': a['area_id'], 'names': sorted({normalized(n) for n in a['names']})}
                             for a in catalog['areas']], key=lambda a: a['area_id']),
            'entities': sorted([{
                'entity_id': r['entity_id'], 'registry_id': r.get('registry_id'),
                'name': normalized(r['name']), 'aliases': sorted({normalized(n) for n in r.get('aliases', [])}),
                'domain': r['domain'], 'device_id': r.get('device_id'), 'area_id': r.get('area_id'),
                'device_name': normalized(r.get('device_name') or ''), 'device_class': r.get('device_class'),
                'category': r.get('attributes', {}).get('semantic_category'),
                'actions': sorted(set(r['actions'])), 'preferred': r.get('preferred') is True,
            } for r in catalog['entities']], key=lambda r: r['entity_id'])}


def semantic_fingerprint(catalog):
    return digest(semantic_catalog(catalog))


def descriptor(row):
    # No action and no room in a device vector. Room is an explicit filter.
    return normalized(' '.join([row['name'], *row.get('aliases', []), row.get('device_name') or '',
                               row.get('device_class') or '', row['domain'],
                               str(row.get('attributes', {}).get('semantic_category') or '')]))


def contains(text, phrase):
    return bool(phrase) and ' ' + phrase + ' ' in ' ' + text + ' '


def fields(text, catalog, context=None):
    context = context or {}
    query = normalized(text)
    refusal = semantic_refusal(text, catalog)
    if not refusal and not hypothesis_allowed(text, catalog):
        refusal = 'semantic_compound'
    area_rows = {a['area_id']: a for a in catalog['areas']}
    explicit = [key for key, a in area_rows.items() if any(contains(query, normalized(n)) for n in a['names'])]
    area, source = None, 'unknown'
    if len(explicit) > 1:
        refusal = refusal or 'multiple_areas'
    elif explicit:
        area, source = explicit[0], 'explicit'
    elif re.search(r'\b(?:de la|la|daquele comodo)\b', query):
        if context.get('last_area') in area_rows and context.get('last_targets'):
            area, source = context['last_area'], 'previous'
        else:
            refusal = refusal or 'missing_previous_area'
    elif context.get('origin_area') in area_rows:
        area, source = context['origin_area'], 'origin'

    action = understood_action(text, catalog)
    action_evidence = 'literal_verb' if action else None
    # Closed constructions from approved user requirements, not fuzzy synonyms.
    constructions = [
        (r'^da uma ligada\b', 'turn_on'),
        (r'^(?:coloca|coloque) .+ (?:pra|para) funcionar$', 'turn_on'),
        (r'^(?:quero|queria|deixa|deixe) .+ (?:acesa|aceso|ligada|ligado)$', 'turn_on'),
        (r'^(?:quero|queria|deixa|deixe) .+ (?:apagada|apagado|desligada|desligado)$', 'turn_off'),
    ]
    for pattern, intent in constructions:
        if re.search(pattern, query):
            if action and action != intent:
                refusal = refusal or 'conflicting_intent'
            action, action_evidence = intent, 'explicit_construction'
            break
    advanced = next((kind for pattern, kind in [
        (r'\b(?:aumenta|diminui|mais clara|mais claro|por cento)\b', 'relative_or_percentage'),
        (r'\b(?:temperatura|graus)\b', 'temperature'),
        (r'\b(?:fecha|feche|abre|abra|metade)\b', 'cover'),
    ] if re.search(pattern, query)), None)
    if advanced and action != 'query':
        refusal = refusal or 'advanced_intent_not_executable'
        action = None
    category = next((key for key, names in CATEGORIES.items() if any(contains(query, n) for n in names)), None)

    remaining = query
    for a in catalog['areas']:
        for name in a['names']:
            remaining = re.sub(r'\b' + re.escape(normalized(name)) + r'\b', ' ', remaining)
    matches, excluded = [], []
    for row in sorted(catalog['entities'], key=lambda r: r['entity_id']):
        labels = [normalized(n) for n in [row['name'], *row.get('aliases', [])]]
        exact = [label for label in labels if contains(query, label)]
        row_description = descriptor(row)
        row_categories = {key for key, names in CATEGORIES.items()
                          if row['domain'] == key or any(contains(row_description, n) for n in names)
                          or row.get('attributes', {}).get('semantic_category') == key}
        head = any(label.split() and label.split()[0] in query.split() for label in labels)
        evidence = 'exact_alias' if exact and head else 'category' if category in row_categories else None
        if exact and not head:
            evidence = 'incidental_name'
        reason = None
        if area is not None and row.get('area_id') != area:
            reason = 'outside_area'
        elif action and action not in row['actions']:
            reason = 'incompatible_capability'
        elif row['domain'] in {'sensor', 'binary_sensor'} and action != 'query':
            reason = 'read_only_entity'
        item = {'entity_id': row['entity_id'], 'evidence': evidence, 'available': row.get('attributes', {}).get('available') is True,
                'device_id': row.get('device_id'), 'preferred': row.get('preferred') is True}
        if reason:
            excluded.append({**item, 'reason': reason})
        else:
            matches.append(item)
        if exact:
            for label in exact:
                remaining = re.sub(r'\b' + re.escape(label) + r'\b', ' ', remaining)
    # Unknown spatial qualifiers never silently fall back to origin.
    if not explicit:
        spatial_query = re.sub(r'^da uma ligada\b', 'liga', query)
        spatial_query = re.sub(r'^da (?:pra|para)\b', 'pode', spatial_query)
        qualifiers = re.findall(r'\b(?:no|na|do|da)\s+(\w+)', spatial_query)
        # An unrelated label elsewhere cannot legitimize an unknown room.
        labels = ' '.join(normalized(n) for r in catalog['entities'] for n in [r['name'], *r.get('aliases', [])]
                          if contains(query, normalized(n)))
        labels += ' ' + ' '.join(n for names in CATEGORIES.values() for n in names if contains(query,n))
        if any(word not in labels.split() and word not in {'comodo', 'aqui'} for word in qualifiers):
            refusal = refusal or 'unknown_explicit_area'
    named = [m for m in matches if m['evidence'] == 'exact_alias']
    grounded = named or [m for m in matches if m['evidence'] == 'category']
    if not grounded:
        typo = []
        by_id = {r['entity_id']: r for r in catalog['entities']}
        for match in matches:
            row = by_id[match['entity_id']]
            heads = [normalized(label).split()[0] for label in [row['name'], *row.get('aliases', [])] if normalized(label)]
            similarity = max((SequenceMatcher(None, word, head).ratio() for word in query.split()
                              for head in heads if len(word) >= 5 and len(head) >= 5), default=0)
            if similarity >= .88:
                typo.append({**match, 'evidence': 'spelling_name', 'lexical_similarity': similarity})
        typo.sort(key=lambda m: (-m['lexical_similarity'], m['entity_id']))
        if typo:
            grounded = [m for m in typo if typo[0]['lexical_similarity'] - m['lexical_similarity'] < .12]
    # Only an explicit preference within one registered physical device removes
    # competition. A shared name alone never deduplicates independent devices.
    if len(grounded) > 1 and len({m['device_id'] for m in grounded}) == 1 and grounded[0]['device_id']:
        preferred = [m for m in grounded if m['preferred']]
        if len(preferred) == 1:
            grounded = preferred
    unique = grounded[0] if len(grounded) == 1 and grounded[0]['available'] else None
    if grounded and not any(m['available'] for m in grounded):
        refusal = refusal or 'target_unavailable'
    device_words = [w for w in remaining.split() if w not in ARTICLES]
    target_fragment = query
    for a in catalog['areas']:
        for name in a['names']:
            target_fragment = re.sub(r'\b' + re.escape(normalized(name)) + r'\b', ' ', target_fragment)
    intent_words = {'liga','ligue','ligar','desliga','desligue','desligar','acende','acenda','acender',
                    'apaga','apague','apagar','coloca','funcionar','deixa','quero','queria','acesa','aceso',
                    'ligado','ligada','desligado','desligada','da','uma','pra','para','pode','aqui','daqui'}
    target_fragment = ' '.join(w for w in target_fragment.split() if w not in intent_words | ARTICLES)
    return {'action': {'exact': action, 'text': ' '.join(device_words) or query, 'evidence': action_evidence,
                       'advanced': advanced},
            'device': {'entity_id': unique['entity_id'] if unique else None, 'category': category,
                       'text': target_fragment or query,
                       'unambiguous': unique is not None, 'candidates': grounded[:16],
                       'candidate_count': len(grounded), 'eligible_count': len(matches),
                       'excluded': excluded[:16], 'excluded_count': len(excluded),
                       'evidence': unique['evidence'] if unique else None},
            'area': {'area_id': area, 'source': source, 'evidence': source},
            'refusal': refusal, 'original_text': text}


def grounded(text, proposal, catalog, context=None):
    result = fields(text, catalog, context)
    return (not result['refusal'] and result['device']['unambiguous']
            and proposal['target_entities'] == [result['device']['entity_id']]
            and (result['action']['exact'] is None or result['action']['exact'] == proposal['intent']))

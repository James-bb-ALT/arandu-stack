"""Field-separated ranking with model-native embeddings; no HA execution."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import time

from .contracts import ACTION_TEXT, digest, finite, settings
from .decision_context import ALGORITHM, CALIBRATION, descriptor, fields, semantic_fingerprint
from .semantic_context import decomposition, confirmation_question, clearly_conversational, speech_only_request

INTENTS = {
    'turn_on': 'Ligar acender ativar colocar para funcionar deixar ligado aceso',
    'turn_off': 'Desligar apagar desativar deixar desligado apagado',
    'query': 'Consultar perguntar qual é o estado a temperatura de um dispositivo',
}


def model_binding(signature):
    return digest({'weights': signature, 'algorithm': CALIBRATION, 'intents': INTENTS})


def operational_scope(catalog):
    return digest(sorted((r['entity_id'], sorted(set(r['actions']) & INTENTS.keys()))
                         for r in catalog['entities'] if r.get('attributes', {}).get('available') is True))


def calibration_path(root, key="e5-small", backend="cpu"):
    from .installation import signature
    return Path(root)/(key+"-"+backend+"-structured-v3-"+signature(key,backend)[:20]+"-calibration.json")


def load_calibration(root,key="e5-small",backend="cpu"):
    path = calibration_path(root,key,backend)
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (OSError, ValueError):
        return None


def binding_valid(calibration, signature, catalog):
    return (type(calibration) is dict and calibration.get('version') == CALIBRATION
            and calibration.get('model') == model_binding(signature)
            and calibration.get('semantic_catalog') == semantic_fingerprint(catalog)
            and calibration.get('validated') is True
            and type(calibration.get('provenance')) is dict
            and type(calibration.get('strata')) is dict
            and calibration.get('provenance', {}).get('representative') is True)


def lower_bound(correct, n):
    if not n:
        return 0.0
    p, z = correct/n, 1.96
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)))/(1+z*z/n)


def policy(ranking, signature, catalog, config, calibration):
    threshold, margin_min = settings(config)
    margin_min = max(.08, margin_min)
    if any(not finite(r['raw_score']) or not -1 <= r['raw_score'] <= 1 for r in ranking):
        raise ValueError('Score inválido')
    ranking = sorted(ranking, key=lambda r: (-r['raw_score'], r['entity_id'], r['intent']))
    if not ranking:
        return {'decision_status': 'rejected', 'reason': 'no_eligible_candidate', 'ranking': [],
                'proposal': None, 'confidence': None, 'margin': None, 'raw_score': None}
    top = ranking[0]
    margin = top['raw_score'] - ranking[1]['raw_score'] if len(ranking) > 1 else 1.0
    valid = binding_valid(calibration, signature, catalog)
    scope_changed = bool(valid and calibration.get('operational_scope') != operational_scope(catalog))
    valid = bool(valid and not scope_changed)
    confidence = None
    if valid:
        index = min(4, max(0, int(top['raw_score']*5)))
        stratum = calibration.get('strata', {}).get(config.get('stratum', 'semantic_intent'), {})
        cells = [stratum.get('bins', []), stratum.get('validation_bins', [])]
        if all(type(rows) is list and len(rows) == 5 for rows in cells):
            train, validation = cells[0][index], cells[1][index]
            if all(type(c) is dict and type(c.get('n')) is int and type(c.get('correct')) is int
                   and c['n'] >= 30 and 0 <= c['correct'] <= c['n'] for c in (train, validation)):
                confidence = min(lower_bound(c['correct'], c['n']) for c in (train, validation))
    state, reason = 'uncalibrated', 'operational_scope_changed' if scope_changed else 'structured_calibration_missing'
    if margin < margin_min:
        state, reason = 'ambiguous', 'insufficient_margin'
    elif confidence is not None:
        state = 'accepted' if confidence >= threshold else 'rejected'
        reason = 'structured_calibrated' if state == 'accepted' else 'below_threshold'
    return {'decision_status': state, 'reason': reason, 'ranking': ranking[:8], 'raw_score': top['raw_score'],
            'confidence': confidence, 'margin': margin, 'calibration_valid': valid,
            'proposal': proposal(top['intent'], top['entity_id'], reason, confidence) if state == 'accepted' else None}


def proposal(action, entity, reason, confidence=None, question=None):
    return {'intent': action, 'target_entities': [entity], 'target_area': None,
            'confidence': confidence, 'requires_clarification': question is not None,
            'clarification_question': question, 'reason': reason}


def infer(encoder, text, catalog, key, config, context=None, *, rank_only=False):
    started, cpu = time.perf_counter(), time.process_time()
    analysis = fields(text, catalog, context)
    prepared = time.perf_counter()
    base = {'algorithm': ALGORITHM, 'contract_version': 2, 'original_text': text, 'analysis': analysis,
            'semantic_fingerprint': semantic_fingerprint(catalog), 'catalog_fingerprint': digest(catalog),
            'model': key, 'model_fingerprint': None, 'raw_score': None, 'margin': None, 'confidence': None,
            'proposal': None, 'ranking': [], 'embedding_used': False, 'confirmation_required': False,
            'load_ms': 0, 'retrieval_ms': 0, 'inference_ms': 0, 'analysis_ms': 0, 'ranking_ms':0,
            'preparation_ms': (prepared-started)*1000, 'validation_ms': 0,
            'cache': {'descriptor_hits': 0, 'descriptor_misses': 0, 'query_hits': 0, 'query_misses': 0}}
    def finish(state, reason):
        base.update(decision_status=state, reason=reason, total_ms=(time.perf_counter()-started)*1000,
                    cpu_ms=(time.process_time()-cpu)*1000)
        try:
            import psutil
            base['memory_bytes'] = psutil.Process().memory_info().rss
        except ImportError:
            base['memory_bytes'] = None
        return base
    if analysis['refusal']:
        return finish('rejected', analysis['refusal'])
    if clearly_conversational(text, catalog) or speech_only_request(text):
        return finish('rejected', 'conversation_not_action')
    action, entity = analysis['action']['exact'], analysis['device']['entity_id']
    rows = {r['entity_id']: r for r in catalog['entities']}
    if not rank_only and action and entity:
        if analysis['device']['evidence'] == 'spelling_name':
            question = confirmation_question(action, rows[entity], catalog)
            base.update(proposal=proposal(action, entity, 'spelling_target_hypothesis', question=question), confirmation_required=True)
            return finish('confirmation_required', 'spelling_target_hypothesis')
        base.update(proposal=proposal(action, entity, 'explicit_fields_and_unique_target'), grounding='deterministic-v2')
        return finish('resolved', 'explicit_fields_and_unique_target')
    if not rank_only and analysis['device']['candidate_count'] > 1:
        return finish('ambiguous', 'multiple_grounded_targets')
    if not rank_only and not action and entity:
        spelling = decomposition(text, catalog, context)['action']['ranking']
        if spelling[0]['lexical_similarity'] >= .8 and spelling[0]['lexical_similarity']-spelling[1]['lexical_similarity'] >= .15:
            intent = spelling[0]['intent']
            if intent in rows[entity]['actions']:
                question = confirmation_question(intent, rows[entity], catalog)
                base.update(proposal=proposal(intent, entity, 'spelling_hypothesis', question=question), confirmation_required=True)
                return finish('confirmation_required', 'spelling_hypothesis')
    signature = encoder.load(key)
    base.update(load_ms=encoder.load_ms, model_fingerprint=model_binding(signature), embedding_used=True)
    before = time.perf_counter()
    # The bounded diagnostic list is not the candidate universe; filter the full catalog independently.
    area = analysis['area']['area_id']
    eligible = [r for r in catalog['entities'] if r.get('attributes', {}).get('available') is True
                and (area is None or r.get('area_id') == area)]
    pairs = [(r, intent) for r in eligible for intent in sorted(set(r['actions']) & INTENTS.keys())
             if (action is None or intent == action) and (intent == 'query' or r['domain'] not in {'sensor','binary_sensor'})]
    row_descriptors = {r['entity_id']:descriptor(r) for r in eligible}
    descriptions = list(dict.fromkeys([row_descriptors[r['entity_id']] for r, _ in pairs] + list(INTENTS.values())))
    if len(encoder.cache) + len(descriptions) > 32768:
        encoder.cache.clear()
    missing = [d for d in descriptions if 'v3:'+d not in encoder.cache]
    base['cache'].update(descriptor_hits=len(descriptions)-len(missing), descriptor_misses=len(missing))
    if missing:
        embeddings = encoder.encode(missing)
        encoder.cache.update(('v3:'+d, v) for d, v in zip(missing, embeddings))
    base['retrieval_ms'] = (time.perf_counter()-before)*1000
    before = time.perf_counter()
    texts = [analysis['action']['text'], analysis['device']['text']]
    unique = list(dict.fromkeys(texts))
    absent = [q for q in unique if 'v3:'+q not in encoder.query_cache]
    base['cache'].update(query_hits=len(unique)-len(absent), query_misses=len(absent))
    if len(encoder.query_cache)+len(absent) > 128:
        encoder.query_cache.clear()
        absent = unique
    if absent:
        vectors = encoder.encode(absent,query=True)
        encoder.query_cache.update(('v3:'+q,v) for q,v in zip(absent,vectors))
    import numpy as np
    ranking_started=time.perf_counter()
    action_query, device_query = [encoder.query_cache['v3:'+q] for q in texts]
    ranking = []
    for row, intent in pairs:
        a = float(np.dot(action_query, encoder.cache['v3:'+INTENTS[intent]]))
        d = float(np.dot(device_query, encoder.cache['v3:'+row_descriptors[row['entity_id']]]))
        ranking.append({'entity_id': row['entity_id'], 'intent': intent, 'area_id': row.get('area_id'),
                        'raw_score': max(-1., min(1., (a+d)/2)), 'intent_score': a, 'target_score': d,
                        'reason': 'mean of separate intent and entity cosine fields'})
    base['ranking_ms']=(time.perf_counter()-ranking_started)*1000
    base['inference_ms'] = (time.perf_counter()-before)*1000
    before = time.perf_counter()
    stratum = 'explicit_intent' if action else 'semantic_intent'
    base.update(policy(ranking, signature, catalog, {**config,'stratum':stratum}, load_calibration(encoder.root,key,encoder.backend_name)))
    base['calibration_stratum'] = stratum
    base.update(eligible_pairs=len(pairs), validation_ms=(time.perf_counter()-before)*1000)
    if not rank_only and base['ranking']:
        top = base['ranking'][0]
        # A semantic winner without independent entity evidence is diagnostic only.
        if entity != top['entity_id']:
            base.update(proposal=None, confidence=None)
            return finish('ambiguous' if analysis['device']['candidate_count'] > 1 else 'rejected', 'target_not_independently_grounded')
        if base['decision_status'] == 'accepted' and analysis['device']['evidence'] != 'spelling_name':
            return finish('accepted', base['reason'])
        if base['decision_status'] == 'uncalibrated' and base['raw_score'] >= .75 and base['margin'] >= max(.08, config.get('min_margin', .08)):
            question = confirmation_question(top['intent'], rows[entity], catalog)
            base.update(proposal=proposal(top['intent'], entity, 'uncalibrated_field_hypothesis', question=question), confirmation_required=True)
            return finish('confirmation_required', 'uncalibrated_field_hypothesis')
    return finish(base['decision_status'], base['reason'])


def calibrate(encoder, catalog, key, train, validation, provenance):
    if not 50 <= len(train) or not 30 <= len(validation) or len(train)+len(validation) > 500:
        raise ValueError('Use ≥50 exemplos de treino e ≥30 de validação; máximo 500.')
    from .semantic_context import normalized
    labels = train+validation
    if len({normalized(r['text']) for r in labels}) != len(labels):
        raise ValueError('Treino e validação devem ser independentes após normalização')
    ids = {r['entity_id'] for r in catalog['entities']}
    for sample in labels:
        if (type(sample.get('text')) is not str or not 1 <= len(sample['text']) <= 512
                or type(sample.get('requires_clarification')) is not bool
                or sample.get('expected_intent') not in (*INTENTS, None)
                or type(sample.get('expected_entities')) is not list
                or len(sample['expected_entities']) > 1 or any(e not in ids for e in sample['expected_entities'])):
            raise ValueError('Rótulo ou alvo de calibração inválido')
        if not sample['requires_clarification'] and (not sample['expected_entities'] or sample['expected_intent'] is None):
            raise ValueError('Exemplo positivo exige intenção e entidade')
    provenance = provenance or {}
    if type(provenance) is not dict:
        raise ValueError('Metadados de proveniência inválidos')
    representative = provenance.get('representative') is True and provenance.get('kind') == 'representative'
    if representative and (provenance.get('license') not in {'Apache-2.0','MIT','CC0-1.0','CC-BY-4.0'}
                           or type(provenance.get('source')) is not str or not 1 <= len(provenance['source']) <= 256):
        raise ValueError('Informe fonte e licença livres do conjunto representativo')
    bins = [{'n':0,'correct':0} for _ in range(5)]
    checked = [{'n':0,'correct':0} for _ in range(5)]
    strata = {kind:{'bins':[{'n':0,'correct':0} for _ in range(5)],
                   'validation_bins':[{'n':0,'correct':0} for _ in range(5)]}
              for kind in ('explicit_intent','semantic_intent')}
    measured = []
    for samples, cells in [(train,bins),(validation,checked)]:
        for sample in samples:
            result = infer(encoder, sample['text'], catalog, key, {}, sample.get('context'), rank_only=True)
            top = result['ranking'][0] if result['ranking'] else None
            correct = bool(top and not sample['requires_clarification'] and sample['expected_intent'] == top['intent']
                           and sample['expected_entities'] == [top['entity_id']])
            index = min(4,max(0,int((top['raw_score'] if top else 0)*5)))
            cells[index]['n'] += 1
            cells[index]['correct'] += int(correct)
            kind = 'explicit_intent' if result['analysis']['action']['exact'] else 'semantic_intent'
            cell = strata[kind]['bins' if samples is train else 'validation_bins'][index]
            cell['n'] += 1; cell['correct'] += int(correct)
            if samples is validation:
                measured.append((kind,index,correct,result.get('margin') or 0))
    covered = [r for r in measured if strata[r[0]]['bins'][r[1]]['n'] >= 30 and strata[r[0]]['validation_bins'][r[1]]['n'] >= 30]
    ece = sum(c['n']*abs(b['correct']/b['n']-c['correct']/c['n']) for cells in strata.values()
              for b,c in zip(cells['bins'],cells['validation_bins'])
              if b['n'] >= 30 and c['n'] >= 30)/len(covered) if covered else None
    brier = sum((strata[k]['bins'][i]['correct']/strata[k]['bins'][i]['n']-int(correct))**2 for k,i,correct,_ in covered)/len(covered) if covered else None
    accepted = [(k,i,c,m) for k,i,c,m in covered if min(lower_bound(cell['correct'],cell['n']) for cell in (strata[k]['bins'][i],strata[k]['validation_bins'][i])) >= .8 and m >= .08]
    false_accepts = sum(not correct for _,_,correct,_ in accepted)
    valid = bool(representative and len(covered) >= 30 and ece is not None and ece <= .1 and not false_accepts and accepted)
    result = {'version':CALIBRATION,'model':model_binding(encoder.key), 'semantic_catalog':semantic_fingerprint(catalog),
              'bins':bins,'validation_bins':checked,'strata':strata,'operational_scope':operational_scope(catalog),
              'validation_n':len(validation),'ece':ece,'brier':brier,
              'accuracy':sum(c for _,_,c,_ in measured)/len(measured), 'coverage':len(accepted)/len(validation),
              'false_acceptances':false_accepts,'covered_n':len(covered),'validated':valid,
              'provenance':{'representative':representative,'kind':provenance.get('kind','technical_fixture'),
                            'license':provenance.get('license'),'source':provenance.get('source')},
              'dataset':digest({'train':train,'validation':validation}),
              'confidence_method':'minimum Wilson 95% lower bound of independent train/validation bins'}
    encoder.root.mkdir(parents=True,exist_ok=True)
    path = calibration_path(encoder.root,key,encoder.backend_name)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=encoder.root,delete=False) as file:
        temporary = Path(file.name)
        json.dump(result,file,ensure_ascii=False,allow_nan=False)
        file.flush(); os.fsync(file.fileno())
    try:
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    return result

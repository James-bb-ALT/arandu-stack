import math
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from .normalizer import norm
from .phonetic import phonetic


def evidence(vocabulary, config, raw, key):
    raw_key = norm(raw)
    if key not in vocabulary.terms:
        return {'score': 1.0, 'orthographic': 1.0, 'phonetic': 1.0, 'edit': 1.0,
                'source_prior': 0.0, 'frequency_prior': 0.0, 'dictionary': 0.0,
                'sources': ['numeric_rule'], 'exact': True}
    term = vocabulary.terms[key]
    orth = fuzz.ratio(raw_key, key) / 100
    ph = fuzz.ratio(phonetic(raw_key), phonetic(key)) / 100
    edit = Levenshtein.normalized_similarity(raw_key, key)
    # A bounded source advantage requires plausible textual/phonetic evidence.
    strength = (orth + ph) / 2
    gate = max(0.0, min(1.0, (strength - 0.55) / 0.45))
    weight = max(config['source_weights'][s] for s in term.sources)
    prior = weight / (weight + 10.0) * gate
    frequency = math.log1p(term.frequency) / math.log1p(vocabulary.max_frequency)
    if key in vocabulary.functional:
        prior = frequency = 0.0
    dictionary = float(key in vocabulary.dictionary)
    w = config['score_weights']
    score = w['orthographic'] * orth + w['phonetic'] * ph + w['edit'] * edit
    # Priors capped independently of user-configured similarity coefficients.
    score += min(w['source'], .12) * prior + min(w['frequency'], .05) * frequency * gate
    score += min(w['dictionary'], .03) * dictionary * gate
    return {'score': score, 'orthographic': orth, 'phonetic': ph, 'edit': edit,
            'source_prior': prior, 'frequency_prior': frequency, 'dictionary': dictionary,
            'sources': sorted(term.sources), 'exact': raw_key == key}


def confidence(chosen, margin):
    """Heuristic evidence quality, not calibrated probability or ASR confidence."""
    if chosen['exact']:
        return .98
    strength = .5 * chosen['orthographic'] + .3 * chosen['phonetic'] + .2 * chosen['edit']
    return max(.01, min(.95, strength * (.55 + .45 * min(1.0, max(0.0, margin) / .15))))


def label(value):
    return 'alta' if value >= .85 else 'media' if value >= .65 else 'baixa'

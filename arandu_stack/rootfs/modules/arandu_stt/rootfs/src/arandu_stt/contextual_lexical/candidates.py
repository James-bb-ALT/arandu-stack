"""Bounded n-gram postings plus phonetic buckets, no vocabulary scan per token."""
from collections import Counter, defaultdict
from functools import lru_cache
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from .normalizer import norm, dynamic_allowed
from .phonetic import phonetic


def grams(word):
    padded = '^' + word + '$'
    return set(padded[i:i+2] for i in range(len(padded)-1))


class CandidateIndex:
    def __init__(self, vocabulary, config, confusions=None):
        self.vocabulary = vocabulary
        self.config = config
        self.postings = defaultdict(list)
        self.phonetics = defaultdict(list)
        self.prefixes = defaultdict(list)
        self.aliases = {norm(k): norm(v) for k, v in (confusions or {}).items() if norm(v) in vocabulary.terms}
        for key in sorted(vocabulary.terms):
            for gram in grams(key):
                self.postings[gram].append(key)
            self.phonetics[phonetic(key)].append(key)
            self.prefixes[key[:1]].append(key)
        # Frequent buckets are bounded once at build time. No linear query over a common gram.
        bound = config['max_index_pool']
        def ordering(k):
            return (-vocabulary.terms[k].frequency, k)
        self.postings = {g: tuple(sorted(items, key=ordering)[:bound]) for g, items in self.postings.items()}
        self.phonetics = {p: tuple(sorted(items, key=ordering)[:bound]) for p, items in self.phonetics.items()}
        self.prefixes = {p: tuple(sorted(items, key=ordering)[:bound]) for p, items in self.prefixes.items()}
        self.fallback = tuple(sorted(vocabulary.terms, key=ordering)[:config['candidates_per_token']])
        self.lookup = lru_cache(maxsize=8192)(self._lookup)

    def _lookup(self, raw):
        key = norm(raw)
        # Preserve valid words, negation, inflection, and proper names as hard anchors.
        if self.vocabulary.allowed(raw):
            return (key,)
        hits = Counter()
        for gram in sorted(grams(key)):
            hits.update(self.postings.get(gram, ()))
        pool = set(k for k, _ in hits.most_common(self.config['max_index_pool']))
        pool.update(self.phonetics.get(phonetic(key), ()))
        if key in self.aliases:
            pool.add(self.aliases[key])
        if not pool:
            pool.update(self.prefixes.get(key[:1], self.fallback))
        if not pool:
            return ()
        def similarity(candidate):
            return (0.6 * fuzz.ratio(key, candidate) +
                    0.25 * fuzz.ratio(phonetic(key), phonetic(candidate)) +
                    15 * Levenshtein.normalized_similarity(key, candidate))
        return tuple(sorted(pool, key=lambda k: (-similarity(k), k))[:self.config['candidates_per_token']])

"""Build a complete immutable-after-publication vocabulary snapshot."""
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path

from .errors import LexicalError
from .normalizer import norm, words, PLACEHOLDERS, dynamic_allowed

SOURCES = {
    'generic_lexicon': ('lexicon/arandu_lexico_generico_palavras_ptbr.csv', ('palavra',)),
    'household': ('household/arandu_household_entities_ptbr.csv', ('nome_canonico', 'sinonimos_variacoes_ptbr')),
    'names': ('names/arandu_nomes_comuns_brasil.csv', ('nome_canonico', 'variantes_graficas', 'variantes_componentes', 'apelidos_comuns')),
    'commands': ('commands/arandu_comandos_comuns_ptbr_v2.csv', ('frase_exemplo', 'frase_template')),
}
VOICE_DOMAINS = {'light', 'fan', 'climate', 'media_player', 'switch', 'cover', 'lock',
                 'vacuum', 'scene', 'script', 'automation', 'sensor', 'binary_sensor',
                 'person', 'input_boolean', 'input_button', 'button', 'select', 'number',
                 'timer', 'camera', 'remote', 'humidifier', 'water_heater', 'todo'}


@dataclass
class Term:
    display: str
    sources: set = field(default_factory=set)
    surfaces: set = field(default_factory=set)
    frequency: int = 0


def inventory_phrases(inventory: dict):
    """Names only: never IDs, credentials, manufacturers, or arbitrary nested values."""
    for group in ('areas', 'floors', 'devices', 'entities', 'persons', 'scenes', 'scripts', 'automations'):
        for record in inventory.get(group, []):
            if record.get('disabled') or record.get('hidden') or record.get('entity_category') in ('config', 'diagnostic'):
                continue
            domain = record.get('domain') or record.get('entity_id', '').split('.')[0]
            if group == 'entities' and domain not in VOICE_DOMAINS:
                continue
            for key in ('name_by_user', 'name', 'original_name', 'friendly_name', 'area', 'floor'):
                value = record.get(key)
                if isinstance(value, str) and value and not ('.' in value and ' ' not in value):
                    yield value
            for key in ('aliases', 'assist_aliases'):
                values = record.get(key) or []
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    if isinstance(value, str):
                        yield value


class Vocabulary:
    def __init__(self, datasets: Path, inventory: dict, config: dict):
        self.terms: dict[str, Term] = {}
        self.bigrams = Counter()
        self.trigrams = Counter()
        self.functional = set()
        self.compounds = {}
        self.local_surfaces = {}
        self.phrase_entities = defaultdict(set)
        self.audit = {}
        self.config = config
        self._seen = set()
        try:
            functional = (datasets / 'functional_ptbr.txt').read_text(encoding='utf-8').splitlines()
            for line in functional:
                if line.strip() and not line.startswith('#'):
                    for token in words(line):
                        self.functional.add(norm(token))
                    self.add_phrase(line, 'functional')
            for source, (relative, fields) in SOURCES.items():
                path = datasets / relative
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    reader = csv.DictReader(stream)
                    if not reader.fieldnames or fields[0] not in reader.fieldnames:
                        raise LexicalError('invalid_dataset', 'Coluna obrigatória ausente: ' + source)
                    count = duplicates = 0
                    for row in reader:
                        count += 1
                        for name in fields:
                            for phrase in (row.get(name) or '').split('|'):
                                if phrase.strip() and not self.add_phrase(phrase, source):
                                    duplicates += 1
                    self.audit[source] = {'rows': count, 'duplicate_phrases_skipped': duplicates,
                                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                if not count:
                    raise LexicalError('empty_dataset', 'Dataset vazio: ' + source)
            for phrase in inventory_phrases(inventory):
                self.add_phrase(phrase, 'home_assistant')
            for entity in inventory.get('entities', []):
                if entity.get('disabled') or entity.get('hidden') or entity.get('entity_category') in ('config', 'diagnostic'):
                    continue
                if entity.get('domain', entity.get('entity_id', '').split('.')[0]) not in VOICE_DOMAINS:
                    continue
                eid = entity.get('entity_id')
                if not eid:
                    continue
                for phrase in inventory_phrases({'entities': [entity]}):
                    key = tuple(norm(t) for t in words(phrase))
                    if 1 <= len(key) <= 16:
                        self.phrase_entities[key].add(eid)
            for source, file in (('household', 'supplemental_ptbr.txt'),):
                for line in (datasets / file).read_text(encoding='utf-8').splitlines():
                    if line and not line.startswith('#'):
                        self.add_phrase(line, source)
            if not self.terms:
                raise LexicalError('empty_vocabulary', 'Vocabulário indisponível')
            self.max_frequency = max(x.frequency for x in self.terms.values()) or 1
            self.max_bigram = max(self.bigrams.values(), default=1)
            self.max_trigram = max(self.trigrams.values(), default=1)
            self.bigram_max_by_left = defaultdict(lambda: 1)
            self.trigram_max_by_prefix = defaultdict(lambda: 1)
            for (left, _), count in self.bigrams.items():
                self.bigram_max_by_left[left] = max(self.bigram_max_by_left[left], count)
            for (a, b, _), count in self.trigrams.items():
                self.trigram_max_by_prefix[a, b] = max(self.trigram_max_by_prefix[a, b], count)
            self.dictionary = set()
            if config['wordlist']:
                for line in Path(config['wordlist']).read_text(encoding='utf-8').splitlines():
                    self.dictionary.add(norm(line.split('/')[0].strip()))
        except OSError as exc:
            raise LexicalError('missing_resource', 'Dataset ou recurso lexical obrigatório indisponível') from exc

    def add_phrase(self, text, source):
        text = PLACEHOLDERS.sub(' ', text.strip())
        tokens = words(text)
        keys = tuple(norm(t) for t in tokens)
        if not keys:
            return False
        duplicate = (source, keys) in self._seen
        self._seen.add((source, keys))
        if source == 'home_assistant' and 2 <= len(keys) <= 16:
            self.local_surfaces.setdefault(keys, tuple(tokens))
        for token, key in zip(tokens, keys):
            if dynamic_allowed(token):
                continue
            entry = self.terms.setdefault(key, Term(token))
            entry.sources.add(source)
            entry.surfaces.add(token)
            if not duplicate:
                entry.frequency += 1
            # Accent restoration; Brazilian names retain their canonical capitalization.
            if ((source == 'names' and token[:1].isupper() and not entry.display[:1].isupper())
                    or (norm(entry.display) == entry.display.casefold() and norm(token) != token.casefold())):
                entry.display = token
            if '-' in token:
                self.compounds[tuple(norm(part) for part in token.split('-'))] = key
        if not duplicate and source != 'functional':
            self.bigrams.update(zip(keys, keys[1:]))
            self.trigrams.update(zip(keys, keys[1:], keys[2:]))
        return not duplicate

    def allowed(self, token):
        return norm(token) in self.terms or dynamic_allowed(token)

    def display(self, key, original):
        if key not in self.terms:
            return original
        term = self.terms[key]
        if original in term.surfaces:
            return original
        if 'names' in term.sources and term.display[:1].isupper():
            for surface in sorted(term.surfaces):
                if surface.casefold() == original.casefold():
                    return surface
            return term.display
        return term.display

    def context(self, sequence, candidate, next_raw=None):
        bi = self.bigrams[sequence[-1], candidate] if sequence else 0
        tri = self.trigrams[sequence[-2], sequence[-1], candidate] if len(sequence) > 1 else 0
        ahead = self.bigrams[candidate, next_raw] if next_raw else 0
        # Relative counts under the same prefix preserve rare useful names/phrases.
        # These features are bounded heuristics, not language-model probabilities.
        return (math.log1p(bi) / math.log1p(self.bigram_max_by_left.get(sequence[-1], 1)) if sequence else 0.0,
                math.log1p(tri) / math.log1p(self.trigram_max_by_prefix.get(tuple(sequence[-2:]), 1)) if len(sequence) > 1 else 0.0,
                math.log1p(ahead) / math.log1p(self.bigram_max_by_left.get(candidate, 1)))


def read_inventory(path: Path):
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError('Inventário inválido')
        for key in ('entities', 'areas', 'devices', 'persons', 'floors', 'scenes', 'scripts', 'automations'):
            if key in result and (not isinstance(result[key], list) or any(not isinstance(x, dict) for x in result[key])):
                raise ValueError('Inventário inválido')
        return result
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        raise LexicalError('invalid_inventory', 'Inventário HA inválido') from exc

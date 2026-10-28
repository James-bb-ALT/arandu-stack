"""Arandu 2.0: acoustic-model-independent contextual correction."""
from dataclasses import dataclass
import logging
import hashlib
import json
from pathlib import Path
import threading
import time

from .candidates import CandidateIndex
from .config import load_config
from .context import Vocabulary, read_inventory
from .decoder import Decoder
from .errors import LexicalError

LOG = logging.getLogger('arandu.lexical')


@dataclass(frozen=True)
class Snapshot:
    decoder: Decoder | None
    legacy: object
    generation: int
    build_ms: float


class ContextualResolver:
    """Same apply/stats contract; each transcription holds one complete snapshot."""
    def __init__(self, inventory_path: Path, resources_dir: Path,
                 datasets_dir: Path | None = None, config_path: Path | None = None,
                 overrides: dict | None = None):
        self.inventory_path = inventory_path
        self.resources_dir = resources_dir
        self.datasets_dir = datasets_dir or resources_dir.parent / 'datasets'
        self.config = load_config(config_path, overrides)
        self._build_lock = threading.Lock()
        self._snapshot = self._build(self._startup_inventory(), 1)
        self._inventory_digest = None

    def _startup_inventory(self):
        try:
            return read_inventory(self.inventory_path)
        except LexicalError:
            LOG.warning('Inventário HA inválido; usando vocabulário genérico')
            return {}

    def _build(self, inventory, generation):
        from lexical import AranduLexicalResolver
        started = time.perf_counter()
        legacy = AranduLexicalResolver(self.inventory_path, self.resources_dir, inventory=inventory)
        if not self.config['enabled']:
            return Snapshot(None, legacy, generation, (time.perf_counter()-started)*1000)
        vocabulary = Vocabulary(self.datasets_dir, inventory, self.config)
        aliases = legacy.confusions.get('words', {})
        index = CandidateIndex(vocabulary, self.config, aliases)
        decoder = Decoder(vocabulary, index, self.config)
        return Snapshot(decoder, legacy, generation, (time.perf_counter()-started)*1000)

    def refresh(self, inventory=None):
        # Serialize builders; readers never acquire this lock.
        with self._build_lock:
            if inventory is None:
                inventory = read_inventory(self.inventory_path)
            digest = hashlib.sha256(json.dumps({k: v for k, v in inventory.items() if k != '_meta'}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest == self._inventory_digest:
                return False
            snapshot = self._build(inventory, self._snapshot.generation+1)
            self._snapshot = snapshot
            self._inventory_digest = digest
        LOG.info('Contexto lexical atualizado: geração=%s build_ms=%.1f', snapshot.generation, snapshot.build_ms)
        return True

    def apply(self, text: str, profile: str = 'full'):
        snapshot = self._snapshot
        if profile != 'full' or not self.config['enabled']:
            selected = 'full' if profile == 'legacy_full' else profile
            result = snapshot.legacy.apply(text, selected)
            result.update(confidence_kind='unavailable_legacy', confidence_is_calibrated_probability=False,
                          closed_vocabulary=False, context_generation=snapshot.generation)
            return result
        result = snapshot.decoder.correct(text)
        result['context_generation'] = snapshot.generation
        return result

    def stats(self):
        snapshot = self._snapshot
        if snapshot.decoder is None:
            return {**snapshot.legacy.stats(), 'version': '2.0.0', 'lexical_enabled': False,
                    'context_generation': snapshot.generation, 'build_ms': round(snapshot.build_ms, 2)}
        vocabulary = snapshot.decoder.vocabulary
        return {'version': '2.0.0', 'lexicon_entries': len(vocabulary.terms),
                'context_generation': snapshot.generation, 'build_ms': round(snapshot.build_ms, 2),
                'source_tokens': {source: sum(source in term.sources for term in vocabulary.terms.values())
                                  for source in self.config['source_weights']}, 'datasets': vocabulary.audit}


__all__ = ['ContextualResolver', 'LexicalError']

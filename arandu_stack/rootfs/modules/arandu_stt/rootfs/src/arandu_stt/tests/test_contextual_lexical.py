import copy
import csv
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from contextual_lexical import ContextualResolver, LexicalError
from contextual_lexical.config import load_config
from contextual_lexical.context import Vocabulary
from contextual_lexical.normalizer import spans, dynamic_allowed, norm
from contextual_lexical.phonetic import phonetic
from contextual_lexical.scoring import evidence
from contextual_lexical.candidates import CandidateIndex
from contextual_lexical.decoder import Decoder


class TestContextualLexical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')

    def correct(self, text):
        return self.resolver.apply(text)

    def test_supplied_examples(self):
        self.assertEqual(self.correct('liga o ventulador do escritorio de jaime')['text'], 'liga o ventilador do escritório de Jaime')
        self.assertEqual(self.correct('qual a temperatua do quarto')['text'], 'qual a temperatura do quarto')

    def test_names_dataset_forms_and_nicknames_are_preserved(self):
        with (HERE/'datasets/names/arandu_nomes_comuns_brasil.csv').open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                for field in ('nome_canonico', 'variantes_graficas', 'variantes_componentes', 'apelidos_comuns'):
                    for text in (row.get(field) or '').split('|'):
                        if text.strip():
                            with self.subTest(text=text):
                                result = self.correct(text.strip())
                                self.assertEqual(result['text'].casefold(), text.strip().casefold())
                                self.assertTrue(result['all_tokens_allowed'])

    def test_duplicate_name_grafias_merge_without_frequency_inflation(self):
        vocab = Vocabulary(HERE/'datasets', {}, load_config())
        self.assertTrue(vocab.add_phrase('Zélquarnome', 'names'))
        frequency = vocab.terms['zelquarnome'].frequency
        self.assertFalse(vocab.add_phrase('Zelquarnome', 'names'))
        self.assertEqual(vocab.terms['zelquarnome'].frequency, frequency)
        self.assertEqual(vocab.display('zelquarnome', 'Zélquarnome'), 'Zélquarnome')
        self.assertEqual(vocab.display('zelquarnome', 'Zelquarnome'), 'Zelquarnome')

    def test_valid_words_names_and_inflections_are_anchors(self):
        for text in ('não desliga o ventilador', 'liga as luzes dos quartos', 'Pedro e Rafael',
                     'hoje estudei história brasileira', 'amanhã vou ler um livro',
                     'gosto de música e cinema', 'a poda', 'liga lava-louças', 'como está a lava e seca'):
            with self.subTest(text=text):
                self.assertEqual(self.correct(text)['text'], text)

    def test_informal_grammar(self):
        for text in ('liga aí o ventilador', 'bota o ventilador pra funcionar', 'acende a luz daqui',
                     'tá fazendo quantos graus no quarto', 'onde tá meu telefone', 'bota meu Spotify pra tocar'):
            self.assertEqual(self.correct(text)['text'], text)

    def test_forced_unknown_stays_low_confidence(self):
        result = self.correct('liga o xyzqvv')
        token = result['tokens'][-1]
        self.assertTrue(token['forced'])
        self.assertEqual(token['confidence_label'], 'baixa')
        self.assertTrue(result['low_confidence'])
        self.assertFalse(result['confidence_is_calibrated_probability'])
        self.assertIn('score', token['alternatives'][0])
        self.assertIn('sources', token['alternatives'][0]['evidence'])

    def test_semantic_compatibility_keeps_valid_word(self):
        result = self.correct('destranca a porda')
        self.assertEqual(result['text'], 'destranca a porta')
        self.assertGreater(result['tokens'][-1]['context']['semantic'], 0)
        self.assertTrue(result['low_confidence'])
        self.assertEqual(self.correct('destranca a poda')['text'], 'destranca a poda')

    def test_closed_invariant_random_oov(self):
        rng = random.Random(42)
        vocab = self.resolver._snapshot.decoder.vocabulary
        for _ in range(120):
            text = 'liga o ' + ''.join(rng.choice('abdjkrvxz') for _ in range(rng.randint(3, 14)))
            result = self.correct(text)
            self.assertTrue(result['all_tokens_allowed'])
            self.assertTrue(all(vocab.allowed(x.original) for x in spans(result['text'])))

    def test_compounds_punctuation_and_numeric_rules(self):
        text = 'Liga o ar condicionado às 19:30, ajusta para -2,5°C e 50%.'
        result = self.correct(text)
        self.assertEqual(result['text'].casefold(), 'liga o ar-condicionado às 19:30, ajusta para -2,5°c e 50%.')
        self.assertTrue(result['all_tokens_allowed'])
        self.assertEqual(self.correct('Wi-Fi')['text'], 'Wi-Fi')
        self.assertTrue(dynamic_allowed('23:59'))
        self.assertFalse(dynamic_allowed('24:00'))
        for text in ('às 99:99', 'às 8:5', 'às 1234:45', 'liga {segredo}', 'liga 🚀'):
            with self.assertRaises(LexicalError):
                self.correct(text)

    def test_empty_input(self):
        self.assertEqual(self.correct('')['text'], '')
        self.assertFalse(self.correct('')['tokens'])

    def test_text_limit_structured_error(self):
        with self.assertRaises(LexicalError) as error:
            self.correct(' '.join(['luz']*257))
        self.assertEqual(error.exception.to_dict()['code'], 'text_too_long')

    def test_dictionary_never_expands_output_vocabulary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'words.dic'
            path.write_text('2\nventilador/AB\nxyzqvv\n', encoding='utf-8')
            resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources', overrides={'wordlist': str(path)})
            vocab = resolver._snapshot.decoder.vocabulary
            self.assertIn('xyzqvv', vocab.dictionary)
            self.assertNotIn('xyzqvv', vocab.terms)
            self.assertNotEqual(resolver.apply('xyzqvv')['text'], 'xyzqvv')

    def test_missing_required_dataset_error_and_disabled_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(LexicalError) as error:
                ContextualResolver(Path(folder)/'missing.json', HERE/'resources', datasets_dir=Path(folder))
            self.assertEqual(error.exception.code, 'missing_resource')
            resolver = ContextualResolver(Path(folder)/'missing.json', HERE/'resources', datasets_dir=Path(folder), overrides={'enabled': False})
            self.assertIn('ventilador', resolver.apply('liga o ventulador')['text'])

    def test_bad_and_missing_ha_inventory_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'ha.json'
            for payload in (None, 'not json', '[]', '{"areas": ["bad"]}'):
                if payload is not None:
                    path.write_text(payload, encoding='utf-8')
                resolver = ContextualResolver(path, HERE/'resources')
                self.assertEqual(resolver.apply('qual a temperatua do quarto')['text'], 'qual a temperatura do quarto')

    def test_configuration_errors_and_functional_unbiased(self):
        for cfg in ({'beam_width': 0}, {'source_weights': {'names': float('nan')}},
                    {'closed_vocabulary': 'false'}, {'score_weights': {'typo': 1}}):
            with self.assertRaises(LexicalError):
                load_config(overrides=cfg)
        vocab = self.resolver._snapshot.decoder.vocabulary
        ev = evidence(vocab, vocab.config, 'de', 'de')
        self.assertEqual(ev['source_prior'], 0)
        self.assertEqual(ev['frequency_prior'], 0)

    def test_ha_weight_normalized_and_similarity_gated(self):
        cfg = load_config()
        generic = Vocabulary(HERE/'datasets', {}, cfg)
        local = Vocabulary(HERE/'datasets', {'areas': [{'name': 'Ventilador'}]}, cfg)
        raw = 'ventulador'
        self.assertGreater(evidence(local, cfg, raw, 'ventilador')['source_prior'], evidence(generic, cfg, raw, 'ventilador')['source_prior'])
        self.assertEqual(evidence(local, cfg, 'zzzzqq', 'ventilador')['source_prior'], 0)
        local.config['source_weights']['home_assistant'] = 100
        self.assertLess(evidence(local, local.config, raw, 'ventilador')['source_prior'], 1)

    def test_global_decode_uses_following_anchor(self):
        cfg = load_config(overrides={'score_weights': {'orthographic': 1, 'phonetic': 0,
                          'edit': 0, 'source': 0, 'frequency': 0, 'bigram': .18,
                          'trigram': 0, 'lookahead': 0, 'semantic': 0}})
        vocab = Vocabulary(HERE/'datasets', {}, cfg)
        vocab.add_phrase('porta automática', 'household')
        vocab.bigrams.clear()
        vocab.trigrams.clear()
        vocab.bigrams['porta', 'automatica'] = 100
        vocab.max_bigram = 100
        vocab.bigram_max_by_left['porta'] = 100
        decoder = Decoder(vocab, CandidateIndex(vocab, cfg), cfg)
        self.assertEqual(decoder.correct('porda')['text'], 'poda')
        result = decoder.correct('porda automática')
        self.assertEqual(result['text'], 'porta automática')
        self.assertLess(result['tokens'][0]['margin'], 0)
        self.assertGreater(result['tokens'][0]['sequence_margin'], 0)

    def test_local_prior_breaks_plausible_tie(self):
        cfg = load_config(overrides={'score_weights': {'bigram': 0, 'trigram': 0, 'lookahead': 0}})
        vocab = Vocabulary(HERE/'datasets', {}, cfg)
        vocab.add_phrase('lavazul', 'household')
        vocab.add_phrase('lavasul', 'household')
        vocab.add_phrase('lavasul', 'home_assistant')
        decoder = Decoder(vocab, CandidateIndex(vocab, cfg), cfg)
        self.assertEqual(decoder.correct('lavaful')['text'], 'lavasul')

    def test_local_unknown_aliases_floors_and_disabled_terms(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')
        resolver.refresh({'floors': [{'name': 'Mezanino', 'aliases': ['Solário']}],
                          'entities': [{'domain': 'light', 'name': 'Zelquar', 'assist_aliases': ['Lumazul']},
                                       {'domain': 'light', 'name': 'Forbiddenqux', 'disabled': True}]})
        vocab = resolver._snapshot.decoder.vocabulary
        for key in ('mezanino', 'solario', 'zelquar', 'lumazul'):
            self.assertIn(key, vocab.terms)
        self.assertNotIn('forbiddenqux', vocab.terms)
        self.assertEqual(resolver.apply('liga Lumazul')['text'], 'liga Lumazul')

    def test_dedup_sources_templates_and_ha(self):
        cfg = load_config()
        once = Vocabulary(HERE/'datasets', {'areas': [{'name': 'Zelquar'}]}, cfg)
        twice = Vocabulary(HERE/'datasets', {'areas': [{'name': 'Zelquar'}, {'name': 'Zelquar'}]}, cfg)
        self.assertEqual(once.terms['zelquar'].frequency, twice.terms['zelquar'].frequency)
        old = once.bigrams.copy()
        self.assertFalse(once.add_phrase('qual a temperatura da {area}', 'commands'))
        self.assertEqual(old, once.bigrams)
        self.assertGreater(once.audit['commands']['duplicate_phrases_skipped'], 1000)

    def test_phonetic_digraphs(self):
        self.assertEqual(phonetic('chave'), phonetic('xave'))
        self.assertNotEqual(phonetic('carro'), phonetic('caso'))
        self.assertEqual(phonetic('cinema'), phonetic('sinema'))

    def test_candidate_query_never_iterates_vocabulary(self):
        index = self.resolver._snapshot.decoder.index
        original = index.vocabulary.terms
        class NoIteration(dict):
            def __iter__(self):
                raise AssertionError('linear vocabulary scan')
            def items(self):
                raise AssertionError('linear vocabulary scan')
        index.vocabulary.terms = NoIteration(original)
        try:
            index.lookup.cache_clear()
            self.assertIn('ventilador', index.lookup('ventulador'))
        finally:
            index.vocabulary.terms = original

    def test_open_policy_preserves_unknown(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources', overrides={'closed_vocabulary': False})
        result = resolver.apply('liga xyzqvv')
        self.assertEqual(result['text'], 'liga xyzqvv')
        self.assertFalse(result['all_tokens_allowed'])

    def test_atomic_refresh_and_failed_build(self):
        resolver = ContextualResolver(HERE/'tests/fixture_inventory.json', HERE/'resources')
        errors = []
        def reader():
            try:
                for _ in range(100):
                    result = resolver.apply('liga o ventulador')
                    self.assertEqual(result['text'], 'liga o ventilador')
                    self.assertTrue(result['all_tokens_allowed'])
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=reader)
        thread.start()
        resolver.refresh({'areas': [{'name': 'Zelquar'}]})
        thread.join()
        self.assertFalse(errors)
        self.assertEqual(resolver.apply('Zelquar')['context_generation'], 2)
        self.assertFalse(resolver.refresh({'areas': [{'name': 'Zelquar'}]}))
        old = resolver._snapshot
        with patch.object(resolver, '_build', side_effect=LexicalError('missing_resource', 'test')):
            with self.assertRaises(LexicalError):
                resolver.refresh({'areas': [{'name': 'Outroqux'}]})
        self.assertIs(resolver._snapshot, old)
        self.assertEqual(resolver.apply('Zelquar')['text'], 'Zelquar')


if __name__ == '__main__':
    unittest.main()

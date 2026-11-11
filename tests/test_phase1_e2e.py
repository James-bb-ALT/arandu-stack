"""Opt-in real Rust HTTP -> simulated authorized HA, no neural weights/downloads."""
import os
import unittest

import test_semantic_confirmation as fixtures
import test_contextual as contextual
from test_contextual import HttpClient
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.intelligence import eligible
from custom_components.local_nlu.contextual_protocol import parse
from custom_components.local_nlu.contextual_catalog import build
from test_semantic_safety import UNSAFE


@unittest.skipUnless(os.environ.get('ARANDU_NLU_ENDPOINT'), 'Opt-in real Rust HTTP endpoint')
class Integrated(contextual.EndToEndTests):
    # Parent uses a local binary; this test accepts an isolated Docker service.
    __unittest_skip__ = not bool(os.environ.get('ARANDU_NLU_ENDPOINT'))

    @classmethod
    def setUpClass(cls):
        cls.endpoint = os.environ['ARANDU_NLU_ENDPOINT']

    @classmethod
    def tearDownClass(cls):
        pass

    async def test_safety_matrix_raw_rust_and_runtime(self):
        hass = fixtures.hass_()
        client = HttpClient(self.endpoint)
        options = {'default_area': 'area_quarto', 'decision_enabled': True, 'llm_enabled': True}
        runtime = ContextualRuntime(hass, client, lambda: options)
        self.addCleanup(runtime.close)
        snapshot = build(hass, hass.auth.user, options)
        await client.async_catalog_v4(snapshot.payload)
        for text in UNSAFE:
            with self.subTest(text=text):
                result = parse(await client.async_interpret_v4({
                    'version': 4, 'text': text, 'generation': snapshot.payload['generation'],
                    'origin_area': 'area_quarto', 'origin_area_source': 'default'}))
                self.assertFalse(result.operations)
                self.assertFalse(eligible(result))
                self.assertTrue(result.reason.startswith('semantic_'))
                await runtime.process(fixtures.input_(text=text, conversation_id=text))
                self.assertEqual(hass.services.calls, [])

    async def test_natural_forms_and_recent_pronoun(self):
        hass = fixtures.hass_()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {'default_area': 'area_sala'})
        self.addCleanup(runtime.close)
        for text in ['Pode acender a luz da sala?', 'Queria a luz da sala acesa',
                     'Deixa a luz da sala ligada', 'Dá pra desligar o ventilador do quarto?',
                     'Coloca a luz em cinquenta por cento', 'Agora diminui ela']:
            conversation = 'continuity' if text in ['Coloca a luz em cinquenta por cento', 'Agora diminui ela'] else text
            result = await runtime.process(fixtures.input_(text=text, conversation_id=conversation))
            self.assertEqual(result.code, 'success', (text, result))
        self.assertEqual(hass.services.calls[-1]['service'], 'turn_on')
        self.assertIn('brightness_pct', hass.services.calls[-1]['data'])

    async def test_explicit_sequence_preserves_order_and_preflights_whole_plan(self):
        hass = fixtures.hass_()
        fixtures.add(hass, 'fixture_tv', 'media_player.fixture_tv', 'TV', {'supported_features': 384})
        hass.services.available.update({('media_player', 'turn_on'), ('media_player', 'turn_off')})
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {'default_area': 'area_sala'})
        self.addCleanup(runtime.close)
        result = await runtime.process(fixtures.input_(text='Liga a TV e depois apaga a luz'))
        self.assertEqual(result.code, 'success', result)
        self.assertEqual([(row['domain'], row['service']) for row in hass.services.calls],
                         [('media_player', 'turn_on'), ('light', 'turn_off')])

    async def test_recent_target_is_not_reused_after_catalog_or_origin_change(self):
        for change in ['catalog', 'origin']:
            with self.subTest(change=change):
                hass = fixtures.hass_()
                fixtures.add(hass, 'satellite', 'assist_satellite.fixture', 'Satélite', {}, area='area_quarto')
                runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
                self.addCleanup(runtime.close)
                user_input = fixtures.input_(text='liga o ventilador', satellite_id='assist_satellite.fixture')
                self.assertEqual((await runtime.process(user_input)).code, 'success')
                if change == 'origin':
                    hass.entity_registry.entities['assist_satellite.fixture'].area_id = 'area_sala'
                else:
                    hass.states._states['fan.ventilador'].attributes['friendly_name'] = 'Novo nome'
                    # This minimal HA fake has no state_changed event bus.
                    runtime.catalog.invalidate()
                before = len(hass.services.calls)
                user_input.text = 'desliga ele'
                result = await runtime.process(user_input)
                self.assertNotEqual(result.code, 'success')
                self.assertEqual(len(hass.services.calls), before)

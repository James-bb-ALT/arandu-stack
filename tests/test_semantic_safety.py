"""User-approved requirements / FIXTURE_TECNICA; never linguistic accuracy gold."""
import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import test_semantic_confirmation as fixtures
from custom_components.local_nlu.semantic_context import hypothesis_allowed
from custom_components.local_nlu.semantic_context import semantic_refusal
from test_contextual import HttpClient
from test_intelligence_services import catalog
from arandu_decision.adapter import Encoder
from arandu_llm.adapter import Llama

UNSAFE = [
    'Não ligue a luz da sala', 'Jamais ligue o ventilador',
    'Nem pense em abrir o portão', 'Não precisa desligar a TV',
    'Desliga tudo, menos a TV', 'Liga a luz e não desliga o ventilador',
    'Se estiver quente, liga o ar', 'Liga a luz, mas não mexe no ventilador',
    'Sem desligar a TV, apaga a luz', 'Liga o ventilador exceto à noite',
    'Liga o ventilador a menos que esteja frio',
    'Liga o ventilador quando eu chegar',
    'Liga o ventilador após eu chegar',
    'Liga o ventilador depois', 'Liga o ventilador às 18 horas',
    'Liga o ventilador daqui 10 minutos', 'Liga o ventilador às 18:00',
]


class Policy(unittest.TestCase):
    def test_every_constraint_refuses_neural_hypotheses(self):
        for text in UNSAFE:
            with self.subTest(text=text):
                self.assertFalse(hypothesis_allowed(text))

    def test_exact_labels_queries_and_calendar_data(self):
        data = catalog()
        data['entities'][0]['name'] = 'Sem Fio'
        for text in ['liga Sem Fio', 'Dá pra ligar Sem Fio?', 'Queria Sem Fio ligada',
                     'qual o estado de Sem Fio', 'quais luzes não estão ligadas',
                     'conte uma piada sem palavrões', 'conte uma história sem violência',
                     'coloca reunião na agenda amanhã às 14 horas']:
            self.assertIsNone(semantic_refusal(text, data), text)
        data['entities'][0]['aliases'] = ['Não', 'Sem', 'Menos']
        self.assertEqual(semantic_refusal('Não liga Sem Fio', data), 'semantic_negation')
        self.assertIsNotNone(semantic_refusal('Desliga tudo menos Sem Fio', data))
        self.assertFalse(hypothesis_allowed('liga ventilador e liga luz', data))
        for text in ['liga ventilador depois abre portão', 'liga ventilador, abre portão',
                     'liga ventilador; fecha persiana', 'liga ventilador após eu chegar']:
            self.assertFalse(hypothesis_allowed(text, data), text)

    def test_distributed_policies_are_identical(self):
        root = Path(__file__).resolve().parents[1]
        original = root/'custom_components/local_nlu'
        copies = [root/f'arandu_stack/rootfs/modules/{module}/rootfs/src/src/{module}'
                  for module in ['arandu_decision', 'arandu_llm']]
        copies.append(root/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/custom_components/local_nlu')
        for distributed in copies:
            for name in ['semantic_context.py', 'semantic_policy.json']:
                self.assertEqual((original/name).read_bytes(), (distributed/name).read_bytes())
        self.assertEqual((original/'semantic_policy.json').read_bytes(),
                         (root/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/addon/engine/data/semantic_policy.json').read_bytes())


class Execution(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Confirmation.asyncSetUp
    infer = fixtures.Confirmation.infer
    say = fixtures.Confirmation.say
    async def test_forged_positive_nlu_plan_cannot_drop_a_constraint(self):
        for text in [*UNSAFE, 'Quais luzes não estão ligadas?', 'conte uma piada sem palavrões',
                     'Não quero o ventilador ligado, conte uma piada',
                     'Não quero ventilador ligado; quero uma piada']:
            with self.subTest(text=text):
                self.runtime.sessions.clear()
                self.client.response = fixtures.plan(fixtures.operation('turn_on', ['reg_fan']))
                self.hass.services.calls.clear()
                await self.say(text)
                self.assertEqual(self.hass.services.calls, [])

    async def test_isolated_models_refuse_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            decision = Encoder(Path(directory))
            llm = Llama(Path(directory), {})
            for text in UNSAFE:
                with self.subTest(text=text):
                    with patch.object(decision, 'load', side_effect=AssertionError('must not load')):
                        result = decision.infer(text, catalog(), 'e5-small', {})
                    self.assertEqual(result['decision_status'], 'rejected')
                    self.assertIsNone(result['proposal'])
                    result = await llm.infer({'text': text}, catalog())
                    self.assertTrue(result['proposal']['requires_clarification'])
                    self.assertEqual(result['load_ms'], 0)

    async def test_speech_constraint_preserves_general_llm_answer(self):
        from test_general_conversation import chat
        from unittest.mock import AsyncMock
        request = AsyncMock(return_value=chat())
        with patch('custom_components.local_nlu.ai_lab.local_request', request):
            result = await self.say('conte uma piada sem palavrões')
        self.assertEqual(result.reason, 'llm_conversation')
        self.assertEqual(self.hass.services.calls, [])

    async def test_legacy_routing_cannot_bypass_safety_with_neural_layers_disabled(self):
        from custom_components.local_nlu.runtime import LocalNluRuntime
        runtime = LocalNluRuntime(self.hass, self.client)
        self.client.response = fixtures.plan(fixtures.operation('turn_on', ['reg_fan']))
        for text in [*UNSAFE, 'Quais luzes não estão ligadas?']:
            with self.subTest(text=text):
                await runtime.async_process(fixtures.input_(text=text))
                self.assertEqual(self.hass.services.calls, [])
        if runtime._contextual:
            runtime._contextual.close()

    async def test_restricted_no_match_never_reaches_models(self):
        for text in UNSAFE:
            with self.subTest(text=text):
                self.runtime.sessions.clear()
                self.requests.clear()
                await self.say(text)
                self.assertEqual(self.requests, [])
                self.assertEqual(self.hass.services.calls, [])


@unittest.skipUnless(os.environ.get('ARANDU_NLU_ENDPOINT'), 'Opt-in real Rust service')
class RealRust(unittest.IsolatedAsyncioTestCase):
    infer = fixtures.Confirmation.infer
    say = fixtures.Confirmation.say

    async def asyncSetUp(self):
        await fixtures.Confirmation.asyncSetUp(self)
        self.client = HttpClient(os.environ['ARANDU_NLU_ENDPOINT'])
        self.runtime.client = self.client

    async def test_real_rust_safety_and_positive_action(self):
        for text in UNSAFE:
            with self.subTest(text=text):
                self.runtime.sessions.clear()
                await self.say(text)
                self.assertEqual(self.hass.services.calls, [])
                self.assertEqual(self.requests, [])
        result = await self.say('Dá pra desligar o ventilador?')
        self.assertEqual(result.code, 'success', result)
        self.assertEqual(self.hass.services.calls[-1]['service'], 'turn_off')


class LegacyExecution(unittest.IsolatedAsyncioTestCase):
    infer = fixtures.Confirmation.infer
    say = fixtures.Confirmation.say
    test_forged_positive_nlu_plan_cannot_drop_a_constraint = Execution.test_forged_positive_nlu_plan_cannot_drop_a_constraint

    async def asyncSetUp(self):
        import importlib.util
        import sys
        await fixtures.Confirmation.asyncSetUp(self)
        name = 'custom_components.local_nlu._phase1_legacy_runtime'
        path = Path(__file__).resolve().parents[1]/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/custom_components/local_nlu/contextual_runtime.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {name: module}):
            spec.loader.exec_module(module)
        self.runtime.close()
        self.runtime = module.ContextualRuntime(self.hass, self.client, lambda: self.options)
        self.addCleanup(self.runtime.close)

"""General chat is speech-only; deterministic Decision remains independently grounded."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_semantic_confirmation as fixtures
from test_intelligence_services import catalog
from arandu_decision.adapter import Encoder
from arandu_llm.adapter import parse_response, prompt_context
from custom_components.local_nlu.semantic_context import clearly_conversational, conversation_allowed
from custom_components.local_nlu.intelligence import accepted_decision


def chat(text='Por que o livro foi ao médico? Porque estava com muitas páginas em branco!'):
    return {'proposal':{'intent':'conversation','target_entities':[],'target_area':None,'confidence':None,
        'requires_clarification':False,'clarification_question':None,'reason':'general_conversation'},
        'response_text':text,'retrieval':{'ranking':[]}}


class Conversation(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.Confirmation.asyncSetUp
    infer=fixtures.Confirmation.infer
    say=fixtures.Confirmation.say

    async def test_general_answer_reaches_assist_without_decision_or_ha_execution(self):
        self.options['decision_enabled']=True
        request=AsyncMock(return_value=chat())
        with patch('custom_components.local_nlu.ai_lab.local_request',request):
            result=await self.say('Conta uma piada')
        self.assertEqual(result.code,'query_success')
        self.assertEqual(result.response_text,chat()['response_text'])
        self.assertEqual(request.await_count,1)
        self.assertEqual(request.call_args.kwargs['payload']['action'],'llm')
        self.assertEqual(self.hass.services.calls,[])

    async def test_domestic_conversation_claim_does_not_replace_a_command(self):
        with patch('custom_components.local_nlu.ai_lab.local_request',AsyncMock(return_value=chat())):
            result=await self.say('liga o ventilador')
        self.assertNotEqual(result.reason,'llm_conversation')
        self.assertEqual(self.hass.services.calls,[])

    async def test_empty_oversized_or_action_carrying_chat_is_rejected(self):
        for response in [chat(''),chat('x'*1001),{**chat(),'proposal':{**chat()['proposal'],'target_entities':['fan.ventilador']}}]:
            with self.subTest(response=response),patch('custom_components.local_nlu.ai_lab.local_request',AsyncMock(return_value=response)):
                result=await self.say('Conta uma piada')
                self.assertNotEqual(result.reason,'llm_conversation')
        self.assertEqual(self.hass.services.calls,[])

    async def test_typo_decision_asks_and_only_yes_executes(self):
        self.options['decision_enabled']=True
        with tempfile.TemporaryDirectory() as folder:
            encoder=Encoder(Path(folder))
            async def infer(session,endpoint,path,*,payload,**kwargs):
                self.requests.append(payload)
                self.assertEqual(payload['action'],'decision')
                return encoder.infer(payload['text'],payload['catalog'],'e5-small',{},payload['context'])
            with patch('custom_components.local_nlu.ai_lab.local_request',infer):
                result=await self.say('acnde o ventilador')
                self.assertEqual(result.code,'confirmation_required')
                self.assertEqual(self.hass.services.calls,[])
                self.assertEqual((await self.say('sim')).code,'success')
                self.assertEqual(len(self.hass.services.calls),1)
                self.assertEqual(len(self.requests),1)


class Contracts(unittest.TestCase):
    def test_chat_schema_and_no_household_context_in_prompt(self):
        for text in ['Conta uma piada','Qual é a capital da França?','Me explique gravidade','Olá','Quem inventou o ventilador?','O que é uma lâmpada?','Como funciona acender uma lâmpada?']:
            with self.subTest(text=text):
                self.assertTrue(clearly_conversational(text,catalog()))
                context,ids=prompt_context({'text':text,'decision':{'ranking':[{'entity_id':'fan.fixture'}]}},catalog())
                self.assertEqual(ids,set());self.assertEqual(context['devices'],[])
        for text in ['liga ventilador','não desliga ventilador','conta uma piada e desliga o ventilador','qual estado do ventilador']:
            self.assertFalse(conversation_allowed(text,catalog()),text)

    def test_real_completion_parser_keeps_chat_separate_from_action_wire(self):
        value={**chat()['proposal'],'response_text':chat()['response_text']}
        output={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
        proposal,reply=parse_response(output,catalog(),set(),256,text='Conta uma piada')
        self.assertEqual(proposal,chat()['proposal']);self.assertEqual(reply,chat()['response_text'])
        self.assertNotIn('response_text',proposal)
        _,reply=parse_response(output,catalog(),set(),256,text='desliga ventilador')
        self.assertIsNone(reply)

    def test_resolved_action_does_not_load_embedding_and_is_revalidated(self):
        with tempfile.TemporaryDirectory() as folder:
            encoder=Encoder(Path(folder));encoder.load=Mock(side_effect=AssertionError('must not load'))
            result=encoder.infer('liga ventilador',catalog(),'e5-small',{})
            self.assertEqual(result['decision_status'],'resolved');self.assertIsNone(result['confidence'])
            self.assertEqual(accepted_decision(result,catalog(),'liga ventilador')['intent'],'turn_on')
            for text in ['desliga ventilador','não liga ventilador','conta uma piada']:
                with self.assertRaises(ValueError):accepted_decision(result,catalog(),text)

    def test_no_unique_device_is_not_resolved_without_embeddings(self):
        value=catalog();value['entities'].append({**value['entities'][0],'entity_id':'fan.other'})
        with tempfile.TemporaryDirectory() as folder:
            encoder=Encoder(Path(folder));encoder.load=Mock(side_effect=RuntimeError('semantic path'))
            with self.assertRaisesRegex(RuntimeError,'semantic path'):encoder.infer('liga ventilador',value,'e5-small',{})

    def test_joint_mean_removes_minimum_plateau_and_queries_are_batched_cached(self):
        calls=[]
        def encode(texts,**kw):calls.append(texts);return texts
        def dot(query,passage):
            if 'Ligar acender' in passage:return .95
            if 'Desligar apagar' in passage:return .9
            if 'Consultar' in passage:return .5
            return .8
        with tempfile.TemporaryDirectory() as folder:
            encoder=Encoder(Path(folder));encoder.load=Mock(return_value='fixture')
            encoder.model=SimpleNamespace(encode=encode)
            with patch.dict(sys.modules,{'numpy':SimpleNamespace(dot=dot)}):
                first=encoder.infer('faz o ventilador funcionar',catalog(),'e5-small',{'min_margin':.001})
                encoder.infer('faz o ventilador funcionar',catalog(),'e5-small',{'min_margin':.001})
            self.assertAlmostEqual(first['raw_score'],.875)
            self.assertAlmostEqual(first['margin'],.025)
            self.assertIsNone(first['confidence'])
            self.assertEqual(first['decision_status'],'confirmation_required')
            self.assertEqual(len(calls),2) # One descriptor batch and one batch of three queries; cached repeat.


class PlainConversation(unittest.TestCase):
    def test_plain_reply_is_bounded_complete_and_never_an_action(self):
        from arandu_llm.adapter import parse_conversation, LlmOutputError
        output={'choices':[{'finish_reason':'stop','message':{'content':'Por que o livro estava triste? Porque tinha muitos problemas!'}}]}
        proposal,text=parse_conversation(output,192)
        self.assertEqual(proposal['target_entities'],[]);self.assertEqual(proposal['intent'],'conversation')
        self.assertIn('Porque',text)
        for finish,content in [('length','texto'),('stop',''),('stop','x'*1001),('stop','texto\x00')]:
            output['choices'][0].update(finish_reason=finish,message={'content':content})
            with self.assertRaises(LlmOutputError):parse_conversation(output,192)

    def test_new_orukeet_default_preserves_explicit_existing_selection(self):
        sys.path.insert(0,str(Path(__file__).parents[1]/'arandu_stack/app'))
        from modules import ModuleManager
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(ModuleManager(root,local=True).config['stt']['stt_model'],'orukeet_int8')
            (root/'modules.json').write_text(json.dumps({'version':2,'modules':{'stt':{'stt_model':'whisper_base'}}}))
            self.assertEqual(ModuleManager(root,local=True).config['stt']['stt_model'],'whisper_base')


class DecisionPreload(unittest.IsolatedAsyncioTestCase):
    async def test_installed_encoder_is_preloaded_and_startup_cost_is_exposed(self):
        from aiohttp.test_utils import TestClient,TestServer
        from arandu_decision.service import create_app
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'e5-small').mkdir()
            (root/'e5-small/arandu-model.json').write_text('{}')
            (root/'e5-small/model.safetensors').touch()
            with patch('arandu_decision.service.Encoder') as factory:
                encoder=factory.return_value;encoder.load_ms=12.5
                client=TestClient(TestServer(create_app(root)))
                await client.start_server()
                try:
                    response=await client.get('/status',headers={'Host':'127.0.0.1:11557','X-Arandu-Lab':'1'})
                    value=await response.json()
                    self.assertEqual(response.status,200)
                    self.assertEqual(value['preload_ms'],12.5)
                    self.assertTrue(value['loaded']);encoder.load.assert_called_once_with('e5-small')
                finally:await client.close()

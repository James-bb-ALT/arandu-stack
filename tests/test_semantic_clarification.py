"""FIXTURE_TECNICA: origin-aware abstention, never guessed actions or areas."""
import copy
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path.append(str(ROOT / 'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp'))
sys.path.append(str(ROOT / 'arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src'))
from test_contextual import Client, hass_, input_, add, plan, operation
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.contextual_catalog import Snapshot
from custom_components.local_nlu.intelligence import fallback, proposal_route
from custom_components.local_nlu.ai_lab_core import digest, validate_proposal
from custom_components.local_nlu.semantic_context import clarification, resolve_device, decomposition
from arandu_llm.adapter import parse_completion, completion_schema
from arandu_llm.contracts import validate_proposal as llm_validate
from arandu_llm.service import retrieve


def catalog():
    return {'areas':[{'area_id':'office','names':['Escritório de Jaime']},{'area_id':'living','names':['Sala']}],
            'entities':[{'entity_id':'fan.'+area,'name':'Ventilador','aliases':[], 'area_id':area,
                         'domain':'fan','actions':['turn_on','turn_off'], 'attributes':{'available':True}}
                        for area in ['office','living']]}


def proposed(area=None, target='fan.office'):
    return {'intent':'turn_on','target_entities':[target],'target_area':area,'confidence':None,
            'requires_clarification':False,'clarification_question':None,'reason':'FIXTURE_TECNICA'}


def output(proposal):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(proposal)}}]}


class ClarificationTests(unittest.TestCase):
    def test_unknown_action_keeps_only_the_origin_device_as_hypothesis(self):
        value=clarification('leva o ventilador',catalog(),{'origin_area':'office'})
        self.assertEqual(value['target_entities'],['fan.office'])
        self.assertIsNone(value['intent'])
        self.assertTrue(value['requires_clarification'])
        self.assertIn('Escritório de Jaime',value['clarification_question'])
        self.assertIn('não entendi o que fazer',value['clarification_question'])
        self.assertEqual(validate_proposal(value,catalog()),llm_validate(value,catalog()))

    def test_explicit_area_overrides_satellite_and_unknown_area_does_not(self):
        self.assertEqual(resolve_device('leva o ventilador da sala',catalog(),{'origin_area':'office'})['entity_id'],'fan.living')
        self.assertIsNone(resolve_device('leva o ventilador na garagem',catalog(),{'origin_area':'office'}))

    def test_ambiguous_unavailable_or_unrecognized_device_is_not_invented(self):
        self.assertEqual(clarification('leva o ventilador',catalog())['target_entities'],[])
        self.assertIn('Não entendi',clarification('FIXTURE_UNRECOGNIZED',catalog())['clarification_question'])
        value=catalog()
        value['entities'][0]['attributes']['available']=False
        self.assertEqual(clarification('leva o ventilador',value,{'origin_area':'office'})['target_entities'],[])

    def test_model_area_name_causes_abstention_instead_of_execution_or_http_error(self):
        result=parse_completion(output(proposed('Escritório de Jaime')),catalog(),{'fan.office','fan.living'},192,
                                text='leva o ventilador',context={'origin_area':'office'})
        self.assertTrue(result['requires_clarification'])
        self.assertIsNone(result['intent'])
        self.assertEqual(result['target_area'],'office')
        self.assertIn('não entendi o que fazer',result['clarification_question'])

    def test_uncertain_action_can_be_proposed_but_requires_confirmation(self):
        result=parse_completion(output(proposed()),catalog(),{'fan.office','fan.living'},192,
                                text='leva o ventilador',context={'origin_area':'office'})
        self.assertEqual(result['intent'],'turn_on')
        self.assertEqual(proposal_route('leva o ventilador',result,catalog(),{'origin_area':'office'}),'confirmation_required')

    def test_generic_action_proximity_keeps_original_fragment_and_three_slots(self):
        for fragment in ['acnde','deslga','lga','leva']:
            analysis=decomposition(fragment+' o ventilador',catalog(),{'origin_area':'office'})
            self.assertEqual(analysis['action']['text'],fragment)
            self.assertEqual(analysis['device']['entity_id'],'fan.office')
            self.assertEqual(analysis['area'],{'area_id':'office','source':'origin'})
            self.assertGreater(analysis['action']['ranking'][0]['lexical_similarity'],0)
            self.assertIsNone(analysis['action']['exact'])

    def test_known_action_without_area_uses_origin_and_rejects_other_device(self):
        result=parse_completion(output(proposed()),catalog(),{'fan.office','fan.living'},192,
                                text='liga o ventilador',context={'origin_area':'office'})
        self.assertFalse(result['requires_clarification'])
        result=parse_completion(output(proposed(target='fan.living')),catalog(),{'fan.office','fan.living'},192,
                                text='liga o ventilador',context={'origin_area':'office'})
        self.assertTrue(result['requires_clarification'])

    def test_generation_schema_and_retrieval_bind_real_catalog_ids(self):
        schema=completion_schema(catalog(),{'fan.office'})
        self.assertEqual(schema['properties']['target_area']['enum'],[None,'living','office'])
        self.assertEqual(schema['properties']['target_entities']['items']['enum'],['fan.office'])
        ranking=retrieve('leva o ventilador',catalog(),{'origin_area':'office'})['ranking']
        self.assertEqual(ranking[0]['entity_id'],'fan.office')

    def test_shared_context_module_is_identical_in_both_distributions(self):
        self.assertEqual((ROOT/'custom_components/local_nlu/semantic_context.py').read_bytes(),
                         (ROOT/'arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src/arandu_llm/semantic_context.py').read_bytes())
        self.assertEqual((ROOT/'custom_components/local_nlu/semantic_context.py').read_bytes(),
                         (ROOT/'arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/semantic_context.py').read_bytes())


class RuntimeClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lab_rejected_guess_becomes_a_question_without_execution(self):
        from custom_components.local_nlu.ai_lab import Lab
        hass=hass_()
        hass.auth.user.id='user'
        entry=SimpleNamespace(entry_id='fixture',options={'default_area':'area_quarto'},
                              runtime_data=SimpleNamespace(_client=Client({'version':4,'status':'no_match'})))
        hass.config_entries=SimpleNamespace(async_entries=lambda _: [entry])
        raw={'proposal':proposed(target='fan.ventilador'), 'retrieval':{'ranking':[{'entity_id':'fan.ventilador','intent':'turn_on'}]}}
        core=SimpleNamespace(Context=lambda user_id:SimpleNamespace(user_id=user_id))
        with patch.dict(sys.modules,{'homeassistant.core':core}), patch('custom_components.local_nlu.ai_lab.local_request',AsyncMock(return_value=raw)) as request:
            result=await Lab(hass)._handle(hass.auth.user,{'command':'run','mode':'llm','text':'leva o ventilador'},None,'http://127.0.0.1:8770')
        self.assertEqual(request.call_args.kwargs['payload']['context']['origin_area'],'area_quarto')
        self.assertIn('É isso?',result['clarification_question'])
        self.assertTrue(result['operations_validated'])
        self.assertTrue(result['confirmation_required'])
        self.assertNotIn('execution_token',result)
        self.assertEqual(hass.services.calls,[])

    async def test_unknown_action_never_executes_in_any_toggle_combination(self):
        cat=catalog()
        snapshot=Snapshot(cat,{r['entity_id']:r for r in cat['entities']})
        async def infer(body):
            p=proposed()
            if body['action']=='decision':
                p['confidence']=.9
                return {'decision_status':'accepted','confidence':.9,'margin':.2,'catalog_fingerprint':digest(cat),
                        'proposal':p,'ranking':[{'entity_id':'fan.office','intent':'turn_on','raw_score':.8}]}
            return {'proposal':p,'retrieval':{'ranking':[{'entity_id':'fan.office','intent':'turn_on'}]}}
        for decision,llm in [(False,False),(True,False),(False,True),(True,True)]:
            operations,trace=await fallback('leva o ventilador',snapshot,{'decision_enabled':decision,'llm_enabled':llm},infer,{'origin_area':'office'})
            if decision or llm:
                self.assertEqual(len(operations),1)
                self.assertIn('confirmation_required',[stage['status'] for stage in trace.values()])
            else:
                self.assertEqual(operations,())

    async def test_nlu_final_layer_names_satellite_device_and_asks_for_action(self):
        hass=hass_()
        add(hass,'satellite','assist_satellite.fixture','Satélite',{},area='area_quarto')
        runtime=ContextualRuntime(hass,Client({'version':4,'status':'no_match'}))
        try:
            result=await runtime.process(input_(text='leva o ventilador',satellite_id='assist_satellite.fixture'))
            self.assertIn('não entendi o que fazer',result.response_text)
            self.assertRegex(result.response_text,'Quarto|Dormitório')
            self.assertEqual(result.code,'missing_slot')
            self.assertTrue(result.continue_conversation)
            self.assertEqual(hass.services.calls,[])
            runtime.client.response=plan(operation(targets=['reg_fan']))
            result=await runtime.process(input_(text='liga',satellite_id='assist_satellite.fixture'))
            self.assertEqual(runtime.client.requests[-1]['last_targets'],['reg_fan'])
            self.assertEqual(result.code,'success')
            self.assertEqual(len(hass.services.calls),1)
        finally:
            runtime.close()

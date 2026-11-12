"""Strict additive v4 fields cannot turn read constraints into effects."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import plan,operation
from custom_components.local_nlu.contextual_protocol import parse,validate_parameters,legacy_neural_catalog
from custom_components.local_nlu.protocol import ProtocolError


class Protocol(unittest.TestCase):
    def test_new_operations_reject_unbound_or_extra_parameters(self):
        for action,params in [('automation_trigger',{'value':True}),('todo_remove',{}),('todo_update',{'old_item':'leite'}),
            ('todo_remove_completed',{'value':'all'}),('contextual_undo',{'value':42}),
            ('history_query',{'metric':'energy','aggregate':'sum'}),('turn_on',{'period':'yesterday'}),
            ('turn_off',{'old_item':'TV'})]:
            with self.subTest(action=action),self.assertRaises(ProtocolError):parse(plan(operation(action,params=params)))

    def test_new_read_fields_are_bounded_and_closed(self):
        for params in [{'limit':0},{'limit':9},{'limit':True},{'period':[]},{'order':'random'},{'metric':[]},{'aggregate':{}},{'new_item':'a'*257},{'service':'shell'}]:
            with self.subTest(params=params),self.assertRaises(ProtocolError):validate_parameters(params)

    def test_neural_view_removes_new_effects_without_mutating_authority(self):
        catalog={'version':4,'generation':'a'*64,'entities':[{'registry_id':'a','actions':['query','automation_trigger','todo_remove','history_query']}],'areas':[],'groups':[]}
        view=legacy_neural_catalog(catalog)
        self.assertEqual(view['entities'][0]['actions'],['query'])
        self.assertNotEqual(view['generation'],catalog['generation'])
        self.assertEqual(catalog['entities'][0]['actions'],['query','automation_trigger','todo_remove','history_query'])

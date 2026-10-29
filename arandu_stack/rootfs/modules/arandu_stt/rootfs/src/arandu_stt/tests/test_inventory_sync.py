import sys
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from inventory_sync import build_inventory


class TestInventorySync(unittest.TestCase):
    def test_build_inventory(self):
        areas=[{"area_id":"sala","name":"Sala","aliases":[]}]
        devices=[{"id":"d1","name":"TV","name_by_user":"TV da sala","area_id":"sala","manufacturer":"Samsung","model":"TV"}]
        entities=[{"entity_id":"media_player.tv_sala","name":"TV da sala","original_name":"TV","aliases":[],"area_id":None,"device_id":"d1","platform":"samsungtv","options":{},"disabled_by":None,"hidden_by":None,"entity_category":None}]
        states=[{"entity_id":"media_player.tv_sala","attributes":{"friendly_name":"TV da sala"}}]
        exposed={"media_player.tv_sala":{"conversation":True}}
        inv=build_inventory(areas,devices,entities,states,exposed)
        self.assertEqual(inv['_meta']['total_entidades'],1)
        self.assertEqual(inv['entities'][0]['area'],'Sala')
        self.assertTrue(inv['entities'][0]['exposed_to_assist'])

if __name__ == '__main__': unittest.main()

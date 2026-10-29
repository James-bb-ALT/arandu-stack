"""Manual contract checks; no downloads or inference."""
import unittest
from arandu_llm.service import validate_catalog, retrieve
from arandu_llm.contracts import decision_policy

def catalog():
    return {"areas": [{"area_id": "sala", "names": ["sala"]}], "entities": [
        {"entity_id": f"light.fixture_{i}", "name": "luz especial" if i == 99 else f"fixture {i}",
         "domain": "light", "aliases": [], "area_id": "sala", "actions": ["turn_on", "turn_off"],
         "attributes": {"available": True}} for i in range(100)]}

class Contracts(unittest.TestCase):
    def test_complete_catalog_is_preserved(self):
        value = catalog()
        self.assertIs(validate_catalog(value), value)
        self.assertEqual(len(value["entities"]), 100)

    def test_duplicate_entity_rejected(self):
        value = catalog()
        value["entities"].append(value["entities"][0])
        with self.assertRaises(ValueError):
            validate_catalog(value)

    def test_overflow_rejected_without_truncation(self):
        value = catalog()
        value["entities"] *= 83
        with self.assertRaises(ValueError):
            validate_catalog(value)
        self.assertEqual(len(value["entities"]), 8300)

    def test_retrieval_visits_last_entity(self):
        output = retrieve("acender luz especial", catalog())
        self.assertEqual(output["eligible_pairs"], 200)
        self.assertEqual(output["ranking"][0]["entity_id"], "light.fixture_99")

    def test_similarity_does_not_become_confidence(self):
        row = {"entity_id": "light.fixture_99", "intent": "turn_on", "raw_score": .99}
        result = decision_policy([row], "fixture", catalog(), {})
        self.assertIsNone(result["confidence"])
        self.assertIsNone(result["proposal"])

if __name__ == "__main__":
    unittest.main()

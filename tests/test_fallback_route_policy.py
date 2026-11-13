"""Parametrized unit tests for pure fallback_policy routing and consistency."""
import unittest
from types import SimpleNamespace

from custom_components.local_nlu.fallback_policy import (
    RouteCategory,
    classify_fallback,
    route_diagnostic,
)
from custom_components.local_nlu.intelligence import eligible, eligible_summary


class TestFallbackRoutePolicy(unittest.TestCase):
    def test_parametrized_routing_table(self):
        cases = [
            # (kwargs, expected_category, expected_decision, expected_llm)
            (
                dict(nlu_status="plan", reason=None, command={"action": "turn_on"}, candidates=(), options=(), pending=False),
                RouteCategory.NLU_RESOLVED,
                False,
                False,
            ),
            (
                dict(nlu_status="clarification", reason="target_not_found", command={"action": "turn_off", "area": "sala"}, candidates=(), options=(), pending=False),
                RouteCategory.RECOVERABLE,
                True,
                True,
            ),
            (
                dict(nlu_status="clarification", reason="target_not_found", command={"action": "turn_off"}, candidates=("light.a", "light.b"), options=(), pending=False),
                RouteCategory.RECOVERABLE,
                True,
                True,
            ),
            (
                dict(nlu_status="clarification", reason="no_compatible_capability", command={"action": "light.query", "domain": "light"}, candidates=(), options=(), pending=False),
                RouteCategory.RECOVERABLE,
                True,
                True,
            ),
            (
                dict(nlu_status="clarification", reason="no_compatible_capability", command=None, candidates=(), options=(), pending=False),
                RouteCategory.UNSUPPORTED,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason="permission_denied", command={"action": "turn_off"}, candidates=(), options=(), pending=False),
                RouteCategory.SAFETY_BLOCK,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason="stale", command=None, candidates=(), options=(), pending=False),
                RouteCategory.SAFETY_BLOCK,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason="semantic_negation", command=None, candidates=(), options=(), pending=False),
                RouteCategory.SAFETY_BLOCK,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason="no_match", command=None, candidates=(), options=(), pending=False, safety_flags=("prompt_injection",)),
                RouteCategory.SAFETY_BLOCK,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason="no_match", command=None, candidates=(), options=(), pending=False, user_scope_known=False),
                RouteCategory.SAFETY_BLOCK,
                False,
                False,
            ),
            (
                dict(nlu_status="clarification", reason="missing_origin_area", command={"action": "turn_on"}, candidates=(), options=(), pending=False),
                RouteCategory.CLARIFY,
                True,
                True,
            ),
            (
                dict(nlu_status="clarification", reason="missing_origin_area", command={"action": "query", "scope": "global"}, candidates=(), options=(), pending=False),
                RouteCategory.RECOVERABLE,
                True,
                True,
            ),
            (
                dict(nlu_status="no_match", reason="no_match", command=None, candidates=(), options=(), pending=True),
                RouteCategory.PENDING_DIALOGUE,
                False,
                False,
            ),
            (
                dict(nlu_status="no_match", reason=None, command=None, candidates=(), options=(), pending=False),
                RouteCategory.RECOVERABLE,
                True,
                True,
            ),
        ]
        for kwargs, exp_cat, exp_dec, exp_llm in cases:
            with self.subTest(kwargs=kwargs):
                route = classify_fallback(**kwargs)
                self.assertEqual(route.category, exp_cat)
                self.assertEqual(route.decision_allowed, exp_dec)
                self.assertEqual(route.llm_allowed, exp_llm)

    def test_diagnostic_omits_transcripts_and_preserves_slots(self):
        route = classify_fallback(
            nlu_status="clarification",
            reason="target_not_found",
            command={"action": "turn_off", "area": "sala", "secret_transcript": "desliga tudo"},
            candidates=(),
            options=(),
            pending=False,
        )
        diag = route_diagnostic(route, decision_called=True, llm_called=False)
        self.assertEqual(diag["route"], "RECOVERABLE")
        self.assertEqual(diag["preserved_slots"], {"action": "turn_off", "area": "sala"})
        self.assertNotIn("secret_transcript", str(diag))
        self.assertTrue(diag["decision_called"])
        self.assertFalse(diag["llm_called"])

    def test_eligible_and_eligible_summary_consistency(self):
        outcome = SimpleNamespace(
            status="clarification",
            reason="no_compatible_capability",
            command={"action": "light.query", "domain": "light"},
            candidates=(),
            options=(),
        )
        self.assertTrue(eligible(outcome, pending=False))
        self.assertFalse(eligible(outcome, pending=True))
        self.assertTrue(
            eligible_summary(
                {"status": "clarification", "reason": "no_compatible_capability", "action": "light.query", "domain": "light"}
            )
        )
        self.assertFalse(
            eligible_summary(
                {"status": "no_match", "reason": "permission_denied", "action": "turn_off"}
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'arandu_stack' / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_dockerfile_copies_model_registry_and_ai_lab_lists_all_llms():
    dockerfile = (ROOT / 'arandu_stack' / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY model_registry /opt/model_registry' in dockerfile
    assert '/opt/model_registry/models.json' in dockerfile

    ai_lab_py = (ROOT / 'custom_components' / 'local_nlu' / 'ai_lab.py').read_text(encoding='utf-8')
    ai_lab_js = (ROOT / 'custom_components' / 'local_nlu' / 'ai_lab_panel.js').read_text(encoding='utf-8')
    assert 'this.last?.proposal?.reason' not in ai_lab_js
    for mid in ('tucano', 'qwen3', 'lfm2.5-1.2b', 'lfm3-1.7b', 'qwen3-1.7b'):
        assert mid in ai_lab_py
        assert mid in ai_lab_js


def test_api_llm_models_returns_200_even_when_llm_service_stopped(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('arandu_app_server_fixture', APP_DIR / 'server.py')
    app_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_server)

    async def run():
        app = app_server.create_app(tmp_path, local=True)
        async with TestClient(TestServer(app)) as client:
            headers = {'Host': '127.0.0.1:8770', 'X-Arandu-Lab': '1'}
            resp = await client.get('/api/llm/models', headers=headers)
            assert resp.status == 200
            data = await resp.json()
            ids = {m['id']: m for m in data['models']}
            for mid in ('tucano', 'qwen3', 'lfm2.5-1.2b', 'lfm3-1.7b', 'qwen3-1.7b'):
                assert mid in ids
                assert ids[mid]['downloadable'] is True
            for unpinned in ('qwen3.5-2b', 'qwen3-4b-instruct-2507'):
                assert unpinned in ids
                assert ids[unpinned]['downloadable'] is False
            tts_resp = await client.get('/api/tts/models', headers=headers)
            assert tts_resp.status == 200

    asyncio.run(run())


def test_conversation_render_silent_on_simple_actions_speaks_on_complex_and_llm():
    from pathlib import Path
    import types

    # Stub minimal homeassistant modules for importing conversation.py
    ha = types.ModuleType('homeassistant')
    ha_comp = types.ModuleType('homeassistant.components')
    ha_conv = types.ModuleType('homeassistant.components.conversation')
    ha_conv.ConversationEntity = object
    ha_conv.ConversationInput = object
    ha_conv.ConversationResult = object
    ha_conv.TraceEvent = object
    ha_conv.async_set_agent = lambda *a, **k: None
    ha_core = types.ModuleType('homeassistant.core')
    ha_core.HomeAssistant = object
    ha_helpers = types.ModuleType('homeassistant.helpers')
    ha_intent = types.ModuleType('homeassistant.helpers.intent')
    ha_intent.IntentResponseErrorCode = types.SimpleNamespace(UNKNOWN='unknown', NO_VALID_TARGETS='no_valid_targets')
    ha_intent.IntentResponseType = types.SimpleNamespace(QUERY_ANSWER='query_answer')
    ha_ce = types.ModuleType('homeassistant.config_entries')
    ha_ce.ConfigEntry = object
    for name, mod in (
        ('homeassistant', ha), ('homeassistant.components', ha_comp),
        ('homeassistant.components.conversation', ha_conv), ('homeassistant.core', ha_core),
        ('homeassistant.helpers', ha_helpers), ('homeassistant.helpers.intent', ha_intent),
        ('homeassistant.config_entries', ha_ce),
    ):
        sys.modules.setdefault(name, mod)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from custom_components.local_nlu.conversation import _render
    from custom_components.local_nlu.runtime import RuntimeResult

    # Simple action: silent (only HA Assist done chime)
    assert _render(RuntimeResult(code='success', operation_count=1, response_text='')) == ('', None, False)
    assert _render(RuntimeResult(code='success', operation_count=2, response_text=None)) == ('', None, False)
    # Complex confirmation / alarm / reminder: speaks
    assert _render(RuntimeResult(code='success', operation_count=1, response_text='Alarme para 7h criado.')) == ('Alarme para 7h criado.', None, False)
    assert _render(RuntimeResult(code='confirmation_required', response_text='Confirma ligar a trava?')) == ('Confirma ligar a trava?', None, False)
    # Clarification / continue conversation: speaks
    assert _render(RuntimeResult(code='missing_slot', response_text='Qual luz você quer ligar?')) == ('Qual luz você quer ligar?', None, False)
    # LLM conversation: speaks
    assert _render(RuntimeResult(code='query_success', response_text='Tudo bem por aqui!')) == ('Tudo bem por aqui!', None, True)


def test_ambiguous_area_from_other_room_clarifies_not_safety_refusal():
    import asyncio
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator

    orch = ProgressiveOrchestrator()
    snapshot = {'generation': 'g1', 'areas': [], 'entities': []}

    async def _interpret_v5(_req):
        return {
            'request_kind': 'needs_repair',
            'safety_flags': [],
            'origin': {'area_id': 'sala'},
            'v4_outcome': {'status': 'clarification', 'reason': 'ambiguous_area', 'operations': []},
            'clauses': [
                {
                    'clause_id': 0,
                    'original_text': 'desliga a luz do escritorio',
                    'slots': {
                        'action': {'status': 'resolved', 'value': 'turn_off'},
                        'area': {'status': 'conflicting', 'candidates': ['escritorio_de_jaime', 'escritorio_ana']},
                    },
                    'unresolved': ['area', 'target'],
                    'allowed_actions': ['turn_off'],
                    'constraints': {'negated': False, 'conditional': False, 'quoted_content': False},
                }
            ],
        }

    async def _decision_denied(_payload):
        return {'route': 'denied', 'status': 'denied', 'reason': 'safety_or_constraint_locked'}

    async def _review_v5(_payload):
        return {'accepted': False}

    res = asyncio.run(
        orch.resolve(
            text='desliga a luz do escritorio',
            snapshot_payload=snapshot,
            snapshot_by_id={},
            origin_area='sala',
            origin_source='satellite',
            origin_context_id=None,
            user_id='u1',
            interpret_v5=_interpret_v5,
            decision_partial=_decision_denied,
            review_v5=_review_v5,
        )
    )
    assert res.outcome_kind == 'clarification'
    assert res.reason == 'ambiguous_area'
    assert 'Em qual cômodo?' in (res.speech or '')

    async def _decision_clarify(_payload):
        return {'route': 'possible_action', 'status': 'needs_clarification', 'reason': 'ambiguous_area'}

    async def _llm_refusal(_partial, _dec, _graph):
        return {'mode': 'refusal', 'reason': 'ambiguous'}

    res2 = asyncio.run(
        orch.resolve(
            text='desliga a luz do escritorio',
            snapshot_payload=snapshot,
            snapshot_by_id={},
            origin_area='sala',
            origin_source='satellite',
            origin_context_id=None,
            user_id='u1',
            interpret_v5=_interpret_v5,
            decision_partial=_decision_clarify,
            llm_handoff=_llm_refusal,
            review_v5=_review_v5,
            llm_enabled=True,
        )
    )
    assert res2.outcome_kind == 'clarification'
    assert res2.reason == 'ambiguous_area'
    assert 'Em qual cômodo?' in (res2.speech or '')


def test_partial_reasoner_resolves_ambiguous_area_from_origin_area():
    dec_src = str(ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_decision' / 'rootfs' / 'src' / 'src')
    if dec_src not in sys.path:
        sys.path.insert(0, dec_src)
    from arandu_decision.partial_reasoner import reason_partial

    base_partial = {
        'request_kind': 'needs_repair',
        'safety_flags': [],
        'v4_outcome': {'status': 'clarification', 'reason': 'ambiguous_area'},
        'clauses': [
            {
                'clause_id': 0,
                'original_text': 'desliga a luz do escritorio',
                'slots': {
                    'action': {'status': 'resolved', 'value': 'turn_off'},
                    'area': {'status': 'conflicting', 'candidates': ['escritorio_de_jaime', 'escritorio_ana']},
                    'target': {'status': 'unknown', 'candidates': []},
                },
                'unresolved': ['area', 'target'],
                'allowed_actions': ['turn_off'],
                'constraints': {'negated': False, 'conditional': False, 'quoted_content': False},
            }
        ],
    }

    # Same room satellite breaks ambiguity
    res_same = reason_partial({**base_partial, 'origin': {'area_id': 'escritorio_de_jaime'}})
    assert res_same['status'] == 'resolved_candidate'
    assert any(r['slot'] == 'area' and r['value'] == 'escritorio_de_jaime' for r in res_same['repairs'])

    # Different room satellite asks for clarification instead of denying
    res_other = reason_partial({**base_partial, 'origin': {'area_id': 'sala'}})
    assert res_other['status'] == 'needs_clarification'
    assert res_other['reason'] == 'ambiguous_area'

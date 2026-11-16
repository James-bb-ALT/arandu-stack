"""Comprehensive regression & E2E verification suite covering T01-T40 from the Contextual NLU/Decision/Dialogue Audit."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MLP_DIR = ROOT / "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp"
if str(MLP_DIR) not in sys.path:
    sys.path.insert(0, str(MLP_DIR))
DECISION_SRC = ROOT / "arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src"
if str(DECISION_SRC) not in sys.path:
    sys.path.insert(0, str(DECISION_SRC))

from arandu_decision.partial_reasoner import reason_partial
from custom_components.local_nlu.contextual_errors import MESSAGES, render_contextual_message
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.dialogue_coordinator import PendingDialogue, PendingRequest, PendingRequestStore
from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator
from custom_components.local_nlu.semantic_context import semantic_refusal
from test_catalog_runtime import _State, _User, _entry, _hass


def _find_rust_binary() -> Path | None:
    env_bin = os.environ.get("ARANDU_NLU_BINARY")
    if env_bin and Path(env_bin).is_file():
        return Path(env_bin)
    for rel in (
        "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/target/debug/local-nlu.exe",
        "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/target/debug/local-nlu",
        "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/target/release/local-nlu.exe",
        "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/target/release/local-nlu",
    ):
        p = ROOT / rel
        if p.is_file():
            return p
    return None


class RustHttpClient:
    """HTTP bridge calling the real compiled Rust engine (`/v4/*` and `/v5/*`)."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def async_phase5_supported(self) -> bool:
        return True

    async def async_catalog_v4(self, payload: dict) -> dict:
        return self._post("/v4/catalog", payload)

    async def async_interpret_v4(self, payload: dict) -> dict:
        return self._post("/v4/interpret", payload)

    async def async_interpret_v5(self, payload: dict) -> dict:
        return self._post("/v5/interpret", payload)

    async def async_review_v5(self, payload: dict) -> dict:
        return self._post("/v5/review", payload)


def _add_entity(
    hass,
    registry_id: str,
    entity_id: str,
    name: str,
    attributes: dict | None = None,
    *,
    state: str = "off",
    area: str = "area_quarto",
    aliases: list[str] | None = None,
    exposed: bool = True,
    device_id: str | None = None,
):
    entry = _entry(registry_id, entity_id, area, name)
    entry.aliases = set(aliases or [])
    if device_id is not None:
        entry.device_id = device_id
    attrs = dict(attributes or {})
    domain = entity_id.split(".", 1)[0]
    if "supported_features" not in attrs:
        if domain in ("climate", "media_player"):
            attrs["supported_features"] = 128 | 256
        elif domain == "fan":
            attrs["supported_features"] = 32 | 16
    hass.entity_registry.entities[entity_id] = entry
    hass.states._states[entity_id] = _State(state, {"friendly_name": name, **attrs})
    if exposed:
        hass.exposed.add(entity_id)
    else:
        hass.exposed.discard(entity_id)
    return entry


def _build_house_hass(*, respeaker: bool = True):
    hass = _hass(_User(set(), admin=True))
    # Ensure rooms: quarto, sala, cozinha, banheiro, varanda exist cleanly
    for aid, nm in (
        ("area_quarto", "Quarto"),
        ("area_sala", "Sala"),
        ("area_cozinha", "Cozinha"),
        ("area_banheiro", "Banheiro"),
        ("area_varanda", "Varanda"),
    ):
        hass.area_registry.areas[aid] = SimpleNamespace(name=nm, aliases=set(), floor_id=None)

    hass.entity_registry.entities.clear()
    hass.states._states.clear()
    hass.exposed.clear()

    # Devices including a ReSpeaker satellite in quarto and a second satellite in sala
    devices = {
        "dev_respeaker_quarto": SimpleNamespace(
            area_id="area_quarto",
            model="ReSpeaker Lite Voice Satellite",
            model_id="respeaker",
            name="Arandu",
            name_by_user="Arandu Quarto",
            configuration_url="http://respeaker-jaime.local/",
        ),
        "dev_sat_sala": SimpleNamespace(
            area_id="area_sala",
            model="Assist Satellite",
            model_id="esp_sat",
            name="Satélite Sala",
            name_by_user=None,
            configuration_url=None,
        ),
    }
    hass.device_registry.async_get = lambda did: devices.get(did)
    sat_device_id = "dev_respeaker_quarto" if respeaker else None
    sat_q = _add_entity(hass, "sat_quarto", "assist_satellite.quarto", "Satélite Quarto", {}, area="area_quarto")
    if sat_device_id:
        sat_q.device_id = sat_device_id
    sat_s = _add_entity(hass, "sat_sala", "assist_satellite.sala", "Satélite Sala", {}, area="area_sala")
    sat_s.device_id = "dev_sat_sala"

    hass.services.available.update({
        ("light", "turn_on"), ("light", "turn_off"),
        ("climate", "turn_on"), ("climate", "turn_off"), ("climate", "set_temperature"),
        ("fan", "turn_on"), ("fan", "turn_off"),
        ("media_player", "turn_on"), ("media_player", "turn_off"),
        ("switch", "turn_on"), ("switch", "turn_off"),
    })
    return hass


def _input(
    text: str,
    *,
    conversation_id: str | None = "conv-1",
    user_id: str = "user",
    satellite_id: str | None = "assist_satellite.quarto",
    device_id: str | None = None,
):
    return SimpleNamespace(
        text=text,
        context=SimpleNamespace(user_id=user_id),
        conversation_id=conversation_id,
        device_id=device_id,
        satellite_id=satellite_id,
        language="pt-BR",
    )


@pytest.fixture(scope="module")
def rust_server():
    binary = _find_rust_binary()
    if binary is None:
        pytest.skip("Rust local-nlu binary not built")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [str(binary), "serve", "--listen", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ready = False
        for _ in range(150):
            try:
                with urllib.request.urlopen(endpoint + "/health", timeout=0.15):
                    ready = True
                    break
            except OSError:
                time.sleep(0.02)
        if not ready:
            pytest.fail("Rust HTTP server failed to start")
        yield endpoint
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class TestContextualAuditT01ToT40:
    """Direct automated verification of Scenarios T01-T40 from the audit matrix."""

    def test_t01_t02_t03_t04_t05_t06_lighting_scope_and_exposure(self, rust_server):
        async def run():
            client = RustHttpClient(rust_server)

            # T01: desliga luz, 1 light in quarto (+ literal "Luz" in cozinha must NOT shadow)
            hass = _build_house_hass()
            _add_entity(hass, "l_q1", "light.quarto_1", "Abajur", area="area_quarto")
            _add_entity(hass, "l_coz", "light.cozinha", "Luz", area="area_cozinha")
            rt = ContextualRuntime(hass, client)
            res_t01 = await rt.process(_input("desliga luz"))
            assert res_t01.code == "success", res_t01
            assert {c["target"]["entity_id"][0] for c in hass.services.calls} == {"light.quarto_1"}
            rt.close()

            # T02: desliga a luz, 3 lights in quarto -> bulk local category without cozinha
            hass = _build_house_hass()
            _add_entity(hass, "l_q1", "light.quarto_1", "Teto", area="area_quarto")
            _add_entity(hass, "l_q2", "light.quarto_2", "Arandela", area="area_quarto")
            _add_entity(hass, "l_q3", "light.quarto_3", "Abajur", area="area_quarto")
            _add_entity(hass, "l_coz", "light.cozinha", "Luz", area="area_cozinha")
            rt = ContextualRuntime(hass, client)
            res_t02 = await rt.process(_input("desliga a luz"))
            assert res_t02.code == "success", res_t02
            assert {c["target"]["entity_id"][0] for c in hass.services.calls} == {
                "light.quarto_1", "light.quarto_2", "light.quarto_3",
            }
            rt.close()

            # T03: apaga as lampadas in quarto (same category policy) + room without lights fails safely
            hass = _build_house_hass()
            _add_entity(hass, "l_q1", "light.quarto_1", "Luminária", area="area_quarto")
            _add_entity(hass, "l_coz", "light.cozinha", "Luz", area="area_cozinha")
            rt = ContextualRuntime(hass, client)
            res_t03 = await rt.process(_input("apaga as lâmpadas"))
            assert res_t03.code == "success", res_t03
            assert {c["target"]["entity_id"][0] for c in hass.services.calls} == {"light.quarto_1"}

            # T03b: room with 0 lights (banheiro) must NOT touch cozinha's literal "Luz"
            sat_b = _add_entity(hass, "sat_banheiro", "assist_satellite.banheiro", "Sat Banheiro", {}, area="area_banheiro")
            rt.catalog.invalidate()
            res_zero = await rt.process(_input("desliga luz", satellite_id=sat_b.entity_id, conversation_id="z"))
            assert res_zero.code == "unavailable", res_zero
            assert res_zero.reason == "no_compatible_capability"
            assert "iluminação compatível" in (res_zero.response_text or "")
            assert res_zero.continue_conversation is False  # T35: terminal failure closes followup
            rt.close()

            # T04 (table): desliga a luz da mesa -> only nominal target "Luz da Mesa"
            hass = _build_house_hass()
            _add_entity(hass, "l_q1", "light.quarto_1", "Luz do Teto", area="area_quarto")
            _add_entity(hass, "l_q2", "light.quarto_mesa", "Luz da Mesa", area="area_quarto")
            rt = ContextualRuntime(hass, client)
            res_t04_table = await rt.process(_input("desliga a luz da mesa"))
            assert res_t04_table.code == "success", res_t04_table
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["light.quarto_mesa"]
            rt.close()

            # T05 (table) & T37: untrusted default / unknown origin -> missing_origin_area clarification, 0 HA calls
            hass = _build_house_hass()
            _add_entity(hass, "l_q1", "light.quarto_1", "Luz Quarto", area="area_quarto")
            _add_entity(hass, "l_s1", "light.sala_1", "Luz Sala", area="area_sala")
            rt = ContextualRuntime(hass, client, lambda: {"default_area": "area_quarto"})
            res_untrusted = await rt.process(_input("desliga luz", satellite_id=None))
            assert res_untrusted.code == "missing_slot", res_untrusted
            assert res_untrusted.reason == "missing_origin_area"
            assert "Em qual cômodo?" in (res_untrusted.response_text or "")
            assert hass.services.calls == []
            rt.close()

            # T06: hidden vs exposed light in same room -> hidden never revealed/controlled
            hass = _build_house_hass()
            _add_entity(hass, "l_exp", "light.quarto_visivel", "Visível", area="area_quarto", exposed=True)
            _add_entity(hass, "l_hid", "light.quarto_oculta", "Oculta", area="area_quarto", exposed=False)
            rt = ContextualRuntime(hass, client)
            res_t06 = await rt.process(_input("desliga luz"))
            assert res_t06.code == "success", res_t06
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["light.quarto_visivel"]
            rt.close()

        asyncio.run(run())

    def test_t07_t08_t09_t10_t36_compositional_ac_and_room_boundaries(self, rust_server):
        async def run():
            client = RustHttpClient(rust_server)
            hass = _build_house_hass()
            _add_entity(hass, "ar_q", "climate.ar_quarto", "Ar-condicionado", area="area_quarto")
            _add_entity(hass, "term_b", "climate.termostato_banheiro", "Termostato", area="area_banheiro")

            rt = ContextualRuntime(hass, client)
            # T07/T08: liga condicionado / ar / ar-condicionado / split -> resolves Ar-condicionado in quarto
            for phrase in ("liga condicionado", "liga o ar", "liga o ar-condicionado", "liga o split"):
                hass.services.calls.clear()
                res = await rt.process(_input(phrase, conversation_id=phrase))
                assert res.code == "success", (phrase, res)
                assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["climate.ar_quarto"]

            # T10: explicit wrong room "liga o condicionado da cozinha" must NOT touch quarto AC
            hass.services.calls.clear()
            res_wrong = await rt.process(_input("liga o condicionado da cozinha", conversation_id="wrong_room"))
            assert res_wrong.code in ("unavailable", "missing_slot", "no_match"), res_wrong
            assert hass.services.calls == []

            # T36: "Termostato" in banheiro must NOT be matched as "condicionado"
            _add_entity(hass, "sat_b", "assist_satellite.banheiro", "Sat Banheiro", {}, area="area_banheiro")
            rt.catalog.invalidate()
            res_term = await rt.process(_input("liga o condicionado", satellite_id="assist_satellite.banheiro", conversation_id="term"))
            assert res_term.code != "success", res_term
            assert hass.services.calls == []

            # T09: two ACs in the same room -> asks clarification, 0 HA calls
            _add_entity(hass, "ar_q2", "climate.ar_quarto_split", "Ar-condicionado Split", area="area_quarto")
            rt.catalog.invalidate()
            res_ambig = await rt.process(_input("liga condicionado", conversation_id="ambig_ac"))
            assert res_ambig.code == "missing_slot", res_ambig
            assert res_ambig.continue_conversation is True
            assert res_ambig.reason == "ambiguous_target"
            assert "Ar-condicionado" in (res_ambig.response_text or "")
            assert hass.services.calls == []
            rt.close()

        asyncio.run(run())

    def test_t11_t12_t31_t32_progressive_v5_and_decision_partial_contract(self, rust_server):
        async def run():
            client = RustHttpClient(rust_server)
            hass = _build_house_hass()
            _add_entity(hass, "tv_q", "media_player.tv_quarto", "Televisão", area="area_quarto", aliases=["tv"])
            _add_entity(hass, "ar_q", "climate.ar_quarto", "Ar-condicionado", area="area_quarto", aliases=["ar"])

            # Verify arandu_decision service accepts BOTH "partial" and "decision_partial"
            from arandu_decision.service import PORT as DECISION_PORT, create_app
            from aiohttp.test_utils import TestClient, TestServer
            app = create_app(root=ROOT / "arandu_stack/rootfs/modules/arandu_decision/rootfs")
            # Directly verify reason_partial deterministic grounding vs phonetic repair
            partial_target = {
                "version": 5,
                "request_id": "req-t07",
                "catalog_generation": "a" * 64,
                "identity_scope_hash": "b" * 64,
                "origin": {"area_id": "area_quarto", "source": "satellite", "context_id": None},
                "request_kind": "possible_device_action",
                "clauses": [{
                    "clause_id": 0,
                    "original_text": "liga o condicionado",
                    "slots": {
                        "action": {"value": "turn_on", "status": "resolved", "evidence": ["nlu_intent:climate.turn_on"], "source": "nlu_rule"},
                        "target": {"value": None, "status": "candidate", "evidence": ["catalog_token_match:condicionado"], "source": "catalog_alias", "candidates": ["ar_q"]},
                        "area": {"value": "area_quarto", "status": "resolved", "evidence": ["origin_area:area_quarto"], "source": "contextual_reference"},
                        "parameters": {"value": {}, "status": "resolved", "evidence": ["no_parameter_requested"], "source": "nlu_rule"},
                    },
                    "allowed_actions": ["turn_on", "turn_off"],
                    "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                    "unresolved": ["target"],
                }],
                "safety_flags": [],
            }
            dec_target = reason_partial({"action": "decision_partial", "partial_understanding": partial_target})
            assert dec_target["status"] == "resolved_candidate"
            assert dec_target["requires_confirmation"] is False
            assert dec_target["calibration_valid"] is True
            assert dec_target["repairs"][0]["slot"] == "target"
            assert dec_target["repairs"][0]["value"] == "ar_q"

            # Verify /infer HTTP endpoint accepts action="decision_partial"
            async with TestClient(TestServer(app)) as tc:
                resp = await tc.post(
                    "/infer",
                    json={"action": "decision_partial", "partial_understanding": partial_target},
                    headers={"Host": f"127.0.0.1:{DECISION_PORT}", "X-Arandu-Lab": "1"},
                )
                assert resp.status == 200
                body = await resp.json()
                assert body["status"] == "resolved_candidate"
                assert body["requires_confirmation"] is False

            # T12: "lingua a TV" (phonetic verb repair) requires confirmation and never auto-executes
            orch = ProgressiveOrchestrator()
            snapshot = {
                "version": 4,
                "generation": "f" * 64,
                "areas": [{"area_id": "area_quarto", "names": ["Quarto"]}],
                "entities": [{
                    "registry_id": "tv_q",
                    "entity_id": "media_player.tv_quarto",
                    "domain": "media_player",
                    "area_id": "area_quarto",
                    "name": "Televisão",
                    "aliases": ["tv"],
                    "actions": ["turn_on", "turn_off"],
                    "attributes": {"available": True},
                }],
            }
            by_id = {"tv_q": snapshot["entities"][0]}
            await client.async_catalog_v4(snapshot)

            async def _dec(p):
                return reason_partial(p)

            res_t12 = await orch.resolve(
                text="lingua a tv",
                snapshot_payload=snapshot,
                snapshot_by_id=by_id,
                origin_area="area_quarto",
                origin_source="satellite",
                origin_context_id="c1",
                user_id="user",
                interpret_v5=client.async_interpret_v5,
                review_v5=client.async_review_v5,
                decision_partial=_dec,
                decision_enabled=True,
                llm_enabled=False,
            )
            assert res_t12.outcome_kind == "confirmation_required", res_t12
            assert res_t12.operations[0]["action"] == "turn_on"
            assert res_t12.operations[0]["targets"] == ["tv_q"]

            # T32: LLM abstention/arbitrary refusal is NOT labeled semantic_safety_refusal
            async def _llm_fake_refusal(p, d, g):
                return {"mode": "refusal", "reason": "uncertain"}

            res_t32 = await orch.resolve(
                text="faz aquela coisa estranha com a tv",
                snapshot_payload=snapshot,
                snapshot_by_id=by_id,
                origin_area="area_quarto",
                origin_source="satellite",
                origin_context_id="c2",
                user_id="user",
                interpret_v5=client.async_interpret_v5,
                review_v5=client.async_review_v5,
                decision_partial=None,
                llm_handoff=_llm_fake_refusal,
                decision_enabled=False,
                llm_enabled=True,
            )
            assert res_t32.outcome_kind == "clarification", res_t32
            assert res_t32.operations == ()
            assert "restrição que não consigo aplicar" not in (res_t32.speech or "")

        asyncio.run(run())

    def test_t13_to_t22_and_t40_multiturn_respeaker_continuity_and_limits(self, rust_server):
        async def run():
            client = RustHttpClient(rust_server)

            # T13 & T14: "ar condicionado" (no verb) -> asks action on ReSpeaker WITHOUT flattening -> "liga" -> executes
            hass = _build_house_hass(respeaker=True)
            _add_entity(hass, "ar_q", "climate.ar_quarto", "Ar-condicionado", area="area_quarto")
            rt = ContextualRuntime(hass, client)
            t13 = await rt.process(_input("ar condicionado", conversation_id="conv-t13"))
            assert t13.code == "missing_slot", t13
            assert t13.continue_conversation is True
            assert "Ar-condicionado" in (t13.response_text or "")
            assert t13.response_text != "Não entendi."
            assert rt.last_outcome["flattened_by_profile"] is False

            t14 = await rt.process(_input("liga", conversation_id="conv-t13"))
            assert t14.code == "success", t14
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["climate.ar_quarto"]
            assert hass.services.calls[-1]["service"] == "turn_on"

            # T16: "a luz da mesa" -> "apaga" preserves nominal target
            hass.services.calls.clear()
            _add_entity(hass, "l_mesa", "light.mesa", "Luz da mesa", area="area_quarto")
            _add_entity(hass, "l_teto", "light.teto", "Luz do teto", area="area_quarto")
            rt.catalog.invalidate()
            t16_a = await rt.process(_input("a luz da mesa", conversation_id="conv-t16"))
            assert t16_a.code == "missing_slot", t16_a
            assert t16_a.continue_conversation is True
            t16_b = await rt.process(_input("apaga", conversation_id="conv-t16"))
            assert t16_b.code == "success", t16_b
            assert t16_b.continue_conversation is False  # T35
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["light.mesa"]
            assert hass.services.calls[-1]["service"] == "turn_off"

            # T15: ambiguous "liga condicionado" -> "o split da janela" fills target & executes
            hass.services.calls.clear()
            _add_entity(hass, "ar_split", "climate.split_janela", "Ar-condicionado Split da Janela", area="area_quarto", aliases=["split", "ar"])
            rt.catalog.invalidate()
            t15_a = await rt.process(_input("liga condicionado", conversation_id="conv-t15"))
            assert t15_a.code == "missing_slot", t15_a
            assert t15_a.continue_conversation is True
            assert rt.last_outcome["flattened_by_profile"] is False
            t15_b = await rt.process(_input("o split da janela", conversation_id="conv-t15"))
            assert t15_b.code == "success", t15_b
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["climate.split_janela"]

            # T17: "liga esse" when last_targets holds 2 entities -> ambiguous_target, 0 HA calls
            hass.services.calls.clear()
            sess_t17 = rt.sessions[(hass.auth.user.id if hasattr(hass, "auth") else "user", "conv-t17")] if ("user", "conv-t17") in rt.sessions else None
            t17_setup = await rt.process(_input("liga o ar-condicionado e a Luz da mesa", conversation_id="conv-t17"))
            hass.services.calls.clear()
            t17_pron = await rt.process(_input("desliga esse", conversation_id="conv-t17"))
            assert t17_pron.code in ("missing_slot", "unavailable", "no_match"), t17_pron
            assert hass.services.calls == []

            # T18: slot correction "não, a da varanda" does NOT trigger semantic_negation
            hass.services.calls.clear()
            _add_entity(hass, "l_var", "light.varanda", "Luz da Varanda", area="area_varanda")
            rt.catalog.invalidate()
            t18_a = await rt.process(_input("liga luz", satellite_id=None, conversation_id="conv-t18"))
            assert t18_a.code == "missing_slot", t18_a
            t18_b = await rt.process(_input("não, a da varanda", satellite_id=None, conversation_id="conv-t18"))
            assert t18_b.code == "success", t18_b
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["light.varanda"]

            # T19: cancellation clears pending, 0 HA effects
            hass.services.calls.clear()
            t19_a = await rt.process(_input("liga condicionado", conversation_id="conv-t19"))
            assert t19_a.code == "missing_slot"
            t19_b = await rt.process(_input("cancela", conversation_id="conv-t19"))
            assert t19_b.code == "cancelled"
            assert t19_b.continue_conversation is False  # T35
            assert hass.services.calls == []

            # T22 & T21 & T34: satellite wake new conversation_id continues ONLY for same user & same satellite;
            # concurrent clarification on sala satellite stays completely isolated from quarto satellite
            hass.services.calls.clear()
            _add_entity(hass, "fan_s1", "fan.sala_1", "Ventilador Teto", area="area_sala")
            _add_entity(hass, "fan_s2", "fan.sala_2", "Ventilador Mesa", area="area_sala")
            rt.catalog.invalidate()
            t22_a = await rt.process(_input("liga condicionado", conversation_id="wake-1"))
            assert t22_a.code == "missing_slot"
            # Concurrent open clarification in sala satellite (T34)
            t34_sala_a = await rt.process(_input("liga o ventilador", satellite_id="assist_satellite.sala", conversation_id="wake-sala-1"))
            assert t34_sala_a.code == "missing_slot", t34_sala_a
            # Different user cannot hijack quarto pending
            t21_u = await rt.process(_input("o split da janela", conversation_id="wake-other-user", user_id="other_user"))
            assert t21_u.code != "success"
            assert hass.services.calls == []
            # Different satellite cannot resolve quarto's split AC
            t21_s = await rt.process(_input("o split da janela", conversation_id="wake-other-sat", satellite_id="assist_satellite.sala"))
            assert t21_s.code != "success"
            assert hass.services.calls == []
            # Resolve sala satellite independently
            t34_sala_b = await rt.process(_input("o de mesa", satellite_id="assist_satellite.sala", conversation_id="wake-sala-2"))
            assert t34_sala_b.code == "success", t34_sala_b
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["fan.sala_2"]
            # Same user + same quarto satellite with new conversation_id resolves quarto split AC
            hass.services.calls.clear()
            t22_b = await rt.process(_input("o split da janela", conversation_id="wake-2"))
            assert t22_b.code == "success", t22_b
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["climate.split_janela"]

            # T40: 3 insufficient attempts close pending with reformulation guidance
            hass.services.calls.clear()
            await rt.process(_input("liga condicionado", conversation_id="conv-t40"))
            await rt.process(_input("coisa inexistente um", conversation_id="conv-t40"))
            await rt.process(_input("coisa inexistente dois", conversation_id="conv-t40"))
            t40_end = await rt.process(_input("coisa inexistente tres", conversation_id="conv-t40"))
            assert t40_end.reason == "max_clarification_attempts", t40_end
            assert t40_end.continue_conversation is False
            assert hass.services.calls == []
            rt.close()

        asyncio.run(run())

    def test_t20_ttl_and_catalog_change_and_t23_to_t39_safety_and_traces(self, rust_server):
        async def run():
            client = RustHttpClient(rust_server)
            hass = _build_house_hass(respeaker=True)
            _add_entity(hass, "l_mesa", "light.mesa", "Luz da mesa", area="area_quarto")
            _add_entity(hass, "l_nao_desl", "light.nao_desliga", "Luz Não Desliga", area="area_sala")
            _add_entity(hass, "tv_sala", "media_player.tv_sala", "TV", area="area_sala")
            _add_entity(hass, "gel_sala", "switch.geladeira", "Geladeira", area="area_sala")
            _add_entity(hass, "temp_sala", "sensor.temperatura_sala", "Temperatura da sala", {"device_class": "temperature", "unit_of_measurement": "°C"}, state="24", area="area_sala")
            _add_entity(hass, "ar_unico", "climate.ar_quarto", "Ar-condicionado", area="area_quarto")
            rt = ContextualRuntime(hass, client)

            # T20: catalog change invalidates active pending
            await rt.process(_input("ar condicionado", conversation_id="conv-t20"))
            _add_entity(hass, "fan_extra", "fan.extra", "Ventilador Extra", area="area_quarto")
            rt.catalog.invalidate()
            t20_after = await rt.process(_input("liga", conversation_id="conv-t20"))
            assert t20_after.code in ("stale", "missing_slot", "no_match"), t20_after
            assert hass.services.calls == []

            # T23: "desliga todas as coisas da sala"
            hass.services.calls.clear()
            t23 = await rt.process(_input("desliga todas as coisas da sala", conversation_id="conv-t23"))
            assert t23.code == "success", t23
            assert {c["target"]["entity_id"][0] for c in hass.services.calls} == {"light.nao_desliga", "media_player.tv_sala", "switch.geladeira"}

            # T24: "desliga tudo, menos a geladeira" on satellite sala
            hass.services.calls.clear()
            t24 = await rt.process(_input("desliga tudo, menos a geladeira", satellite_id="assist_satellite.sala", conversation_id="conv-t24"))
            assert t24.code == "success", t24
            called = {c["target"]["entity_id"][0] for c in hass.services.calls}
            assert "switch.geladeira" not in called
            assert called == {"light.nao_desliga", "media_player.tv_sala"}

            # T25 & T26: negation and condition get distinct specific messages (never generic)
            hass.services.calls.clear()
            t25 = await rt.process(_input("não desliga a luz", conversation_id="conv-t25"))
            assert t25.code == "cancelled", t25
            assert t25.reason == "semantic_negation"
            assert "não executar" in (t25.response_text or "")

            t26 = await rt.process(_input("desliga a luz quando eu sair", conversation_id="conv-t26"))
            assert t26.code == "unavailable", t26
            assert t26.reason == "semantic_condition"
            assert "condições" in (t26.response_text or "")
            assert t25.response_text != t26.response_text
            assert hass.services.calls == []

            # T28: verbal entity "Luz Não Desliga" executes without false semantic refusal
            hass.services.calls.clear()
            t28 = await rt.process(_input("apaga a Luz Não Desliga", conversation_id="conv-t28"))
            assert t28.code == "success", t28
            assert [c["target"]["entity_id"][0] for c in hass.services.calls] == ["light.nao_desliga"]

            # T27: read query ("quais lâmpadas estão ligadas?") executes read-only, 0 HA calls
            hass.services.calls.clear()
            _add_entity(hass, "l_q_on", "light.quarto_acesa", "Abajur Aceso", state="on", area="area_quarto")
            rt.catalog.invalidate()
            t27 = await rt.process(_input("quais lâmpadas estão ligadas?", conversation_id="conv-t27"))
            assert t27.code == "query_success", t27
            assert "Abajur Aceso" in (t27.response_text or "")
            assert hass.services.calls == []

            # T30: unavailable & unexposed entities are never controlled or leaked
            hass.services.calls.clear()
            _add_entity(hass, "fan_off", "fan.offline", "Exaustor", state="unavailable", area="area_quarto")
            _add_entity(hass, "fan_priv", "fan.privado", "Privado", state="off", area="area_quarto", exposed=False)
            rt.catalog.invalidate()
            t30_unavail = await rt.process(_input("liga o exaustor", conversation_id="conv-t30a"))
            assert t30_unavail.code != "success", t30_unavail
            t30_unexposed = await rt.process(_input("liga o privado", conversation_id="conv-t30b"))
            assert t30_unexposed.code != "success", t30_unexposed
            assert "privado" not in (t30_unexposed.response_text or "").casefold() or "não encontrei" in (t30_unexposed.response_text or "").casefold()
            assert hass.services.calls == []

            # T29: "liga a temperatura da sala" -> capability incompatible, no effect
            hass.services.calls.clear()
            t29 = await rt.process(_input("liga a temperatura da sala", conversation_id="conv-t29"))
            assert t29.code != "success", t29
            assert hass.services.calls == []

            # T33: compound command executes each clause once cleanly
            hass.services.calls.clear()
            t33 = await rt.process(_input("liga o ar-condicionado e apaga a Luz da mesa", conversation_id="conv-t33"))
            assert t33.code == "success", t33
            assert [(c["target"]["entity_id"][0], c["service"]) for c in hass.services.calls] == [
                ("climate.ar_quarto", "turn_on"),
                ("light.mesa", "turn_off"),
            ]

            # T39: unknown mention cites the fragment instead of generic "Não entendi."
            hass.services.calls.clear()
            t39 = await rt.process(_input("liga a geringonça", conversation_id="conv-t39"))
            assert t39.code != "success"
            assert "geringonça" in (t39.response_text or "")

            # Dialogue TTL guard in PendingDialogue / PendingRequestStore
            req = PendingDialogue(
                user_id="user",
                origin_id="assist_satellite.quarto",
                conversation_id="wake-ttl",
                generation="g" * 64,
                origin_area="area_quarto",
                created_at=100.0,
                ttl_s=90.0,
            )
            store = PendingRequestStore()
            store.put(req)
            assert store.get_valid_for_origin(user_id="user", origin_id="assist_satellite.quarto", generation="g" * 64, max_age_seconds=30.0, now=120.0) is not None
            assert store.get_valid_for_origin(user_id="user", origin_id="assist_satellite.quarto", generation="g" * 64, max_age_seconds=30.0, now=140.0) is None
            rt.close()

        asyncio.run(run())

    def test_latency_and_ai_lab_provenance_parity(self, rust_server):
        from custom_components.local_nlu.ai_lab import Lab

        async def run():
            client = RustHttpClient(rust_server)
            hass = _build_house_hass(respeaker=True)
            _add_entity(hass, "ar_q", "climate.ar_quarto", "Ar-condicionado", area="area_quarto")
            _add_entity(hass, "l_q1", "light.quarto_1", "Luminária", area="area_quarto")

            # Verify AI Lab passes "satellite" as origin_area_source when test_area_id is selected
            hass.auth.user.id = "admin"
            hass.auth.user.is_active = True
            hass.auth.user.is_admin = True
            entry = SimpleNamespace(entry_id="e1", options={}, data={}, runtime_data=SimpleNamespace(_client=client))
            hass.config_entries = SimpleNamespace(async_entries=lambda _: [entry])

            observed_requests = []
            orig_v4 = client.async_interpret_v4

            async def spy_v4(payload):
                observed_requests.append(payload)
                return await orig_v4(payload)

            client.async_interpret_v4 = spy_v4
            with patch.dict(
                sys.modules,
                {
                    "homeassistant.core": SimpleNamespace(Context=lambda user_id: SimpleNamespace(user_id=user_id)),
                    "homeassistant.helpers.aiohttp_client": SimpleNamespace(async_get_clientsession=lambda _: None),
                },
            ):
                lab = Lab(hass)
                out = await lab._handle(
                    hass.auth.user,
                    {"command": "run", "mode": "nlu", "text": "desliga luz", "test_area_id": "area_quarto"},
                    None,
                    rust_server,
                )
            assert observed_requests[-1]["origin_area_source"] == "satellite"
            assert out["origin_area_source"] == "satellite"
            assert out["target_entities"] == ["light.quarto_1"]

            # Measure real latencies (NLU direct, Clarification, Decision partial)
            hass.auth.user.id = "user"
            rt = ContextualRuntime(hass, client)
            t0 = time.perf_counter()
            bench_nlu = await rt.process(_input("desliga luz", conversation_id="bench-1"))
            nlu_ms = (time.perf_counter() - t0) * 1000.0
            assert bench_nlu.code == "success", bench_nlu

            t1 = time.perf_counter()
            bench_clarify = await rt.process(_input("ar condicionado", conversation_id="bench-2"))
            clarify_ms = (time.perf_counter() - t1) * 1000.0
            assert bench_clarify.code == "missing_slot", bench_clarify

            t2 = time.perf_counter()
            reason_partial({"partial_understanding": {
                "version": 5, "request_id": "b", "catalog_generation": "a" * 64, "identity_scope_hash": "b" * 64,
                "origin": {"area_id": "area_quarto", "source": "satellite", "context_id": None},
                "request_kind": "possible_device_action",
                "clauses": [{
                    "clause_id": 0, "original_text": "liga condicionado",
                    "slots": {
                        "action": {"value": "turn_on", "status": "resolved", "evidence": ["nlu_intent:climate.turn_on"], "source": "nlu_rule"},
                        "target": {"value": None, "status": "candidate", "evidence": ["catalog_token_match:condicionado"], "candidates": ["ar_q"]},
                        "area": {"value": "area_quarto", "status": "resolved", "evidence": ["origin_area:area_quarto"]},
                    },
                    "allowed_actions": ["turn_on", "turn_off"],
                    "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                    "unresolved": ["target"],
                }],
                "safety_flags": [],
            }})
            dec_ms = (time.perf_counter() - t2) * 1000.0
            rt.close()
            print(f"\nBENCHMARK_LATENCIES nlu_ms={nlu_ms:.2f} clarify_ms={clarify_ms:.2f} decision_partial_ms={dec_ms:.2f}")
            assert nlu_ms < 500.0
            assert clarify_ms < 500.0
            assert dec_ms < 100.0

        asyncio.run(run())

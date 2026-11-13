import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_local_nlu_and_docker_mirror_are_in_sync():
    repo = Path(__file__).resolve().parent.parent
    src = repo / "custom_components" / "local_nlu"
    dst = repo / "arandu_stack" / "rootfs" / "modules" / "arandu_nlu" / "rootfs" / "src" / "custom_components" / "local_nlu"
    assert src.is_dir() and dst.is_dir()
    src_files = {p.relative_to(src).as_posix(): p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    dst_files = {p.relative_to(dst).as_posix(): p for p in dst.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert set(src_files) == set(dst_files), f"Arquivos divergentes: {set(src_files) ^ set(dst_files)}"
    for rel, sp in src_files.items():
        sb = sp.read_bytes().replace(b"\r\n", b"\n")
        db = dst_files[rel].read_bytes().replace(b"\r\n", b"\n")
        assert sb == db, f"Arquivo dessincronizado: {rel}"


class TestHotfixAsync(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_apply_verifies_weights_and_rolls_back_on_corruption(self):
        import tempfile
        import sys
        llm_src = Path(__file__).resolve().parent.parent / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src"
        if str(llm_src) not in sys.path:
            sys.path.insert(0, str(llm_src))
        from arandu_llm import models as llm_models, service as llm_service
        from aiohttp.test_utils import TestClient, TestServer

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            payload_bytes = b"GGUF" + b"\x00" * 60
            sha = hashlib.sha256(payload_bytes).hexdigest()
            fake_info = {
                "name": "Tucano Test",
                "kind": "llm",
                "license": "Apache-2.0",
                "repo": "test/tucano",
                "revision": "rev1",
                "file": "tucano-test.gguf",
                "size": len(payload_bytes),
                "sha256": sha,
                "runtime": "llama.cpp / CPU",
                "thinking": False,
                "experimental": False,
            }
            model_dir = tmp_path / "tucano"
            model_dir.mkdir(parents=True)
            gguf_path = model_dir / "tucano-test.gguf"
            gguf_path.write_bytes(payload_bytes)
            fake_llama = tmp_path / "llama-server"
            fake_llama.write_text("#!/bin/sh\n", encoding="utf-8")

            with patch.dict(llm_models.MODELS, {"tucano": fake_info}), patch.dict(llm_service.MODELS, {"tucano": fake_info}):
                app = llm_service.create_app(tmp_path, executable=str(fake_llama), threads=2, runtime_settings={"llm_model": "tucano", "resident_policy": "LAZY"})
                async with TestClient(TestServer(app)) as client:
                    headers = {"Host": f"127.0.0.1:{llm_service.PORT}", "X-Arandu-Lab": "1"}
                    resp = await client.post("/infer", json={
                        "action": "apply",
                        "llm_model": "tucano",
                        "resident_policy": "LAZY",
                        "generation": {"threads": 3, "context_size": 2048, "max_tokens": 128, "temperature": 0.1},
                        "contract_version": 2,
                    }, headers=headers)
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["weights_installed"] is True
                    assert data["weights_verified"] is True
                    assert data["integrity_verified"] is True
                    assert data["loaded"] is False
                    assert data["model_loaded"] is False
                    assert data["load_state"] == "unloaded"
                    assert data["resident_policy"] == "LAZY"
                    assert isinstance(data["integrity_check_ms"], (int, float))

                    # Corrupt weights on disk to verify failure + rollback
                    gguf_path.write_bytes(b"BAD!" + b"\x00" * 60)
                    resp_bad = await client.post("/infer", json={
                        "action": "apply",
                        "llm_model": "tucano",
                        "resident_policy": "LAZY",
                        "generation": {"threads": 4},
                        "contract_version": 2,
                    }, headers=headers)
                    assert resp_bad.status == 422
                    st = await (await client.get("/status", headers=headers)).json()
                    assert st["threads"] == 3

    async def test_session_history_isolation_and_cache_prompt(self):
        import sys
        llm_src = Path(__file__).resolve().parent.parent / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src"
        if str(llm_src) not in sys.path:
            sys.path.insert(0, str(llm_src))
        from arandu_llm import structured
        from arandu_llm.inference_slot import inference_slot

        slot_timings = {}
        async with inference_slot(slot_timings):
            assert "inference_slot_wait_ms" in slot_timings
            assert slot_timings["inference_slot_wait_ms"] >= 0

        adapter = SimpleNamespace(
            settings={"llm_model": "tucano", "max_tokens": 128, "temperature": 0.1, "profiles": {}},
            threads=2,
            port=19999,
            process=SimpleNamespace(pid=12345),
            last_integrity_ms=1.2,
            phase="init",
        )
        catalog = {"entities": [], "areas": []}
        captured_requests = []

        async def fake_completion(_adapter, req, *, cache_prompt=False):
            captured_requests.append((req, cache_prompt))
            return "Tudo ótimo!", {
                "inference_ms": 12.0,
                "first_token_ms": 4.0,
                "prompt_tokens": 20,
                "generated_tokens": 3,
                "prompt_eval_ms": 5.0,
                "decode_ms": 7.0,
                "tokens_per_second": 250.0,
                "decode_tokens_per_second": 300.0,
                "prefix_cache_requested": cache_prompt,
                "cached_prompt_tokens": 10 if cache_prompt else 0,
                "cache_hit": bool(cache_prompt),
            }

        with patch.object(structured, "ensure", AsyncMock(return_value=0.0)), patch.object(structured, "completion", fake_completion):
            r1 = await structured.infer(adapter, {"text": "Oi tudo bem?", "decision": {"route": "general_conversation"}, "session_id": "s1", "keep_context": True, "cache_prompt": True, "context": {"user_id": "u1"}}, catalog)
            assert r1["kind"] == "conversation"
            assert r1["cache_hit"] is True
            assert captured_requests[-1][1] is True
            assert len(captured_requests[-1][0]["messages"]) == 2

            await structured.infer(adapter, {"text": "Como vai você?", "decision": {"route": "general_conversation"}, "session_id": "s1", "keep_context": True, "context": {"user_id": "u1"}}, catalog)
            assert len(captured_requests[-1][0]["messages"]) == 4

            await structured.infer(adapter, {"text": "Bom dia", "decision": {"route": "general_conversation"}, "session_id": "s2", "keep_context": True, "context": {"user_id": "u1"}}, catalog)
            assert len(captured_requests[-1][0]["messages"]) == 2

            await structured.infer(adapter, {"text": "Boa tarde", "decision": {"route": "general_conversation"}, "session_id": "s1", "keep_context": True, "context": {"user_id": "u2"}}, catalog)
            assert len(captured_requests[-1][0]["messages"]) == 2

            await structured.infer(adapter, {"text": "Boa noite", "decision": {"route": "general_conversation"}, "session_id": "s1", "keep_context": False, "context": {"user_id": "u1"}}, catalog)
            assert len(captured_requests[-1][0]["messages"]) == 2

            await structured.infer(adapter, {"text": "Me conte uma piada", "decision": {"route": "general_conversation"}, "session_id": "s1", "keep_context": True, "reset_session": True, "context": {"user_id": "u1"}}, catalog)
            assert len(captured_requests[-1][0]["messages"]) == 2


def test_real_ha_state_query_reflects_live_changes_and_respects_authorization():
    from custom_components.local_nlu.light_aggregation import evaluate_aggregated_light_query, is_aggregated_light_query

    assert is_aggregated_light_query("Qual luz está ligada?")
    states = {
        "light.sala": SimpleNamespace(state="off", attributes={"friendly_name": "Luz da Sala"}),
        "light.quarto": SimpleNamespace(state="off", attributes={"friendly_name": "Luz do Quarto"}),
        "light.segredo": SimpleNamespace(state="on", attributes={"friendly_name": "Luz Secreta"}),
    }
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda eid: states.get(eid)))
    snapshot = SimpleNamespace(
        payload={
            "entities": [
                {"entity_id": "light.sala", "domain": "light", "name": "Luz da Sala", "area_id": "sala"},
                {"entity_id": "light.quarto", "domain": "light", "name": "Luz do Quarto", "area_id": "quarto"},
            ],
            "areas": [{"area_id": "sala", "names": ["Sala"]}, {"area_id": "quarto", "names": ["Quarto"]}],
        }
    )

    res1 = evaluate_aggregated_light_query("Qual luz está ligada?", snapshot, hass)
    assert res1 is not None
    assert "0 luzes" in res1["speech"] or "nenhuma" in res1["speech"].lower()
    assert "Secreta" not in res1["speech"]

    states["light.sala"] = SimpleNamespace(state="on", attributes={"friendly_name": "Luz da Sala"})
    res2 = evaluate_aggregated_light_query("Qual luz está ligada?", snapshot, hass)
    assert res2 is not None
    assert "Luz da Sala" in res2["speech"]
    assert "Secreta" not in res2["speech"]


def test_panel_asset_tag_cache_busting_and_frontend_markers():
    import re
    from custom_components.local_nlu.ai_lab import _panel_asset_tag
    repo = Path(__file__).resolve().parent.parent
    panel_js = (repo / "custom_components" / "local_nlu" / "ai_lab_panel.js").read_text(encoding="utf-8")
    tag = _panel_asset_tag()
    assert re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{12}", tag), f"Tag inesperada: {tag}"
    for marker in ("playAudio(", "Tocar áudio", "ttsState", "playbackState", "dataset.panelVersion"):
        assert marker in panel_js, f"Marcador ausente no frontend: {marker}"


class TestFastPathAndMetricsAsync(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_read_query_uses_fast_path_without_calling_llm(self):
        import sys
        import types
        from custom_components.local_nlu.ai_lab import Lab

        entry = SimpleNamespace(
            entry_id="entry-1",
            options={"decision_enabled": True, "llm_enabled": True, "stack_endpoint": "http://127.0.0.1:11555"},
            data={},
            runtime_data=SimpleNamespace(_client=SimpleNamespace(_endpoint="http://127.0.0.1:11555", async_catalog_v4=AsyncMock(return_value={"version": 4, "status": "catalog_ready", "generation": 1}))),
        )
        states = {"light.sala": SimpleNamespace(state="on", attributes={"friendly_name": "Luz da Sala"})}
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_entries=lambda _domain: [entry]),
            states=SimpleNamespace(get=lambda eid: states.get(eid)),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")),
        )
        fake_snapshot = SimpleNamespace(
            payload={
                "generation": 1,
                "entities": [{"entity_id": "light.sala", "domain": "light", "name": "Luz da Sala", "area_id": "sala", "actions": ["turn_on", "turn_off", "query"]}],
                "areas": [{"area_id": "sala", "names": ["Sala"]}],
            },
            by_id={"light.sala": {"entity_id": "light.sala", "domain": "light", "name": "Luz da Sala", "area_id": "sala", "actions": ["turn_on", "turn_off", "query"]}},
        )
        user = SimpleNamespace(id="user-1", is_admin=True, is_active=True)
        lab = Lab(hass)

        fake_ha_core = types.ModuleType("homeassistant.core")
        fake_ha_core.Context = lambda user_id=None: SimpleNamespace(user_id=user_id)
        fake_ha_helpers = types.ModuleType("homeassistant.helpers")
        fake_ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
        fake_ha_aiohttp.async_get_clientsession = lambda _h: SimpleNamespace()

        local_req_spy = AsyncMock(side_effect=AssertionError("LLM/Decision não deveria ser chamado no fast path"))
        with patch.dict(sys.modules, {"homeassistant.core": fake_ha_core, "homeassistant.helpers": fake_ha_helpers, "homeassistant.helpers.aiohttp_client": fake_ha_aiohttp}), \
             patch("custom_components.local_nlu.contextual_catalog.build", return_value=fake_snapshot), \
             patch("custom_components.local_nlu.contextual_catalog.origin_area", return_value=("sala", "lab_selection")), \
             patch("custom_components.local_nlu.ai_lab.local_request", local_req_spy):
            res = await lab.handle(user, {"command": "run", "text": "Qual luz tá ligada?", "mode": "pipeline"})

        assert local_req_spy.call_count == 0
        assert res["source"] == "ha_read_fast_path"
        assert res["kind"] == "query"
        assert res["operations_validated"] is False
        assert "execution_token" not in res
        assert res["stages"]["llm"]["status"] == "skipped"
        assert res["stages"]["ha"]["status"] == "executed"
        assert res["stages"]["ha"]["read_only"] is True
        assert "Luz da Sala" in res["response_text"]

    async def test_completion_separates_decode_rate_from_e2e_and_tracks_cache_hit(self):
        import sys
        llm_src = Path(__file__).resolve().parent.parent / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src"
        if str(llm_src) not in sys.path:
            sys.path.insert(0, str(llm_src))
        from arandu_llm import structured

        chunks = [
            b'data: {"choices":[{"delta":{"content":"Oi!"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":40,"completion_tokens":10,"prompt_tokens_details":{"cached_tokens":32}},"timings":{"prompt_ms":120.0,"predicted_ms":500.0,"predicted_per_second":20.0}}\n',
            b"data: [DONE]\n",
        ]

        class FakeContent:
            def __aiter__(self):
                self._it = iter(chunks)
                return self
            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        class FakeResponse:
            status = 200
            content = FakeContent()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return False

        class FakeSession:
            def __init__(self, *_, **__):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return False
            def post(self, *_a, **_kw):
                return FakeResponse()

        import aiohttp
        with patch.object(aiohttp, "ClientSession", FakeSession):
            raw, metrics = await structured.completion(SimpleNamespace(port=19999), {"messages": []}, cache_prompt=True)

        assert raw == "Oi!"
        assert metrics["prompt_eval_ms"] == 120.0
        assert metrics["decode_ms"] == 500.0
        assert metrics["decode_tokens_per_second"] == 20.0
        assert metrics["end_to_end_tokens_per_second"] == metrics["tokens_per_second"]
        assert metrics["cached_prompt_tokens"] == 32
        assert metrics["cache_hit"] is True
        assert metrics["prefix_cache_requested"] is True

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
    from custom_components.local_nlu.ai_lab import _panel_asset_tag, _panel_version_info
    repo = Path(__file__).resolve().parent.parent
    panel_js = (repo / "custom_components" / "local_nlu" / "ai_lab_panel.js").read_text(encoding="utf-8")
    tag = _panel_asset_tag()
    info = _panel_version_info()
    assert re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{12}", tag), f"Tag inesperada: {tag}"
    assert info["panel_tag"] == tag
    assert info["backend_version"] == "11.3.9"
    for marker in (
        "playAudio(",
        "Tocar áudio",
        "ttsState",
        "playbackState",
        "dataset.panelVersion",
        "PANEL_VERSION = '11.3.9'",
        "versionDiagnostics",
        "versionWarning",
        "O painel carregado está desatualizado. Atualize o frontend do Arandu.",
    ):
        assert marker in panel_js, f"Marcador ausente no frontend: {marker}"


def test_is_exclusively_read_query_matrix():
    from custom_components.local_nlu.ai_lab import is_exclusively_read_query

    catalog = {
        "entities": [
            {"entity_id": "light.sala", "domain": "light", "name": "Luz da Sala", "area_id": "sala"},
            {"entity_id": "binary_sensor.porta", "domain": "binary_sensor", "device_class": "door", "name": "Porta da Entrada", "area_id": "sala"},
            {"entity_id": "binary_sensor.janela", "domain": "binary_sensor", "device_class": "window", "name": "Janela do Quarto", "area_id": "quarto"},
            {"entity_id": "light.quarto_e_banheiro", "domain": "light", "name": "Luz do Quarto e Banheiro", "area_id": "quarto"},
        ],
        "areas": [
            {"area_id": "sala", "names": ["Sala"]},
            {"area_id": "quarto", "names": ["Quarto"]},
        ],
    }

    assert is_exclusively_read_query("Qual luz tá ligada?", catalog) is True
    assert is_exclusively_read_query("Qual porta está aberta?", catalog) is True
    assert is_exclusively_read_query("Quais janelas estão abertas?", catalog) is True
    assert is_exclusively_read_query("Qual luz do Quarto e Banheiro está ligada?", catalog) is True

    for blocked in (
        "Qual luz tá ligada e liga o ventilador",
        "Qual luz tá ligada? Depois desliga todas",
        "Quantas janelas estão abertas menos a do quarto?",
        "Se estiver escuro, qual luz está ligada?",
        "Não me diga qual luz está ligada",
        "Liga a luz da sala",
        "Qual a temperatura da sala?",
    ):
        assert is_exclusively_read_query(blocked, catalog) is False, f"Não deveria acionar fast path para: {blocked}"


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

    async def test_completion_leaves_absent_timings_as_none_without_fabrication(self):
        import sys
        llm_src = Path(__file__).resolve().parent.parent / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src"
        if str(llm_src) not in sys.path:
            sys.path.insert(0, str(llm_src))
        from arandu_llm import structured

        chunks = [
            b'data: {"choices":[{"delta":{"content":"Ok"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":2}}\n',
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
            raw, metrics = await structured.completion(SimpleNamespace(port=19999), {"messages": []}, cache_prompt=False)

        assert raw == "Ok"
        assert metrics["prompt_eval_ms"] is None
        assert metrics["decode_ms"] is None
        assert metrics["decode_tokens_per_second"] is None
        assert metrics["cached_prompt_tokens"] is None
        assert metrics["cache_hit"] is False

    async def test_compound_query_and_isolated_modes_bypass_fast_path_and_upgrade_panel(self):
        import sys
        import types
        from custom_components.local_nlu import ai_lab

        # 1. Lifecycle test: upgrade from 11.3.7 panel without ValueError: Overwriting panel
        ws_calls = []
        removed = []

        async def strict_register_panel(hass, **kwargs):
            panels = hass.data.setdefault("frontend_panels", {})
            path = kwargs["frontend_url_path"]
            if path in panels:
                raise ValueError(f"Overwriting panel {path}")
            panels[path] = dict(kwargs)

        async def remove_panel(hass, path):
            removed.append(path)
            hass.data.get("frontend_panels", {}).pop(path, None)

        components = types.ModuleType("homeassistant.components")
        components.panel_custom = SimpleNamespace(async_register_panel=AsyncMock(side_effect=strict_register_panel))
        components.frontend = SimpleNamespace(async_remove_panel=AsyncMock(side_effect=remove_panel))
        components.websocket_api = SimpleNamespace(
            websocket_command=lambda _s: (lambda fn: fn),
            async_response=lambda fn: fn,
            async_register_command=lambda _h, fn: ws_calls.append(fn),
        )
        http_mod = types.ModuleType("homeassistant.components.http")
        http_mod.StaticPathConfig = lambda url, path, cache: SimpleNamespace(url=url, path=path, cache=cache)
        hass_reg = SimpleNamespace(
            data={"frontend_panels": {"arandu-ai-lab": {"module_url": "/local_nlu_ai_lab/panel.js?v=11.3.7-oldoldoldold"}}},
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        )

        with patch.dict(sys.modules, {
            "homeassistant.components": components,
            "homeassistant.components.frontend": components.frontend,
            "homeassistant.components.http": http_mod,
        }):
            await ai_lab.async_register(hass_reg)
            first_url = hass_reg.data["frontend_panels"]["arandu-ai-lab"]["module_url"]
            assert first_url.endswith(ai_lab._panel_asset_tag())
            assert removed == ["arandu-ai-lab"]
            assert len(ws_calls) == 1

            # Re-registering with identical tag is a no-op
            await ai_lab.async_register(hass_reg)
            assert components.panel_custom.async_register_panel.call_count == 1
            assert len(ws_calls) == 1

            # Simulating new asset hash updates panel cleanly without ValueError or duplicate WS
            with patch.object(ai_lab, "_panel_asset_tag", return_value="11.3.9-cafebabecafe"):
                await ai_lab.async_register(hass_reg)
            assert hass_reg.data["frontend_panels"]["arandu-ai-lab"]["module_url"] == "/local_nlu_ai_lab/panel.js?v=11.3.9-cafebabecafe"
            assert hass_reg.data["local_nlu_ai_lab_tag"] == "11.3.9-cafebabecafe"
            assert len(ws_calls) == 1

            # Rolling back previous tag if update fails unexpectedly
            with patch.object(ai_lab, "_panel_asset_tag", return_value="11.3.9-brokenhash00"):
                components.panel_custom.async_register_panel.side_effect = [RuntimeError("panel error"), None]
                try:
                    await ai_lab.async_register(hass_reg)
                except RuntimeError:
                    pass
            assert hass_reg.data["local_nlu_ai_lab_tag"] == "11.3.9-cafebabecafe"

        # 2. Compound command in pipeline mode & isolated mode must NOT short-circuit to fast path
        entry = SimpleNamespace(
            entry_id="entry-1",
            options={"decision_enabled": True, "llm_enabled": False, "stack_endpoint": "http://127.0.0.1:11555"},
            data={},
            runtime_data=SimpleNamespace(_client=SimpleNamespace(
                _endpoint="http://127.0.0.1:11555",
                async_catalog_v4=AsyncMock(return_value={"version": 4, "status": "catalog_ready", "generation": 1}),
                async_interpret_v4=AsyncMock(return_value={"version": 4, "status": "clarification", "reason": "ambiguous", "operations": [], "generation": 1}),
            )),
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
        lab = ai_lab.Lab(hass)
        fake_ha_core = types.ModuleType("homeassistant.core")
        fake_ha_core.Context = lambda user_id=None: SimpleNamespace(user_id=user_id)
        fake_ha_helpers = types.ModuleType("homeassistant.helpers")
        fake_ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
        fake_ha_aiohttp.async_get_clientsession = lambda _h: SimpleNamespace()

        async def fake_pipeline(text, nlu, decision, llm, **_kw):
            nlu_raw = await nlu(text)
            return {
                "text": text,
                "source": "nlu",
                "kind": "clarification",
                "response_text": None,
                "clarification_question": "Pode detalhar?",
                "confirmation_required": False,
                "sensitive": False,
                "operations_validated": False,
                "action_json": None,
                "target_entities": [],
                "proposal": nlu_raw,
                "total_ms": 5.0,
                "stages": {s: {"status": "skipped"} for s in ("vad", "stt", "nlu", "decision", "llm", "validation", "ha", "response", "tts")},
            }

        with patch.dict(sys.modules, {"homeassistant.core": fake_ha_core, "homeassistant.helpers": fake_ha_helpers, "homeassistant.helpers.aiohttp_client": fake_ha_aiohttp}), \
             patch("custom_components.local_nlu.contextual_catalog.build", return_value=fake_snapshot), \
             patch("custom_components.local_nlu.contextual_catalog.origin_area", return_value=("sala", "lab_selection")), \
             patch("custom_components.local_nlu.ai_lab.pipeline", fake_pipeline):
            res_compound = await lab.handle(user, {"command": "run", "text": "Qual luz tá ligada e liga o ventilador", "mode": "pipeline"})
            res_isolated = await lab.handle(user, {"command": "run", "text": "Qual luz tá ligada?", "mode": "nlu"})

        assert res_compound["source"] != "ha_read_fast_path"
        assert res_compound["backend_version"] == "11.3.9"
        assert res_isolated["source"] != "ha_read_fast_path"
        assert entry.runtime_data._client.async_interpret_v4.call_count == 2

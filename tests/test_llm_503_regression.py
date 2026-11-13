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

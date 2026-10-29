#!/usr/bin/env python3
"""Exercise the actual scratch image with synthetic HA execution boundary."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests/mlp')]
from test_contextual import HttpClient, hass_, input_
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


async def main(endpoint):
    with urllib.request.urlopen(endpoint + '/health', timeout=3) as response:
        assert json.load(response) == {'status': 'ok', 'version': 1}
    with urllib.request.urlopen(endpoint + '/diagnostics', timeout=3) as response:
        diagnostic = json.load(response)
        assert diagnostic['service_version'] == json.loads((ROOT / 'custom_components/local_nlu/manifest.json').read_text())['version']
        assert diagnostic['protocols'] == [1, 2, 3, 4] and diagnostic['execution'] == 'passive'
    hass = hass_()
    runtime = ContextualRuntime(hass, HttpClient(endpoint), lambda: {'default_area': 'area_sala'})
    for text, status in [('Liga a luz', 'success'), ('Qual é a temperatura aqui?', 'query_success'), ('Não liga a luz', 'cancelled'), ('Desliga tudo na sala', 'success')]:
        result = await runtime.process(input_(text, conversation_id=text))
        assert result.code == status, result
    assert len(hass.services.calls) == 3  # one light on; room light and fan off
    print('NLU 2.0 scratch image + Rust HTTP + Python executor smoke PASS')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint', default='http://127.0.0.1:11555')
    asyncio.run(main(parser.parse_args().endpoint))

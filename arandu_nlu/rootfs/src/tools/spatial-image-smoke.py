#!/usr/bin/env python3
"""FIXTURE_TECNICA: actual add-on image plus the authorized simulated HA boundary."""
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests/mlp")]
from test_contextual import HttpClient, input_
from test_contextual_spatial import house, called
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


async def main(endpoint):
    cases = [
        ("luz", "sala", "turn_on", {"light.sala_0", "light.sala_1"}),
        ("luz", "cozinha", "turn_on", {"light.cozinha_0", "light.cozinha_1"}),
        ("liga a sala", "cozinha", "turn_on", {"light.sala_0", "light.sala_1", "fan.sala"}),
        ("desliga todos os dispositivos da sala", "cozinha", "turn_off", {"light.sala_0", "light.sala_1", "fan.sala"}),
        ("desliga as luzes", "cozinha", "turn_off", {"light.cozinha_0", "light.cozinha_1"}),
    ]
    for text, room, service, expected in cases:
        hass = house()
        result = await ContextualRuntime(hass, HttpClient(endpoint)).process(input_(text, satellite_id="assist_satellite." + room))
        assert result.code == "success", result
        assert called(hass) == expected, (text, called(hass))
        assert all(call["service"] == service for call in hass.services.calls)
    hass = house()
    result = await ContextualRuntime(hass, HttpClient(endpoint), lambda: {"default_area": "area_sala"}).process(input_("luz"))
    assert result.code == "missing_slot" and result.response_text == "Em qual cômodo?", result
    assert not hass.services.calls
    print("SPATIAL ADDON IMAGE + Rust HTTP + Python simulated HA PASS (6 cases)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

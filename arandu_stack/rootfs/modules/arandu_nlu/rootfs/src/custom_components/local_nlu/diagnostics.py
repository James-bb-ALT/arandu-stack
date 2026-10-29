"""Home Assistant downloadable diagnostics, without options or private catalog."""
from __future__ import annotations

import json
from pathlib import Path


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    runtime = entry.runtime_data
    enabled = bool(runtime._contextual_enabled())
    version = json.loads(Path(__file__).with_name("manifest.json").read_text())["version"]
    configured = 4 if enabled else 3 if runtime._v2_enabled() else 1
    result = {"integration_version": version, "contextual_enabled": enabled,
              "protocol": getattr(runtime._client, "_last_interpret_protocol", configured), "configured_protocol": configured,
              "last_outcome": getattr(getattr(runtime, "_contextual", None), "last_outcome", None)}
    from .identity import identity_diagnostics
    contextual = getattr(runtime, "_contextual", None)
    result["identity"] = contextual.identity_diagnostics() if contextual is not None else identity_diagnostics(dict(entry.options) if hasattr(entry, "options") else {})
    try:
        service = await runtime._client.async_diagnostics()
        result.update(service)
        result["compatible"] = version == service["service_version"] and result["protocol"] in service["protocols"]
        if not result["compatible"]:
            result["warning"] = "integration_addon_version_mismatch"
    except Exception:
        result.update(compatible=False, warning="backend_diagnostics_unavailable")
    result["route"] = f"/v{result['protocol']}/interpret"
    return result

"""Unified Model Registry & Lifecycle helper for Arandu Stack (STT, TTS, Decision, LLM)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().parent.parent / 'model_registry' / 'models.json'

LIFECYCLE_STATES = (
    'catalogued',
    'source_verified',
    'downloadable',
    'installed',
    'integrity_verified',
    'runtime_compatible',
    'load_tested',
    'selectable',
    'active',
    'resident',
    'error',
)


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))


def list_models(kind: str | None = None, *, downloadable_only: bool = False) -> list[dict[str, Any]]:
    items = list(load_registry().get('items', []))
    if kind is not None:
        items = [item for item in items if item.get('kind') == kind]
    if downloadable_only:
        items = [item for item in items if item.get('downloadable') is True]
    return [dict(item) for item in items]


def resolve_legacy_id(model_id: str | None) -> str | None:
    if not model_id or not isinstance(model_id, str):
        return model_id
    for item in load_registry().get('items', []):
        if item.get('id') == model_id or model_id in (item.get('legacy_ids') or ()):
            return item.get('id')
    return model_id


def get_model(model_id: str | None) -> dict[str, Any] | None:
    canonical = resolve_legacy_id(model_id)
    if not canonical:
        return None
    for item in load_registry().get('items', []):
        if item.get('id') == canonical:
            return dict(item)
    return None


def model_ids(kind: str | None = None, *, include_legacy: bool = True, downloadable_only: bool = False) -> set[str]:
    ids: set[str] = set()
    for item in list_models(kind, downloadable_only=downloadable_only):
        ids.add(item['id'])
        if include_legacy:
            for alias in item.get('legacy_ids') or ():
                ids.add(alias)
    return ids


def lifecycle_state(
    item: dict[str, Any],
    *,
    installed: bool | None = None,
    integrity_verified: bool | None = None,
    loaded: bool | None = None,
    active: bool = False,
    resident: bool = False,
    error: str | None = None,
) -> str:
    if error:
        return 'error'
    if resident and loaded:
        return 'resident'
    if active and loaded:
        return 'active'
    if loaded:
        return 'load_tested'
    if integrity_verified:
        return 'integrity_verified'
    if installed:
        return 'installed'
    if item.get('downloadable'):
        return 'downloadable'
    if item.get('verified'):
        return 'source_verified'
    return 'catalogued'


def summarize_item(
    item: dict[str, Any],
    *,
    installed: bool | None = None,
    integrity_verified: bool | None = None,
    loaded: bool | None = None,
    active: bool = False,
    resident: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    files = item.get('files') or []
    disk_bytes = item.get('disk_bytes')
    if disk_bytes is None and files:
        sizes = [f.get('size') for f in files if isinstance(f.get('size'), int)]
        if len(sizes) == len(files):
            disk_bytes = sum(sizes)
    return {
        'id': item['id'],
        'legacy_ids': list(item.get('legacy_ids') or ()),
        'kind': item['kind'],
        'name': item['display_name'],
        'vendor': item.get('vendor'),
        'repo': item.get('repo_id'),
        'revision': item.get('revision_immutable'),
        'license': item.get('license'),
        'needs_auth': bool(item.get('needs_auth')),
        'runtime': item.get('runtime'),
        'architecture': item.get('architecture'),
        'format': item.get('format'),
        'quantization': item.get('quantization'),
        'disk_bytes': disk_bytes,
        'ram_estimate_mb': item.get('ram_estimate_mb'),
        'supports_cpu': bool(item.get('supports_cpu')),
        'supports_gpu_intel': bool(item.get('supports_gpu_intel')),
        'downloadable': bool(item.get('downloadable')),
        'testable': bool(item.get('testable')),
        'recommended': bool(item.get('recommended')),
        'verified': bool(item.get('verified')),
        'experimental': bool(item.get('experimental')),
        'restrictions': list(item.get('restrictions') or ()),
        'installed': installed,
        'integrity_verified': integrity_verified,
        'loaded': loaded,
        'active': active,
        'resident': resident,
        'error': error,
        'lifecycle_state': lifecycle_state(
            item,
            installed=installed,
            integrity_verified=integrity_verified,
            loaded=loaded,
            active=active,
            resident=resident,
            error=error,
        ),
    }

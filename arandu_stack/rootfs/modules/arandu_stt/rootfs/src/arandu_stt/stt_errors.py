"""Structured error codes and safe diagnostic logging for Arandu STT & pipeline."""
from __future__ import annotations

import json
import logging
from typing import Any

ERROR_CODES = frozenset({
    'DEPENDENCY_CONFLICT',
    'NATIVE_LIBRARY_MISSING',
    'MODEL_RUNTIME_UNAVAILABLE',
    'MODEL_LOAD_FAILED',
    'MODEL_INTEGRITY_FAILED',
    'STT_TIMEOUT',
    'NLU_TIMEOUT',
    'DECISION_UNAVAILABLE',
    'LLM_TIMEOUT',
    'TTS_FAILURE',
    'QUEUE_SATURATED',
    'MODULE_STARTUP_FAILED',
})


class AranduStructuredError(RuntimeError):
    """RuntimeError carrying a canonical structured error code and stage metadata."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        module: str = 'arandu_stt',
        stage: str = 'runtime',
        recovery: str | None = None,
        final_state: str | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f'Unknown structured error code: {code}')
        super().__init__(f'{code}: {message}')
        self.code = code
        self.module = module
        self.stage = stage
        self.recovery = recovery
        self.final_state = final_state


class AranduRuntimeUnavailableError(AranduStructuredError, ImportError):
    """Structured error that also satisfies ImportError callers."""

    def __init__(self, message: str, *, module: str = 'arandu_stt', stage: str = 'load') -> None:
        super().__init__('MODEL_RUNTIME_UNAVAILABLE', message, module=module, stage=stage)


def format_structured_error(
    code: str,
    module: str,
    stage: str,
    exc: BaseException | str,
    *,
    request_id: str | None = None,
    recovery: str | None = None,
    final_state: str | None = None,
) -> dict[str, Any]:
    """Build a structured diagnostic payload without tokens, audio or private transcripts."""
    if code not in ERROR_CODES:
        code = 'MODULE_STARTUP_FAILED'
    exc_type = type(exc).__name__ if isinstance(exc, BaseException) else 'Error'
    exc_msg = str(exc) if isinstance(exc, BaseException) else str(exc)
    # Truncate exception message to avoid leaking unexpectedly large payloads.
    if len(exc_msg) > 240:
        exc_msg = exc_msg[:237] + '...'
    return {
        'error_code': code,
        'module': module,
        'stage': stage,
        'exception_type': exc_type,
        'message': exc_msg,
        'request_id': request_id,
        'recovery': recovery,
        'final_state': final_state,
    }


def log_structured_error(
    logger: logging.Logger,
    code: str,
    module: str,
    stage: str,
    exc: BaseException | str,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = format_structured_error(code, module, stage, exc, **kwargs)
    logger.error('ARANDU_EVENT %s', json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload

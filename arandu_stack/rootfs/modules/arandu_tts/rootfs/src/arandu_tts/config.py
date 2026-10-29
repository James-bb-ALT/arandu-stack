"""Configuração central do Arandu TTS (Wyoming + laboratório Web)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def bool_env(name: str, default: bool = False) -> bool:
    return os.environ.get(name, '1' if default else '0').strip().lower() in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class Config:
    uri: str = 'tcp://0.0.0.0:10212'
    voices_dir: Path = Path('./voices')
    default_voice: str = 'pt_BR-faber-medium'
    auto_download: bool = True
    preload: bool = True
    max_chars: int = 3000
    piper_threads: int = 2
    fallback_voice: str = 'pt_BR-faber-medium'
    remote_fallback: bool = True
    remote_url: str = ''
    remote_token: str = ''
    remote_timeout: int = 60
    log_level: str = 'INFO'
    pocket_enabled: bool = True
    pocket_language: str = 'portuguese'
    pocket_threads: int = 2
    pocket_preload: bool = False
    pocket_fallback: bool = True
    web_enabled: bool = True
    web_host: str = '0.0.0.0'
    web_port: int = 8099
    web_token: str = ''
    web_ingress_only: bool = False
    max_upload_mb: int = 12
    max_audio_seconds: int = 30

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            uri=os.environ.get('ARANDU_URI', cls.uri),
            voices_dir=Path(os.environ.get('ARANDU_VOICES_DIR', './voices')).expanduser(),
            default_voice=os.environ.get('ARANDU_DEFAULT_VOICE', cls.default_voice),
            auto_download=bool_env('ARANDU_AUTO_DOWNLOAD', True),
            preload=bool_env('ARANDU_PRELOAD', True),
            max_chars=int(os.environ.get('ARANDU_MAX_CHARS', '3000')),
            piper_threads=int(os.environ.get('ARANDU_PIPER_THREADS', '2')),
            fallback_voice=os.environ.get('ARANDU_FALLBACK_VOICE', cls.fallback_voice),
            remote_fallback=bool_env('ARANDU_REMOTE_FALLBACK', True),
            remote_url=os.environ.get('ARANDU_REMOTE_URL', '').rstrip('/'),
            remote_token=os.environ.get('ARANDU_REMOTE_TOKEN', ''),
            remote_timeout=int(os.environ.get('ARANDU_REMOTE_TIMEOUT', '60')),
            log_level=os.environ.get('ARANDU_LOG_LEVEL', 'INFO'),
            pocket_enabled=bool_env('ARANDU_POCKET_ENABLED', True),
            pocket_language=os.environ.get('ARANDU_POCKET_LANGUAGE', 'portuguese'),
            pocket_threads=int(os.environ.get('ARANDU_POCKET_THREADS', '2')),
            pocket_preload=bool_env('ARANDU_POCKET_PRELOAD', False),
            pocket_fallback=bool_env('ARANDU_POCKET_FALLBACK', True),
            web_enabled=bool_env('ARANDU_WEB_ENABLED', True),
            web_host=os.environ.get('ARANDU_WEB_HOST', '0.0.0.0'),
            web_port=int(os.environ.get('ARANDU_WEB_PORT', '8099')),
            web_token=os.environ.get('ARANDU_WEB_TOKEN', ''),
            web_ingress_only=bool_env('ARANDU_WEB_INGRESS_ONLY', False),
            max_upload_mb=int(os.environ.get('ARANDU_MAX_UPLOAD_MB', '12')),
            max_audio_seconds=int(os.environ.get('ARANDU_MAX_AUDIO_SECONDS', '30')),
        )

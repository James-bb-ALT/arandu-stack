"""Descoberta de vozes Piper ONNX, Pocket TTS e worker remoto."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

VOICE_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


@dataclass(frozen=True)
class VoiceSpec:
    name: str
    language: str
    backend: str  # piper | pocket | remote
    model: Path | None = None
    remote_voice: str | None = None
    description: str = ''


def valid_voice_id(voice_id: str) -> bool:
    return bool(VOICE_ID.fullmatch(voice_id))


def catalogue(directory: Path, pocket_enabled: bool = True) -> dict[str, VoiceSpec]:
    directory.mkdir(parents=True, exist_ok=True)
    result: dict[str, VoiceSpec] = {}
    for model in sorted(directory.glob('*.onnx')):
        if model.is_symlink() or not valid_voice_id(model.stem):
            continue
        config = Path(str(model) + '.json')
        if not config.is_file() or config.is_symlink():
            continue
        try:
            data = json.loads(config.read_text(encoding='utf-8'))
            lang = data.get('language', {}).get('code') or data.get('espeak', {}).get('voice') or 'pt_BR'
            if not isinstance(lang, str):
                raise ValueError('invalid language')
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        result[model.stem] = VoiceSpec(model.stem, lang, 'piper', model, description=f'Piper: {model.stem}')

    if pocket_enabled:
        result['pocket-rafael'] = VoiceSpec('pocket-rafael', 'pt_BR', 'pocket',
                                            description='Pocket TTS · Rafael (voz incluída no catálogo Kyutai)')
        pocket_dir = directory / 'pocket'
        if pocket_dir.exists():
            for profile in sorted(pocket_dir.glob('*.safetensors')):
                if profile.is_symlink() or not valid_voice_id(profile.stem) or profile.stem == 'rafael':
                    continue
                name = f'pocket-{profile.stem}'
                if name in result:
                    continue
                result[name] = VoiceSpec(name, 'pt_BR', 'pocket', profile,
                                          description=f'Pocket TTS · clonagem: {profile.stem}')

    remotes = directory / 'remote_voices.json'
    if remotes.is_file() and not remotes.is_symlink():
        raw = json.loads(remotes.read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('remote_voices.json precisa ser objeto JSON')
        for name, record in raw.items():
            if not isinstance(name, str) or not valid_voice_id(name):
                raise ValueError(f'ID de voz remoto inválido: {name!r}')
            if name in result:
                raise ValueError(f'Nome de voz duplicado: {name}')
            if not isinstance(record, dict) or not valid_voice_id(str(record.get('remote_voice', ''))):
                raise ValueError(f'Voz remota inválida: {name}')
            result[name] = VoiceSpec(name, str(record.get('language', 'pt_BR')),
                                     'remote', remote_voice=record['remote_voice'],
                                     description=str(record.get('description', 'Voz customizada via GPU')))
    return result


def assert_model_pair(directory: Path, voice_name: str) -> Path:
    if not valid_voice_id(voice_name):
        raise ValueError('Nome da voz inválido')
    voice = catalogue(directory).get(voice_name)
    if voice is None or voice.backend != 'piper' or voice.model is None:
        raise FileNotFoundError(f'Modelo {voice_name} (.onnx + .onnx.json) ausente de {directory}')
    return voice.model

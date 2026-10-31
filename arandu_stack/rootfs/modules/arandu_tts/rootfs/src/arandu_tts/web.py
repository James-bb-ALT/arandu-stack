"""Laboratório Web: comparação Piper/Pocket, métricas e importação de voz consentida."""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from aiohttp import web

from .config import Config
from .engine import SpeechEngine
from .text import normalize_text
from .voices import VoiceSpec, catalogue, valid_voice_id

LOG = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent / 'static' / 'index.html'


def create_app(cfg: Config, engine: SpeechEngine) -> web.Application:
    @web.middleware
    async def guard(request: web.Request, handler):
        if cfg.web_ingress_only and request.remote != '172.30.32.2':
            raise web.HTTPForbidden(text='Acesso exclusivamente pelo Home Assistant Ingress')
        if request.path.startswith('/api/'):
            if cfg.web_token and not hmac.compare_digest(request.headers.get('X-Arandu-Token', ''), cfg.web_token):
                raise web.HTTPUnauthorized(text='Token da interface web inválido')
            if request.method != 'GET' and request.headers.get('X-Arandu-UI') != '1':
                raise web.HTTPForbidden(text='Cabeçalho de proteção ausente')
        return await handler(request)

    app = web.Application(middlewares=[guard], client_max_size=(cfg.max_upload_mb + 1) * 1024 * 1024)
    upload_lock = asyncio.Lock()

    async def index(request):
        return web.FileResponse(HERE, headers={'Cache-Control': 'no-store'})

    def available() -> dict[str, VoiceSpec]:
        return catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled)

    async def status(request):
        voices = available()
        return web.json_response({
            'version': '0.2.1',
            'voices': [dict(id=v.name, label=v.description or v.name, engine=v.backend, language=v.language)
                       for v in voices.values()],
            'piper_available': any(v.backend == 'piper' for v in voices.values()),
            'pocket_enabled': cfg.pocket_enabled,
            'pocket_loaded': engine.pocket_loaded,
            'default_voice': cfg.default_voice,
            'max_upload_mb': cfg.max_upload_mb,
            'max_chars': cfg.max_chars,
            'default_pocket_voice': 'pocket-rafael',
        }, headers={'Cache-Control': 'no-store'})

    async def warmup(request):
        if not cfg.pocket_enabled:
            raise web.HTTPConflict(text='Pocket desativado')
        started = time.perf_counter()
        try:
            await asyncio.to_thread(engine.warm_pocket)
        except Exception as exc:
            LOG.exception('Pocket falhou no warm-up')
            raise web.HTTPServiceUnavailable(text=f'Não foi possível carregar Pocket: {exc}') from exc
        return web.json_response({'loaded': True, 'elapsed_ms': round(1000 * (time.perf_counter() - started))})

    async def synthesize(request):
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text='JSON inválido')
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text='Corpo inválido')
        text = normalize_text(str(payload.get('text', ''))).strip()
        if not text or len(text) > cfg.max_chars:
            raise web.HTTPBadRequest(text=f'Texto deve ter entre 1 e {cfg.max_chars} caracteres')
        backend = payload.get('engine')
        if backend not in {'piper', 'pocket'}:
            raise web.HTTPBadRequest(text='Escolha piper ou pocket')
        voices = available()
        requested = payload.get('voice')
        if requested:
            if not isinstance(requested, str) or requested not in voices:
                raise web.HTTPBadRequest(text='Voz inexistente')
            voice = voices[requested]
        else:
            candidates = [v for v in voices.values() if v.backend == backend]
            voice = candidates[0] if candidates else None
        if voice is None or voice.backend != backend:
            raise web.HTTPBadRequest(text='Voz incompatível com motor ou modelo ausente')
        began = time.perf_counter()
        first_ms = None
        pcm = bytearray()
        rate = None
        try:
            async for chunk in engine.stream(text, voice):
                if rate is None:
                    rate = chunk.rate
                if chunk.rate != rate or chunk.width != 2 or chunk.channels != 1:
                    raise ValueError('Motor precisa produzir PCM16 mono na mesma taxa')
                if first_ms is None:
                    first_ms = (time.perf_counter() - began) * 1000
                pcm.extend(chunk.data)
                if len(pcm) > 32 * 1024 * 1024:
                    raise ValueError('Áudio excedeu 32 MiB')
        except Exception as exc:
            LOG.exception('Falha de síntese no web app (%s)', backend)
            raise web.HTTPServiceUnavailable(text=f'Síntese {backend} falhou: {exc}') from exc
        if not pcm or rate is None:
            raise web.HTTPServiceUnavailable(text='Motor não retornou áudio')
        elapsed_ms = (time.perf_counter() - began) * 1000
        audio_seconds = len(pcm) / (rate * 2)
        with io.BytesIO() as buffer:
            with wave.open(buffer, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(pcm)
            wav_bytes = buffer.getvalue()
        return web.Response(body=wav_bytes, content_type='audio/wav', headers={
            'X-Arandu-Ttfb-Ms': f'{first_ms:.1f}',
            'X-Arandu-Total-Ms': f'{elapsed_ms:.1f}',
            'X-Arandu-Audio-Seconds': f'{audio_seconds:.2f}',
            'X-Arandu-Rtf': f'{elapsed_ms / 1000 / audio_seconds:.3f}',
            'X-Arandu-Voice': voice.name,
            'Cache-Control': 'no-store',
        })

    async def upload_voice(request):
        if not cfg.pocket_enabled:
            raise web.HTTPConflict(text='Pocket desativado')
        reader = await request.multipart()
        slug = None
        accepted = False
        data = None
        async with upload_lock:
            pocket_dir = cfg.voices_dir / 'pocket'
            pocket_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='upload-', dir=pocket_dir) as workdir:
                work = Path(workdir)
                input_file = work / 'clip.bin'
                total = 0
                while part := await reader.next():
                    if part.name == 'name':
                        slug = (await part.text()).strip().lower()
                    elif part.name == 'consent':
                        accepted = (await part.text()).strip() == 'true'
                    elif part.name == 'audio':
                        if data is not None:
                            raise web.HTTPBadRequest(text='Envie um único áudio')
                        data = True
                        with input_file.open('wb') as out:
                            while chunk := await part.read_chunk(64 * 1024):
                                total += len(chunk)
                                if total > cfg.max_upload_mb * 1024 * 1024:
                                    raise web.HTTPRequestEntityTooLarge(
                                        max_size=cfg.max_upload_mb * 1024 * 1024, actual_size=total)
                                out.write(chunk)
                if not isinstance(slug, str) or len(slug) > 72 or not valid_voice_id(slug) or slug == 'rafael':
                    raise web.HTTPBadRequest(text='Nome: 1–72 letras, números, _ ou -; não use rafael')
                if not accepted:
                    raise web.HTTPBadRequest(text='Confirme que você tem autorização para clonar essa voz')
                if not data or total < 1024:
                    raise web.HTTPBadRequest(text='Áudio ausente ou pequeno demais')
                wav = work / 'clip.wav'
                try:
                    await asyncio.to_thread(convert_reference, input_file, wav, cfg.max_audio_seconds)
                    target = pocket_dir / (slug + '.safetensors')
                    await asyncio.to_thread(engine.prepare_pocket_voice, wav, target)
                    os.replace(wav, pocket_dir / (slug + '.wav'))
                except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
                    LOG.exception('Falha ao preparar voz Pocket')
                    raise web.HTTPUnprocessableEntity(text=f'Não foi possível processar a amostra: {exc}') from exc
        return web.json_response({'voice': f'pocket-{slug}', 'message': 'Perfil salvo e pronto para síntese'})

    async def remove_voice(request):
        slug = request.match_info['slug']
        if not valid_voice_id(slug) or slug == 'rafael':
            raise web.HTTPBadRequest(text='Voz inválida')
        base = cfg.voices_dir / 'pocket'
        profile = base / (slug + '.safetensors')
        if not profile.exists() or profile.is_symlink():
            raise web.HTTPNotFound(text='Perfil não encontrado')
        async with upload_lock:
            if hasattr(engine, 'drop_pocket_voice'):
                await asyncio.to_thread(engine.drop_pocket_voice, f'pocket-{slug}')
            profile.unlink(missing_ok=True)
            sample = base / (slug + '.wav')
            if sample.is_file() and not sample.is_symlink():
                sample.unlink()
        return web.json_response({'removed': f'pocket-{slug}'})

    app.router.add_get('/', index)
    app.router.add_get('/api/status', status)
    app.router.add_post('/api/warmup', warmup)
    app.router.add_post('/api/synthesize', synthesize)
    app.router.add_post('/api/voices', upload_voice)
    app.router.add_delete('/api/voices/{slug}', remove_voice)
    return app


def convert_reference(source: Path, target: Path, max_seconds: int) -> None:
    """Normaliza áudio browser/arquivo em mono PCM16/24k; nunca usa shell."""
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
           '-vn', '-ac', '1', '-ar', '24000', '-t', str(max_seconds), '-acodec', 'pcm_s16le', str(target)]
    subprocess.run(cmd, check=True, timeout=90, capture_output=True)
    with wave.open(str(target), 'rb') as wav:
        seconds = wav.getnframes() / wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or not 1.0 <= seconds <= max_seconds:
            raise ValueError('Gravação deve ter entre 1 segundo e o limite configurado')


async def start_web(cfg: Config, engine: SpeechEngine) -> web.AppRunner:
    app = create_app(cfg, engine)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host=cfg.web_host, port=cfg.web_port).start()
    except Exception:
        await runner.cleanup()
        raise
    LOG.info('Web app Arandu TTS iniciado em %s:%s', cfg.web_host, cfg.web_port)
    return runner

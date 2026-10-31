"""Arandu control plane. Supervisor credentials never reach inference processes."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import unicodedata
from aiohttp import ClientSession, ClientTimeout, ClientError, web
from modules import ModuleManager
from wyoming_gateway import WyomingGateway
from discovery import announce_stack
from logs import LogStore, SOURCES, LOGGER

APP_VERSION = '1.0.1'

async def read_limited(content, limit):
    """Read to EOF: StreamReader.read(n) may return only the first network chunk."""
    raw = bytearray()
    async for chunk in content.iter_chunked(8192):
        raw.extend(chunk)
        if len(raw) > limit:
            raise ValueError('Resposta do módulo excedeu o limite de bytes')
    return bytes(raw)

MODULES = {
    'wake': ('arandu_wake', 'Wake', 'Palavra de ativação · Wyoming'),
    'stt': ('arandu_stt', 'STT', 'Áudio para texto · FastConformer'),
    'nlu': ('ptbr_nlu', 'NLU', 'Comandos e validação · Rust'),
    'decision': ('arandu_decision', 'Decision', 'Multilingual E5 Small'),
    'llm': ('arandu_llm', 'LLM', 'Tucano 2 Qwen 0.5B'),
    'tts': ('arandu_tts', 'TTS', 'Texto para áudio · Piper / Pocket'),
}

def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())

def validate_phrases(value):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError('Máximo de 200 frases')
    seen, result = set(), []
    for row in value:
        if not isinstance(row, dict) or set(row) != {'phrase', 'command', 'enabled'} or type(row['enabled']) is not bool:
            raise ValueError('Frase exige phrase, command e enabled')
        for key in ('phrase', 'command'):
            if not isinstance(row[key], str) or not 1 <= len(row[key].strip()) <= 512 or any(ord(c) < 32 for c in row[key]):
                raise ValueError('Use textos entre 1 e 512 caracteres, sem quebras de linha')
        key = normalize(row['phrase'])
        if key in seen:
            raise ValueError('Frases duplicadas após normalização')
        seen.add(key)
        result.append({**row, 'phrase': row['phrase'].strip(), 'command': row['command'].strip()})
    return result

def redact(value):
    return {k: v for k, v in value.items()
            if (k == 'max_tokens' and type(v) is int)
            or not any(term in k.lower() for term in ('token', 'secret', 'password'))}

def create_app(data_dir, local=False):
    data_dir.mkdir(parents=True, exist_ok=True)
    reference = [None]
    journal = LogStore(data_dir, lambda: reference[0].config if reference[0] else {})
    journal.configure_logging()
    try:
        manager = ModuleManager(data_dir, local, journal=journal)
    except Exception:
        LOGGER.exception('Falha ao carregar as configurações do Arandu Assistant')
        raise
    reference[0] = manager
    phrases_file = data_dir / 'phrases.json'
    csrf = secrets.token_urlsafe(32)
    lock = asyncio.Lock()
    def phrases():
        return validate_phrases(json.loads(phrases_file.read_text(encoding='utf-8'))) if phrases_file.exists() else []

    @web.middleware
    async def boundary(request, handler):
        # Only the Supervisor ingress proxy reaches UI; runtime is internal read-only.
        peer = request.remote
        if os.environ.get('ARANDU_LOCAL_CONTAINER') == '1':
            import ipaddress
            if request.host not in {'127.0.0.1:8770', 'localhost:8770'} or not peer or not ipaddress.ip_address(peer).is_private:
                raise web.HTTPForbidden()
        elif local:
            if peer not in {'127.0.0.1', '::1'} or request.host not in {'127.0.0.1:8770', 'localhost:8770'}:
                raise web.HTTPForbidden()
        elif request.path not in {'/runtime', '/status', '/infer', '/synthesize'} and peer != '172.30.32.2':
            raise web.HTTPForbidden()
        elif request.path in {'/runtime', '/status', '/infer', '/synthesize'}:
            import ipaddress
            if not peer or ipaddress.ip_address(peer) not in ipaddress.ip_network('172.30.32.0/23'):
                raise web.HTTPForbidden()
        if request.path in {'/infer', '/synthesize'} and (request.headers.get('Origin') or request.headers.get('X-Arandu-Lab') != '1'):
            raise web.HTTPForbidden()
        if request.method != 'GET' and request.path not in {'/infer', '/synthesize'} and request.headers.get('X-Arandu-CSRF') != csrf:
            raise web.HTTPForbidden()
        try:
            response = await handler(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
            return response
        except (ValueError, KeyError, TypeError) as exc:
            LOGGER.warning('Solicitação recusada (%s): %s', request.path, str(exc))
            return web.json_response({'error': str(exc)[:240]}, status=422)
        except (ClientError, TimeoutError):
            LOGGER.exception('Serviço indisponível (%s)', request.path)
            return web.json_response({'error': 'Serviço indisponível ou tempo limite excedido'}, status=503)
        except OSError:
            LOGGER.exception('Falha de arquivo ou processo (%s)', request.path)
            return web.json_response({'error': 'Falha de arquivo ou processo; consulte a página Logs'}, status=503)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    async def index(request):
        return web.FileResponse(Path(__file__).parent / 'static/index.html')
    async def state(request):
        modules = []
        for key in MODULES:
            row = await manager.status(key)
            row.update(name=MODULES[key][1], description=MODULES[key][2], options=redact(row['options']))
            modules.append(row)
        return web.json_response({'modules': modules, 'phrases': phrases(), 'csrf': csrf, 'local': local, 'version': APP_VERSION})
    async def logs(request):
        selected = request.query.get('source', 'all')
        if selected != 'all' and selected not in SOURCES:
            raise ValueError('Fonte de log desconhecida')
        lines = int(request.query.get('lines', '300'))
        result = []
        for source in SOURCES if selected == 'all' else (selected,):
            try:
                row = await asyncio.to_thread(journal.tail, source, lines)
            except (OSError, ValueError) as exc:
                if lines not in (100, 300, 1000):
                    raise ValueError('Escolha 100, 300 ou 1000 linhas') from exc
                row = {'id': source, 'exists': False, 'text': '', 'modified': None, 'truncated': False,
                       'error': 'Não foi possível ler o log: ' + journal.redact(str(exc))}
            row['name'] = 'Aplicativo' if source == 'app' else MODULES[source][1]
            if source != 'app':
                status = await manager.status(source)
                row.update(state=status['state'], exit_code=status['exit_code'], last_error=status['last_error'])
            result.append(row)
        return web.json_response({'sources': result, 'lines': lines, 'version': APP_VERSION})
    async def control(request):
        key, action = request.match_info['key'], request.match_info['action']
        if action not in {'start', 'stop', 'restart', 'options'}:
            raise ValueError('Operação inválida')
        async with lock:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('Objeto JSON obrigatório')
            if action == 'options':
                options = body.get('options')
                if not isinstance(options, dict):
                    raise ValueError('Opções devem ser objeto JSON')
                await manager.configure(key, options)
            elif action == 'start':
                await manager.start(key)
            elif action == 'stop':
                await manager.stop(key)
            else:
                await manager.stop(key, persist=False)
                await manager.start(key)
            return web.json_response({'saved': True, 'restart_required': action == 'options'})
    async def save_phrases(request):
        rows = validate_phrases((await request.json()).get('phrases'))
        async with lock:
            temporary = phrases_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(phrases_file)
        return web.json_response({'saved': True, 'count': len(rows)})
    async def runtime(request):
        nlu = await manager.status('nlu')
        return web.json_response({'nlu_enabled': nlu['state'] == 'started', 'nlu_options': {k:v for k,v in manager.config['nlu'].items() if k != 'enabled'}, 'phrases': phrases()})
    async def model_request(key, path, payload=None):
        if key not in {'decision', 'llm'}:
            raise ValueError('Módulo de inferência inválido')
        port = 11557 if key == 'decision' else 11558
        url = 'http://127.0.0.1:' + str(port) + path
        async with ClientSession(timeout=ClientTimeout(total=185), trust_env=False) as session:
            async with session.request('GET' if payload is None else 'POST', url, json=payload,
                    headers={'X-Arandu-Lab': '1'}, allow_redirects=False) as response:
                raw = await read_limited(response.content, 2_097_152)
                if response.status != 200:
                    # Preserve HTTP failure classification without forwarding raw model output.
                    phase = 'unknown'
                    code = 'upstream_http_' + str(response.status)
                    try:
                        diagnostic = json.loads(raw)
                        if diagnostic.get('phase') in {'loading', 'retrieval', 'inference', 'validation'}:
                            phase = diagnostic['phase']
                        if key == 'llm' and diagnostic.get('code') in {'llm_output_truncated', 'llm_invalid_json', 'llm_response_invalid', 'llm_response_incomplete'}:
                            code = diagnostic['code']
                    except (ValueError, AttributeError):
                        pass
                    raise web.HTTPBadGateway(text=json.dumps({'stage': key, 'code': code,
                        'phase': phase, 'message': 'Módulo recusou a solicitação; consulte os logs do módulo.'}), content_type='application/json')
                return json.loads(raw)
    async def synthesize(request):
        body = await request.json()
        if (type(body) is not dict or type(body.get('text')) is not str or not 1 <= len(body['text']) <= 512
                or body.get('engine') not in {'piper', 'pocket'}):
            raise ValueError('Texto ou motor TTS inválido')
        async with ClientSession(timeout=ClientTimeout(total=60), trust_env=False) as session:
            async with session.post('http://127.0.0.1:11559/api/synthesize',
                    json={'text': body['text'], 'engine': body['engine']}, headers={'X-Arandu-UI': '1'}, allow_redirects=False) as response:
                raw = await read_limited(response.content, 4_194_304)
                if response.status != 200:
                    raise web.HTTPBadGateway(text=json.dumps({'stage': 'tts', 'code': 'upstream_http_' + str(response.status),
                        'phase': 'inference', 'message': 'TTS recusou a síntese; consulte os logs do módulo.'}), content_type='application/json')
                if len(raw) > 4_194_304 or not raw.startswith(b'RIFF'):
                    raise ValueError('TTS não retornou WAV válido dentro do limite')
                return web.Response(body=raw, content_type='audio/wav',
                    headers={'X-Arandu-Ttfb-Ms': response.headers.get('X-Arandu-Ttfb-Ms', '')})

    async def model_status(request):
        async def one(key, model):
            try:
                result = await model_request(key, '/status')
                return result['models'][0]
            except Exception:
                return {'id': model, 'name': MODULES[key][1], 'kind': key, 'installed': None,
                        'available': False, 'reason': 'Módulo ausente, parado ou inacessível'}
        return web.json_response({'models': await asyncio.gather(one('decision', 'e5-small'), one('llm', 'tucano'))})
    async def model_infer(request):
        payload = await request.json()
        action = payload.get('action', 'decision')
        if action == 'install':
            raise ValueError('Ative auto_download nas opções do módulo e reinicie para baixar os pesos')
        if action == 'unload':
            results = await asyncio.gather(*(model_request(k, '/infer', {'action': 'unload'}) for k in ('decision', 'llm')), return_exceptions=True)
            return web.json_response({'unloaded': [k for k, r in zip(('decision', 'llm'), results) if not isinstance(r, Exception)]})
        if action == 'llm':
            if payload.get('llm_model', 'tucano') != 'tucano':
                raise ValueError('Este módulo LLM usa Tucano; outros modelos pertencem ao Lab experimental no PC')
            key = 'llm'
        else:
            if payload.get('model', 'e5-small') != 'e5-small':
                raise ValueError('Este módulo Decision usa E5 Small; outros modelos pertencem ao Lab experimental no PC')
            key = 'decision'
        return web.json_response(await model_request(key, '/infer', payload))
    async def startup(app):
        journal.event('app', 'Arandu Assistant ' + APP_VERSION + ' iniciando.')
        app['wyoming_gateway'] = WyomingGateway(manager, journal)
        if not local:
            await app['wyoming_gateway'].start()
        await manager.startup()
        if not local:
            app['wyoming_discovery'] = asyncio.create_task(announce_stack(app['wyoming_gateway'], journal))
        journal.event('app', 'Painel e página de logs prontos.')
    async def cleanup(app):
        journal.event('app', 'Encerrando Arandu Assistant.')
        discovery = app.get('wyoming_discovery')
        if discovery:
            discovery.cancel()
            await asyncio.gather(discovery, return_exceptions=True)
        await app['wyoming_gateway'].close()
        await manager.close()
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_get('/', index)
    app.router.add_get('/api/state', state)
    app.router.add_get('/api/logs', logs)
    app.router.add_post('/api/modules/{key}/{action}', control)
    app.router.add_post('/api/phrases', save_phrases)
    app.router.add_get('/runtime', runtime)
    app.router.add_get('/status', model_status)
    app.router.add_post('/infer', model_infer)
    app.router.add_post('/synthesize', synthesize)
    app.router.add_static('/static', Path(__file__).parent / 'static')
    return app

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--data', type=Path, default=Path('/data'))
    args = parser.parse_args()
    web.run_app(create_app(args.data, args.local), host='127.0.0.1' if args.local else '0.0.0.0', port=8770 if args.local else 8099, access_log=None)

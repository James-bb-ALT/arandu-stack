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

class Supervisor:
    def __init__(self):
        self.token = os.environ.get('SUPERVISOR_TOKEN')
        self.prefix = None

    async def call(self, path, body=None):
        if not self.token:
            raise ValueError('Supervisor indisponível neste PC; controle de módulos requer HA OS/Supervised')
        async with ClientSession(timeout=ClientTimeout(total=120), trust_env=False) as session:
            async with session.request('POST' if body is not None else 'GET', 'http://supervisor' + path,
                    json=body, headers={'Authorization': 'Bearer ' + self.token}) as response:
                data = await response.json()
                if response.status != 200 or data.get('result') != 'ok':
                    raise ValueError('Supervisor recusou a operação; confira se o módulo está instalado')
                return data.get('data', {})

    async def slug(self, key):
        if key not in MODULES:
            raise ValueError('Módulo desconhecido')
        if self.prefix is None:
            info = await self.call('/addons/self/info')
            slug = info['slug']
            if not slug.endswith('_arandu_stack'):
                raise ValueError('Identidade do painel incompatível')
            self.prefix = slug[:-len('arandu_stack')]
        return self.prefix + MODULES[key][0]

    async def status(self, key):
        slug = await self.slug(key)
        try:
            info = await self.call('/addons/' + slug + '/info')
            return {'id': key, 'name': MODULES[key][1], 'description': MODULES[key][2], 'slug': slug,
                    'installed': True, 'state': info.get('state'), 'version': info.get('version'),
                    'options': redact(info.get('options', {})), 'schema': info.get('schema', {})}
        except ValueError:
            return {'id': key, 'name': MODULES[key][1], 'description': MODULES[key][2], 'slug': slug,
                    'installed': False, 'state': 'not_installed', 'options': {}, 'schema': {}}

def redact(value):
    return {k: v for k, v in value.items() if not any(term in k.lower() for term in ('token', 'secret', 'password'))}

def create_app(data_dir, local=False):
    supervisor = Supervisor()
    data_dir.mkdir(parents=True, exist_ok=True)
    phrases_file = data_dir / 'phrases.json'
    csrf = secrets.token_urlsafe(32)
    lock = asyncio.Lock()
    def phrases():
        return validate_phrases(json.loads(phrases_file.read_text(encoding='utf-8'))) if phrases_file.exists() else []

    @web.middleware
    async def boundary(request, handler):
        # Only the Supervisor ingress proxy reaches UI; runtime is internal read-only.
        peer = request.remote
        if local:
            if peer not in {'127.0.0.1', '::1'} or request.host not in {'127.0.0.1:8770', 'localhost:8770'}:
                raise web.HTTPForbidden()
        elif request.path not in {'/runtime', '/status', '/infer'} and peer != '172.30.32.2':
            raise web.HTTPForbidden()
        elif request.path in {'/runtime', '/status', '/infer'}:
            import ipaddress
            if not peer or ipaddress.ip_address(peer) not in ipaddress.ip_network('172.30.32.0/23'):
                raise web.HTTPForbidden()
        if request.path == '/infer' and (request.headers.get('Origin') or request.headers.get('X-Arandu-Lab') != '1'):
            raise web.HTTPForbidden()
        if request.method != 'GET' and request.path != '/infer' and request.headers.get('X-Arandu-CSRF') != csrf:
            raise web.HTTPForbidden()
        try:
            response = await handler(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
            return response
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)[:240]}, status=422)
        except (ClientError, TimeoutError):
            return web.json_response({'error': 'Serviço indisponível ou tempo limite excedido'}, status=503)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    async def index(request):
        return web.FileResponse(Path(__file__).parent / 'static/index.html')
    async def state(request):
        if local:
            modules = [{'id': k, 'name': v[1], 'description': v[2], 'installed': False,
                        'state': 'supervisor_required', 'options': {}, 'schema': {}} for k, v in MODULES.items()]
        else:
            modules = await asyncio.gather(*(supervisor.status(k) for k in MODULES))
        return web.json_response({'modules': modules, 'phrases': phrases(), 'csrf': csrf, 'local': local})
    async def control(request):
        key, action = request.match_info['key'], request.match_info['action']
        if action not in {'start', 'stop', 'restart', 'options'}:
            raise ValueError('Operação inválida')
        async with lock:
            slug = await supervisor.slug(key)
            body = await request.json()
            if action == 'options':
                options = body.get('options')
                if not isinstance(options, dict):
                    raise ValueError('Opções devem ser objeto JSON')
                info = await supervisor.call('/addons/' + slug + '/info')
                unknown = set(options) - set(info.get('options', {}))
                if unknown:
                    raise ValueError('Opção desconhecida: ' + ', '.join(sorted(unknown)))
                await supervisor.call('/addons/' + slug + '/options', {'options': {**info.get('options', {}), **options}})
            else:
                await supervisor.call('/addons/' + slug + '/' + action, {})
                # Persist the choice across host restarts, not only the current process.
                if action in {'start', 'stop'}:
                    await supervisor.call('/addons/' + slug + '/options', {'boot': 'auto' if action == 'start' else 'manual'})
            return web.json_response({'saved': True, 'restart_required': action == 'options'})
    async def save_phrases(request):
        rows = validate_phrases((await request.json()).get('phrases'))
        async with lock:
            temporary = phrases_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(phrases_file)
        return web.json_response({'saved': True, 'count': len(rows)})
    async def runtime(request):
        nlu = await supervisor.status('nlu')
        return web.json_response({'nlu_enabled': nlu['state'] == 'started', 'phrases': phrases()})
    async def model_request(key, path, payload=None):
        slug = await supervisor.slug(key)
        port = 11557 if key == 'decision' else 11558
        url = 'http://' + slug.replace('_', '-') + ':' + str(port) + path
        async with ClientSession(timeout=ClientTimeout(total=185), trust_env=False) as session:
            async with session.request('GET' if payload is None else 'POST', url, json=payload,
                    headers={'X-Arandu-Lab': '1'}, allow_redirects=False) as response:
                raw = await response.content.read(2_097_153)
                if len(raw) > 2_097_152 or response.status != 200:
                    raise ValueError('Módulo recusou a solicitação ou está indisponível')
                return json.loads(raw)
    async def model_status(request):
        async def one(key, model):
            try:
                result = await model_request(key, '/status')
                return result['models'][0]
            except Exception:
                return {'id': model, 'name': MODULES[key][1], 'kind': key, 'installed': False,
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
    app.router.add_get('/', index)
    app.router.add_get('/api/state', state)
    app.router.add_post('/api/modules/{key}/{action}', control)
    app.router.add_post('/api/phrases', save_phrases)
    app.router.add_get('/runtime', runtime)
    app.router.add_get('/status', model_status)
    app.router.add_post('/infer', model_infer)
    app.router.add_static('/static', Path(__file__).parent / 'static')
    return app

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--data', type=Path, default=Path('/data'))
    args = parser.parse_args()
    web.run_app(create_app(args.data, args.local), host='127.0.0.1' if args.local else '0.0.0.0', port=8770 if args.local else 8099, access_log=None)

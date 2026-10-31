"""One app, isolated module processes. Never passes HA credentials to children."""
from __future__ import annotations
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import math
from urllib.parse import urlsplit
from settings import FIELDS, VOICE_ID, schema
from logs import LogStore

DEFAULTS = {
    'nlu': {'enabled': True, 'custom_phrases_enabled': True, 'max_input_chars': 512, 'session_ttl': 60, 'low_battery_threshold': 20},
    'wake': {'enabled': False, 'wake_word': 'alexa', 'threshold': .5, 'trigger_level': 1, 'refractory_seconds': 2.0},
    'stt': {'enabled': False, 'auto_download': False, 'model_variant': 'adapted', 'threads': 2,
            'padding_ms': 0, 'profile': 'full', 'debug_text': False, 'refresh_inventory': False,
            'lexical_enabled': True, 'closed_vocabulary': True, 'generic_weight': 7,
            'home_assistant_weight': 10, 'inventory_refresh_seconds': 0},
    'decision': {'enabled': False, 'auto_download': False, 'threads': 2, 'threshold': .8, 'min_margin': .08},
    'llm': {'enabled': False, 'auto_download': False, 'threads': 2, 'context_size': 4096, 'max_tokens': 192, 'temperature': 0.0},
    'tts': {'enabled': False, 'voice': 'pt_BR-arandu-medium', 'auto_download': False, 'max_chars': 3000,
            'preload': True, 'cpu_threads': 2, 'pocket_enabled': False, 'pocket_preload': False,
            'pocket_threads': 2, 'pocket_fallback': False, 'fallback_voice': 'pt_BR-faber-medium',
            'remote_fallback': False, 'remote_url': '', 'remote_token': '', 'hf_token': '', 'log_level': 'INFO'},
}
PORTS = {'nlu': 11555, 'wake': 10400, 'stt': 10350, 'tts': 10212, 'decision': 11557, 'llm': 11558}

def validate_options(key, options, data=Path('/data')):
    if key not in DEFAULTS or not isinstance(options, dict) or set(options) - set(DEFAULTS[key]):
        raise ValueError('Módulo ou opção desconhecida')
    for name, value in options.items():
        expected = DEFAULTS[key][name]
        if isinstance(expected, bool):
            valid = type(value) is bool
        elif isinstance(expected, int):
            valid = type(value) is int and 0 <= value <= 86400
        elif isinstance(expected, float):
            valid = type(value) in (float, int) and math.isfinite(value)
        else:
            valid = isinstance(value, str) and len(value) <= 1024 and not any(ord(c) < 32 for c in value)
        if not valid:
            raise ValueError('Valor inválido: ' + name)
        if name in {'threads', 'cpu_threads', 'pocket_threads'} and not 1 <= value <= 4:
            raise ValueError('Threads deve ficar entre 1 e 4')
        if name == 'trigger_level' and not 1 <= value <= 20:
            raise ValueError('Trigger level deve ficar entre 1 e 20')
    merged = {**DEFAULTS[key], **options}
    for row in schema(key, merged, data):
        value = merged[row['id']]
        if row['type'] == 'number' and (value < row['min'] or value > row['max']):
            raise ValueError(row['label'] + ': valor fora do intervalo')
        if row['type'] == 'select' and row['id'] not in {'voice', 'fallback_voice'} and value not in {choice[0] for choice in row['choices']}:
            raise ValueError(row['label'] + ': opção inválida')
    if key == 'tts':
        if any(not VOICE_ID.fullmatch(merged[name]) for name in ('voice', 'fallback_voice')):
            raise ValueError('Identificador de voz inválido')
        if merged['fallback_voice'].startswith('pocket-'):
            raise ValueError('A voz de reserva deve usar Piper')
        if merged['voice'].startswith('pocket-') and not merged['pocket_enabled']:
            raise ValueError('Habilite Pocket TTS para selecionar uma voz Pocket')
        if merged['remote_url']:
            address = urlsplit(merged['remote_url'])
            if address.scheme not in {'http', 'https'} or not address.hostname or address.username or address.password:
                raise ValueError('Use um endereço HTTP/HTTPS; informe credenciais no campo de token')
        if merged['remote_fallback'] and not merged['remote_url']:
            raise ValueError('Informe o endereço do servidor remoto para habilitar esse fallback')
    if key == 'stt' and (merged['model_variant'] not in {'adapted', 'original'} or merged['padding_ms'] not in {0,100,200,300} or merged['profile'] not in {'raw','normalize_only','acoustic_only','fuzzy_only','inventory_only','full','legacy_full'}):
        raise ValueError('Perfil STT inválido')
    if key == 'tts' and merged['log_level'] not in {'INFO', 'DEBUG', 'WARNING', 'ERROR'}:
        raise ValueError('Log level inválido')
    return merged

class ModuleManager:
    def __init__(self, data, local=False, journal=None):
        self.data = data
        self.local = local
        self.file = data / 'modules.json'
        self.processes = {}
        self.logs = {}
        self.watchers = {}
        self.config = copy.deepcopy(DEFAULTS)
        self.journal = journal or LogStore(data, lambda: self.config)
        self.stopping = set()
        self.failures = {}
        self.lock = asyncio.Lock()
        if self.file.exists():
            document = json.loads(self.file.read_text(encoding='utf-8'))
            if not isinstance(document, dict):
                raise ValueError('Configuração de módulos inválida')
            migrated = document.get('version') != 2
            stored = document if migrated else document.get('modules')
            if not isinstance(stored, dict) or set(stored) - set(DEFAULTS):
                raise ValueError('Configuração de módulos inválida')
            for key, options in stored.items():
                if migrated and key == 'tts' and options.get('voice') == 'pt_BR-faber-medium':
                    options = {**options, 'voice': 'pt_BR-arandu-medium'}
                self.config[key] = validate_options(key, options, self.data)
            if migrated:
                backup = self.file.with_name('modules-v1-backup.json')
                if not backup.exists():
                    backup.write_bytes(self.file.read_bytes())
                self.save()

    def installed(self, key):
        return Path('/usr/bin/local-nlu').is_file() if key == 'nlu' else Path(f'/opt/venvs/{key}/bin/python').is_file()

    def save(self):
        self.data.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix('.tmp')
        temp.write_text(json.dumps({'version': 2, 'modules': self.config}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(self.file)

    async def status(self, key):
        if key not in DEFAULTS:
            raise ValueError('Módulo desconhecido')
        process = self.processes.get(key)
        running = bool(process and process.returncode is None)
        return {'id': key, 'installed': self.installed(key), 'state': 'started' if running else ('error' if key in self.failures or process and self.config[key]['enabled'] else 'stopped'),
                'enabled': self.config[key]['enabled'], 'options': dict(self.config[key]),
                'last_error': self.journal.redact(self.failures.get(key, '')),
                'port': PORTS[key], 'exit_code': process.returncode if process else None,
                'slug': ('Wyoming conjunto · porta 10212 · recarregue a integração ao ativar um novo módulo' if key in {'stt', 'tts', 'wake'} else 'Módulo interno · porta ' + str(PORTS[key])), 'schema': schema(key, self.config[key], self.data)}

    async def configure(self, key, options):
        if key not in DEFAULTS:
            raise ValueError('Módulo desconhecido')
        # enabled is controlled only by start/stop so persisted state matches lifecycle.
        merged = validate_options(key, {**self.config[key], **options}, self.data)
        if merged['enabled'] != self.config[key]['enabled']:
            raise ValueError('Use Ativar/Desativar para mudar enabled')
        self.config[key] = merged
        self.save()
        self.journal.event(key, 'Configurações salvas; reinicie o módulo para aplicar.')

    async def start(self, key, persist=True):
        if key not in DEFAULTS:
            raise ValueError('Módulo desconhecido')
        if not self.installed(key):
            self.failures[key] = 'Runtime não instalado.'
            self.journal.event(key, 'Runtime não instalado; não foi possível iniciar.', error=True)
            raise ValueError('Runtime não instalado neste ambiente; instale o app Arandu Assistant')
        current = self.processes.get(key)
        if current and current.returncode is None:
            return
        directory = self.data / 'modules' / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'options.json').write_text(json.dumps(self.config[key]), encoding='utf-8')
        environment = {k: v for k, v in os.environ.items() if k not in {'SUPERVISOR_TOKEN','HASSIO_TOKEN','HA_TOKEN','HASS_TOKEN','HF_TOKEN'}}
        environment.update(ARANDU_MODULE_DATA=str(directory), OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
        if key in {'decision', 'llm'}:
            environment.update(ARANDU_INFERENCE_LOCK=str(self.data / 'intelligence.lock'),
                               OMP_NUM_THREADS=str(self.config[key]['threads']),
                               OPENBLAS_NUM_THREADS=str(self.config[key]['threads']),
                               MKL_NUM_THREADS=str(self.config[key]['threads']),
                               OMP_WAIT_POLICY='PASSIVE', KMP_BLOCKTIME='0')
        if key == 'stt' and self.config[key].get('refresh_inventory') and os.environ.get('SUPERVISOR_TOKEN'):
            environment['SUPERVISOR_TOKEN'] = os.environ['SUPERVISOR_TOKEN']
        command = ['/usr/bin/local-nlu', 'serve', '--listen', '0.0.0.0:11555'] if key == 'nlu' else [f'/opt/venvs/{key}/bin/python', '/opt/assistant/module_worker.py', key]
        self.journal.event(key, 'Iniciando módulo.')
        log = self.journal.path(key).open('ab')
        previous_log = self.logs.pop(key, None)
        if previous_log:
            previous_log.close()
        self.logs[key] = log
        try:
            self.processes[key] = await asyncio.create_subprocess_exec(*command, env=environment, stdout=log, stderr=log, start_new_session=True)
        except OSError as exc:
            self.failures[key] = str(exc)
            self.journal.event(key, 'Falha ao iniciar processo: ' + str(exc), error=True)
            raise
        process = self.processes[key]
        self.failures.pop(key, None)
        self.journal.event(key, 'Processo iniciado; PID ' + str(process.pid) + '.')
        async def reap_children():
            code = await process.wait()
            intentional = process.pid in self.stopping
            self.stopping.discard(process.pid)
            if not intentional and self.processes.get(key) is process:
                self.failures[key] = 'Processo encerrado; código ' + str(code) + '.'
            self.journal.event(key, 'Processo encerrado; código ' + str(code) + ('. Parada solicitada.' if intentional else '.'), error=code != 0 and not intentional)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.watchers[key] = asyncio.create_task(reap_children())
        if persist:
            self.config[key]['enabled'] = True
            self.save()

    async def stop(self, key, persist=True):
        if key not in DEFAULTS:
            raise ValueError('Módulo desconhecido')
        process = self.processes.get(key)
        if process and process.returncode is None:
            self.stopping.add(process.pid)
            self.journal.event(key, 'Parada solicitada.')
            # Kill the process group as well: LLM workers may own a llama-server child.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        if persist:
            self.config[key]['enabled'] = False
            self.failures.pop(key, None)
            self.save()

    async def startup(self):
        if not self.local:
            for key, options in self.config.items():
                if options['enabled']:
                    try:
                        await self.start(key, persist=False)
                    except Exception as exc:
                        self.failures[key] = str(exc)
                        self.journal.event(key, 'Inicialização falhou: ' + str(exc), error=True)

    async def close(self):
        for key in DEFAULTS:
            await self.stop(key, persist=False)
        for log in self.logs.values():
            log.close()
        await asyncio.gather(*self.watchers.values(), return_exceptions=True)

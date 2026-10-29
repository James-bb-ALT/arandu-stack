"""Bounded, redacted access to fixed app/module log files."""
from datetime import datetime, timezone
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import time
import traceback

SOURCES = ('app', 'wake', 'stt', 'nlu', 'decision', 'llm', 'tts')
MAX_BYTES = 131072
LOGGER = logging.getLogger('arandu')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')

def secrets_from(config):
    values = {os.environ.get(key, '') for key in ('SUPERVISOR_TOKEN', 'HASSIO_TOKEN', 'HA_TOKEN', 'HASS_TOKEN', 'HF_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN')}
    for options in config.values():
        if isinstance(options, dict):
            values.update(value for key, value in options.items() if isinstance(value, str) and any(term in key.lower() for term in ('token', 'password', 'secret')))
    return sorted((v for v in values if v), key=len, reverse=True)

def sanitize(text, secrets=()):
    text = ANSI.sub('', text)
    for value in secrets:
        if value:
            text = text.replace(value, '[OCULTO]')
    text = re.sub(r'(?i)(authorization[\s\"\x27:=]+(?:bearer\s+)?)[^\s\"\x27,}]+', r'\1[OCULTO]', text)
    text = re.sub(r'(?i)([?&](?:token|access_token|api_key|password|secret)=)[^\s&\"\x27]+', r'\1[OCULTO]', text)
    text = re.sub(r'(?i)(https?://)[^/\s:@]+:[^@\s/]+@', r'\1[OCULTO]@', text)
    text = re.sub(r'\b(?:hf_[A-Za-z0-9]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b', '[OCULTO]', text)
    return ''.join(c for c in text if c in '\n\r\t' or ord(c) >= 32)

class LogStore:
    def __init__(self, data, config_provider=lambda: {}):
        self.data = Path(data)
        self.config_provider = config_provider

    def path(self, source):
        if source not in SOURCES:
            raise ValueError('Fonte de log desconhecida')
        path = self.data / ('logs/assistant.log' if source == 'app' else 'modules/' + source + '/runtime.log')
        if path.is_symlink() or not path.resolve().is_relative_to(self.data.resolve()):
            raise ValueError('Caminho de log inválido')
        return path

    def redact(self, text):
        return sanitize(text, secrets_from(self.config_provider()))

    def tail(self, source, lines=300):
        if lines not in (100, 300, 1000):
            raise ValueError('Escolha 100, 300 ou 1000 linhas')
        path = self.path(source)
        if not path.exists():
            return {'id': source, 'exists': False, 'text': '', 'truncated': False, 'modified': None}
        if not path.is_file():
            raise ValueError('Arquivo de log inválido')
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            start = max(0, size - MAX_BYTES)
            stream.seek(start)
            text = stream.read(MAX_BYTES).decode('utf-8', errors='replace')
        rows = text.splitlines()
        if start and rows:
            rows = rows[1:]
        truncated = bool(start or len(rows) > lines)
        return {'id': source, 'exists': True, 'text': self.redact('\n'.join(rows[-lines:])), 'truncated': truncated,
                'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}

    def event(self, source, message, error=False):
        text = self.redact(message)
        (LOGGER.error if error else LOGGER.info)('[%s] %s', source, text)
        if source == 'app':
            return
        try:
            path = self.path(source)
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            with path.open('a', encoding='utf-8') as stream:
                stream.write(stamp + ' [controlador] ' + text + '\n')
        except (ValueError, OSError):
            LOGGER.exception('Não foi possível registrar evento do módulo %s', source)

    def configure_logging(self):
        store = self
        class Redact(logging.Filter):
            def filter(self, record):
                record.msg = store.redact(record.getMessage())
                record.args = ()
                if record.exc_info:
                    record.exc_text = store.redact(''.join(traceback.format_exception(*record.exc_info)))
                    record.exc_info = None
                return True
        path = self.path('app')
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)sZ %(levelname)s %(name)s: %(message)s', '%Y-%m-%dT%H:%M:%S')
        formatter.converter = time.gmtime
        handlers = [file_handler, logging.StreamHandler()]
        for handler in handlers:
            handler.setFormatter(formatter)
            handler.addFilter(Redact())
        logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)

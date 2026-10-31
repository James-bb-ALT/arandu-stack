"""Strict in-memory telemetry sink; never accepts commands, audio or credentials."""
from collections import OrderedDict
from datetime import datetime, timezone
import copy
import math
import re

STAGES = ('wake', 'stt', 'prepare', 'authorization', 'queue', 'nlu', 'decision', 'llm', 'validation', 'ha', 'tts')
STATUSES = {'running', 'pending', 'not_observed', 'not_reached', 'disabled', 'completed', 'accepted',
            'rejected', 'error', 'confirmation_required', 'clarification_required', 'cancelled', 'uncalibrated', 'ambiguous'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86_400_000


class TraceStore:
    def __init__(self, sanitize=lambda text: text):
        self.rows = OrderedDict()
        self.include_text = False
        self.sanitize = sanitize

    def ingest(self, value):
        fields = {'protocol', 'id', 'sequence', 'started_at', 'status', 'origin', 'result', 'reason', 'total_ms', 'text', 'stages'}
        if type(value) is not dict or set(value) != fields or value['protocol'] != 'arandu-trace-v1':
            raise ValueError('Formato de trace inválido')
        if not isinstance(value['id'], str) or not re.fullmatch('[a-f0-9]{32}', value['id']):
            raise ValueError('Identificador de trace inválido')
        if type(value['sequence']) is not int or not 1 <= value['sequence'] <= 10000:
            raise ValueError('Sequência inválida')
        if value['status'] not in STATUSES or value['origin'] not in ('satellite', 'device', 'unknown'):
            raise ValueError('Estado ou origem inválidos')
        if value['result'] is not None and (not isinstance(value['result'], str) or not re.fullmatch('[a-zA-Z0-9_.-]{1,96}', value['result'])):
            raise ValueError('Código de resultado inválido')
        if value['reason'] is not None and (not isinstance(value['reason'], str) or not re.fullmatch('[a-zA-Z0-9_.-]{1,96}', value['reason'])):
            raise ValueError('Motivo do resultado inválido')
        if value['total_ms'] is not None and not number(value['total_ms']):
            raise ValueError('Duração inválida')
        if not isinstance(value['started_at'], str) or len(value['started_at']) > 40:
            raise ValueError('Data inválida')
        try:
            if datetime.fromisoformat(value['started_at']).tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError) as error:
            raise ValueError('Data inválida') from error
        if value['text'] is not None and (not isinstance(value['text'], str) or len(value['text']) > 512):
            raise ValueError('Texto excedeu limite')
        if type(value['stages']) is not dict or set(value['stages']) != set(STAGES):
            raise ValueError('Etapas inválidas')
        for row in value['stages'].values():
            if type(row) is not dict or not set(row) <= {'status', 'offset_ms', 'duration_ms', 'attempts', 'reason', 'confidence'} or row.get('status') not in STATUSES:
                raise ValueError('Dados de etapa inválidos')
            if any(not number(row[key]) for key in ('offset_ms', 'duration_ms') if key in row):
                raise ValueError('Tempo de etapa inválido')
            if 'attempts' in row and (type(row['attempts']) is not int or not 1 <= row['attempts'] <= 128):
                raise ValueError('Tentativas inválidas')
            if 'confidence' in row and (not number(row['confidence']) or row['confidence'] > 1):
                raise ValueError('Confiança inválida')
            if 'reason' in row and (not isinstance(row['reason'], str) or not re.fullmatch('[a-zA-Z0-9_.-]{1,96}', row['reason'])):
                raise ValueError('Motivo inválido')
        current = self.rows.get(value['id'])
        if current and current['sequence'] >= value['sequence']:
            return False
        record = copy.deepcopy(value)
        record['text'] = self.sanitize(value['text']) if self.include_text and value['text'] else None
        record['received_at'] = datetime.now(timezone.utc).isoformat()
        self.rows[value['id']] = record
        # Keep request creation order stable while live snapshots are updated.
        while len(self.rows) > 50:
            self.rows.popitem(last=False)
        return True

    def configure(self, include_text):
        if type(include_text) is not bool:
            raise ValueError('Escolha se deseja incluir o texto')
        self.include_text = include_text
        if not include_text:
            for row in self.rows.values():
                row['text'] = None

    def view(self):
        return {'protocol': 'arandu-trace-v1', 'include_text': self.include_text,
                'capacity': 50, 'requests': copy.deepcopy(list(reversed(self.rows.values())))}

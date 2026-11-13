"""Strict in-memory telemetry sink; never accepts commands, audio or credentials."""
from collections import OrderedDict
from datetime import datetime, timezone
import copy
import math
import re
import time

LEGACY_STAGES = ('wake', 'stt', 'prepare', 'authorization', 'queue', 'nlu', 'decision', 'llm', 'validation', 'ha', 'tts')
STAGES = (*LEGACY_STAGES, 'normalization', 'model_queue', 'nlu_queue', 'resource_queue', 'playback', 'scheduler')
STATUSES = {'running', 'pending', 'not_observed', 'not_reached', 'disabled', 'completed', 'accepted',
            'rejected', 'error', 'confirmation_required', 'clarification_required', 'cancelled', 'uncalibrated', 'ambiguous'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86_400_000


class TraceStore:
    def __init__(self, sanitize=lambda text: text):
        self.rows = OrderedDict()
        self.include_text = False
        self.sanitize = sanitize
        self.received = {}

    def ingest(self, value):
        fields = {'protocol', 'id', 'sequence', 'started_at', 'status', 'origin', 'result', 'reason', 'total_ms', 'text', 'stages'}
        if type(value) is not dict or not fields <= set(value) <= fields|{'job_ref','job_kind','scheduled_at'} or value['protocol'] != 'arandu-trace-v1':
            raise ValueError('Formato de trace inválido')
        if 'job_ref' in value and (not isinstance(value['job_ref'],str) or not re.fullmatch('[a-f0-9]{32}',value['job_ref'])):
            raise ValueError('Job inválido')
        if 'job_kind' in value and value['job_kind'] not in {'timer','alarm','reminder','action'}:
            raise ValueError('Tipo de job inválido')
        if 'scheduled_at' in value:
            if not isinstance(value['scheduled_at'],str) or len(value['scheduled_at'])>40 or datetime.fromisoformat(value['scheduled_at']).tzinfo is None:
                raise ValueError('Vencimento inválido')
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
        if type(value['stages']) is not dict or not set(LEGACY_STAGES) <= set(value['stages']) <= set(STAGES):
            raise ValueError('Etapas inválidas')
        for row in value['stages'].values():
            if type(row) is not dict or not set(row) <= {'status', 'offset_ms', 'duration_ms', 'attempts', 'reason', 'confidence','observation','model','backend','embedding_used','load_ms','inference_ms','cpu_ms','tokens_per_second'} or row.get('status') not in STATUSES:
                raise ValueError('Dados de etapa inválidos')
            if 'observation' in row and row['observation'] not in {'observed','not_observed','disabled','error','cancelled','unavailable'}:
                raise ValueError('Observação inválida')
            if 'model' in row:
                try:
                    from .model_registry import model_ids
                except ImportError:
                    from model_registry import model_ids
                if row['model'] not in model_ids():
                    raise ValueError('Modelo inválido')
            if 'backend' in row and row['backend'] not in {'cpu','gpu_intel'}:
                raise ValueError('Backend inválido')
            if 'embedding_used' in row and type(row['embedding_used']) is not bool:
                raise ValueError('Uso de embeddings inválido')
            if any(not number(row[key]) for key in ('load_ms','inference_ms','cpu_ms','tokens_per_second') if key in row):
                raise ValueError('Métrica inválida')
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
        self.received[value['id']] = time.monotonic()
        # Keep request creation order stable while live snapshots are updated.
        while len(self.rows) > 50:
            removed, _ = self.rows.popitem(last=False)
            self.received.pop(removed, None)
        return True

    def configure(self, include_text):
        if type(include_text) is not bool:
            raise ValueError('Escolha se deseja incluir o texto')
        self.include_text = include_text
        if not include_text:
            for row in self.rows.values():
                row['text'] = None

    def view(self):
        now = time.monotonic()
        # Monotonic receipt window, bounded retention, no wall-clock latency math.
        for key in list(self.received):
            if key not in self.rows or now-self.received[key] > 300:
                self.received.pop(key, None)
                self.rows.pop(key, None)
        finished = [row for row in self.rows.values() if row['total_ms'] is not None]
        def distribution(values):
            ordered = sorted(values)
            return {f'p{p}':ordered[min(len(ordered)-1,round((len(ordered)-1)*p/100))] if ordered else None for p in (50,95,99)}
        summary = dict(window_seconds=300, retained_limit=50, completed=len(finished),
            errors=sum(row['status']=='error' for row in finished),
            timeouts=sum('timeout' in (row.get('reason') or '').lower() or row.get('reason')=='TimeoutError' for row in finished),
            saturated=sum(row.get('reason')=='queue_saturated' for row in finished),
            integration_ms=distribution([row['total_ms'] for row in finished]),
            stages={name:distribution([row['stages'][name]['duration_ms'] for row in finished if name in row['stages'] and 'duration_ms' in row['stages'][name]]) for name in STAGES},
            audio_to_audio='not_observed')
        return {'protocol': 'arandu-trace-v1', 'include_text': self.include_text,
                'capacity': 50, 'summary':summary, 'requests': copy.deepcopy(list(reversed(self.rows.values())))}

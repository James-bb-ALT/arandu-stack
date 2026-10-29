"""Generate a resumable, explicitly synthetic Jaime voice dataset with VoiceStudio.

Uses the installed VoiceStudio backend and its existing Python environment.
No training manifests or original recordings are changed.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ['APPDATA']) / 'VoiceStudio/runtime/project'
DEFAULT_OUTPUT = ROOT / 'runs/jaime_robotico_500'


class AlreadyRunning(RuntimeError):
    pass


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phrases():
    """500 unique PT-BR commands, evenly distributed across five domains."""
    rooms = ['cozinha', 'sala', 'quarto', 'banheiro', 'varanda', 'garagem',
             'lavanderia', 'escritório', 'corredor', 'jardim']
    groups = []
    groups.append([template.format(room=room) for room in rooms for template in [
        'Arandu, acende a luz da {room}.', 'Apaga a luz da {room}, por favor.',
        'A luz da {room} está ligada?', 'Deixa a luz da {room} mais forte.',
        'Diminui o brilho da luz da {room}.', 'Coloca a luz da {room} em cinquenta por cento.',
        'Muda a luz da {room} para azul.', 'Coloca uma luz quente na {room}.',
        'Liga todas as lâmpadas da {room}.', 'Desliga a iluminação da {room} daqui a cinco minutos.'
    ]])
    groups.append([template.format(room=room) for room in rooms for template in [
        'Qual é a temperatura da {room}?', 'Como está a umidade da {room}?',
        'Liga o ventilador da {room}.', 'Desliga o ventilador da {room}.',
        'Coloca o ar condicionado da {room} em vinte e três graus.',
        'O ar condicionado da {room} está ligado?', 'Aumenta a velocidade do ventilador da {room}.',
        'Diminui a velocidade do ventilador da {room}.', 'Ativa o modo automático na {room}.',
        'Desliga a climatização da {room} em meia hora.'
    ]])
    devices = ['televisão', 'cafeteira', 'impressora', 'máquina de lavar', 'lava louças',
               'tomada da bancada', 'tomada da mesa', 'caixa de som', 'fita de led', 'luminária']
    groups.append([template.format(device=device) for device in devices for template in [
        'Liga a {device}, por favor.', 'Desliga a {device} agora.',
        'A {device} está ligada?', 'Quanto a {device} consumiu hoje?',
        'Programa a {device} para ligar às sete horas.', 'A {device} está disponível?',
        'Me avisa quando a {device} terminar.', 'Desliga a {device} daqui a dez minutos.',
        'Cancela o agendamento da {device}.', 'Verifica o estado da {device}.'
    ]])
    groups.append([template.format(room=room) for room in rooms for template in [
        'A janela da {room} está aberta?', 'A porta da {room} está fechada?',
        'Abre a cortina da {room}.', 'Fecha a cortina da {room}.',
        'Deixa a cortina da {room} pela metade.', 'Tem alguém na {room}?',
        'O sensor da {room} está funcionando?', 'Qual é a bateria do sensor da {room}?',
        'Me avisa se houver movimento na {room}.', 'A câmera da {room} está conectada?'
    ]])
    actions = ['beber água', 'tomar o remédio', 'regar as plantas', 'tirar a roupa da máquina',
               'verificar a impressora', 'desligar o forno', 'fechar a janela', 'carregar o celular',
               'alimentar o cachorro', 'recolher o lixo']
    groups.append([template.format(action=action) for action in actions for template in [
        'Arandu, me lembra de {action} em cinco minutos.',
        'Me lembra de {action} em dez minutos.', 'Cria um lembrete para {action} daqui a meia hora.',
        'Me avisa para {action} às oito horas.', 'Me lembra de {action} amanhã cedo.',
        'Cancela o lembrete de {action}.', 'Adia o lembrete de {action} por quinze minutos.',
        'Existe algum lembrete para {action}?', 'Me lembra de {action} todos os dias.',
        'Marca como concluído o lembrete de {action}.'
    ]])
    # Interleave domains so a small smoke test covers different kinds of speech.
    result = [group[i] for i in range(100) for group in groups]
    assert len(result) == len(set(result)) == 500
    return result


def wav_stats(path):
    with wave.open(str(path), 'rb') as audio:
        channels, rate, width, frames = (audio.getnchannels(), audio.getframerate(),
                                        audio.getsampwidth(), audio.getnframes())
        samples = array.array('h', audio.readframes(frames))
    if width != 2 or channels != 1 or rate != 16000:
        raise ValueError(f'Expected mono PCM16/16 kHz: {path}')
    if sys.byteorder != 'little':
        samples.byteswap()
    duration = frames / rate
    rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
    if not 0.3 <= duration <= 35 or rms < 30:
        raise ValueError(f'Invalid or silent clip: duration={duration:.2f}, rms={rms:.2f}')
    return {'duration': round(duration, 5), 'rms': round(rms, 2),
            'peak': max(abs(value) for value in samples), 'sample_rate': rate,
            'channels': channels, 'sha256': digest(path)}


def convert(source, target, robotic=False):
    temporary = target.with_suffix('.tmp.wav')
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source),
               '-ac', '1', '-ar', '16000']
    if robotic:
        # Ring modulation produces a deliberate robot timbre while retaining a dry component.
        command += ['-af', 'aeval=val(0)*(0.35+0.65*cos(2*PI*85*t)):c=same,alimiter=limit=0.9:level=false']
    command += ['-c:a', 'pcm_s16le', str(temporary)]
    subprocess.run(command, check=True, capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    stats = wav_stats(temporary)
    temporary.replace(target)
    return stats


def prepare_reference(output, metadata):
    target = output / 'reference/jaime.wav'
    manifest = output / 'reference/source.json'
    if manifest.exists() and target.exists():
        info = json.loads(manifest.read_text(encoding='utf-8'))
        if digest(target) != info['sha256']:
            raise ValueError('Reference changed; use another output folder.')
        return info
    rows = [json.loads(line) for line in metadata.read_text(encoding='utf-8').splitlines() if line]
    candidates = []
    for row in rows:
        if not (row.get('speaker', '').startswith('Jaime') and row.get('status') == 'valid'
                and row.get('split') == 'train' and 2 <= row.get('duration', 0) <= 6):
            continue
        path = Path(row['audio_filepath'])
        if path.exists():
            stats = wav_stats(path)
            candidates.append((stats['peak'] >= 32700, -row['duration'], row))
    candidates.sort(key=lambda item: item[:2])
    selected = []
    seconds = 0
    for _, _, row in candidates:
        if seconds + row['duration'] > 18:
            continue
        selected.append(row)
        seconds += row['duration']
        if seconds >= 10:
            break
    if seconds < 6:
        raise ValueError('Not enough valid Jaime training recordings for a reference.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), 'wb') as destination:
        destination.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        for row in selected:
            with wave.open(row['audio_filepath'], 'rb') as source:
                destination.writeframes(source.readframes(source.getnframes()))
            destination.writeframes(b'\0\0' * 2400)
    info = {'text': ' '.join(row['text'] for row in selected), 'sources': selected,
            'sha256': digest(target), 'path': str(target), 'synthetic': False}
    atomic_json(manifest, info)
    return info


class Client:
    def __init__(self, url):
        self.url = url

    def request(self, route, fields=None, audio=None, timeout=30):
        headers = {}
        body = None
        if fields is not None:
            boundary = uuid.uuid4().hex
            parts = []
            for name, value in fields.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))
            if audio:
                parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="ref_audio"; filename="jaime.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                          Path(audio).read_bytes(), b'\r\n']
            parts.append(f'--{boundary}--\r\n'.encode())
            body = b''.join(parts)
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        request = urllib.request.Request(self.url + route, data=body, headers=headers)
        # Local API must never pass through a configured HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'VoiceStudio HTTP {error.code}: {error.read().decode("utf-8", errors="replace")[:1200]}') from error

    def json(self, route, fields=None, audio=None):
        return json.loads(self.request(route, fields, audio)[0])


def backend(client, output, device, port):
    data = output / 'voicestudio_data'
    def ready():
        try:
            client.json('/health')
            info = client.json('/system/info')
            if Path(info['data_dir']).resolve() != data.resolve():
                raise ValueError('Port belongs to a different VoiceStudio instance.')
            return True
        except (urllib.error.URLError, RuntimeError, KeyError, TimeoutError):
            return False
    if ready():
        logging.info('Reusing dedicated VoiceStudio backend on port %s', port)
        return
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    executable = RUNTIME / '.venv/Scripts/python.exe'
    main = RUNTIME / 'backend/main.py'
    if not executable.exists() or not main.exists():
        raise FileNotFoundError('VoiceStudio runtime is missing.')
    data.mkdir(exist_ok=True)
    # Isolated history prevents the 500-clip batch from pruning existing app recordings.
    if not (data / 'prefs.json').exists():
        atomic_json(data / 'prefs.json', {'analytics_enabled': False, 'generation_history_cap': 0})
    env = os.environ.copy()
    for key in ('OMNIVOICE_DESKTOP_CONTAINED', 'OMNIVOICE_DESKTOP_DRAIN_FD', 'OMNIVOICE_API_KEY'):
        env.pop(key, None)
    env.update(OMNIVOICE_DATA_DIR=str(data), OMNIVOICE_PORT=str(port), OMNIVOICE_BIND_HOST='127.0.0.1',
               OMNIVOICE_DEVICE=device, OMNIVOICE_PRELOAD_CAPTURE_ASR='0',
               OMNIVOICE_PRELOAD_WATERMARK='0', OMNIVOICE_CPU_GENERATE_TIMEOUT_S='1800',
               OMNIVOICE_GENERATE_TIMEOUT_S='1800', OMP_NUM_THREADS='8', MKL_NUM_THREADS='8')
    checkpoint = output / 'model_checkpoint'
    if (checkpoint / 'READY').exists():
        env['OMNIVOICE_MODEL'] = str(checkpoint)
    # Read only the model-cache path; never copy or print credentials.
    user_env = Path.home() / '.config/omnivoice/env'
    if user_env.exists():
        for line in user_env.read_text(encoding='utf-8').splitlines():
            if line.startswith('OMNIVOICE_CACHE_DIR='):
                env['OMNIVOICE_CACHE_DIR'] = line.split('=', 1)[1].strip().strip('\"\'')
    bootstrap = Path(__file__).with_name('voicestudio_backend_bootstrap.py')
    with (output / 'backend.stdout.log').open('ab') as stdout, (output / 'backend.stderr.log').open('ab') as stderr:
        process = subprocess.Popen([str(executable), str(bootstrap)], cwd=str(RUNTIME), env=env,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    atomic_json(output / 'backend_process.json', {'pid': process.pid, 'port': port, 'device': device})
    logging.info('Started dedicated VoiceStudio backend: PID %s, device %s', process.pid, device)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('VoiceStudio exited; see backend.stderr.log.')
        if ready():
            return
        time.sleep(2)
    raise TimeoutError('VoiceStudio startup exceeded 300 seconds; see backend logs.')


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for folder in ['audio_robotico', 'audio_limpo', 'records']:
        (output / folder).mkdir(exist_ok=True)
    lock = (output / '.generation.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise AlreadyRunning('Another generation process already owns this output folder.') from error
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(output / 'generation.log', encoding='utf-8'), logging.StreamHandler()])
    items = phrases()
    plan = {'synthetic': True, 'speaker': 'Jaime (clone sintético com efeito robótico)',
            'engine': 'VoiceStudio / k2-fsa OmniVoice', 'num_step': args.steps,
            'effect': 'ring modulation 85 Hz, 35% dry',
            'phrases': [{'id': i, 'text': text, 'seed': 41000 + i} for i, text in enumerate(items, 1)]}
    plan_file = output / 'phrases.json'
    if plan_file.exists() and json.loads(plan_file.read_text(encoding='utf-8')) != plan:
        raise ValueError('Plan changed; choose another output folder.')
    atomic_json(plan_file, plan)
    (output / 'frases.txt').write_text('\n'.join(f'{i:04d}\t{text}' for i, text in enumerate(items, 1)) + '\n', encoding='utf-8')
    reference = prepare_reference(output, args.metadata)
    if args.prepare_only:
        logging.info('Prepared 500 unique phrases and %.1f seconds of reference audio.', wav_stats(Path(reference['path']))['duration'])
        return
    client = Client(f'http://127.0.0.1:{args.port}')
    backend(client, output, args.device, args.port)
    profile_file = output / 'profile.json'
    if profile_file.exists():
        profile = json.loads(profile_file.read_text(encoding='utf-8'))
        if not any(row['id'] == profile['id'] for row in client.json('/profiles')):
            raise ValueError('Saved clone profile missing in dedicated VoiceStudio database.')
    else:
        profile = client.json('/profiles', {'name': 'Jaime - clone sintetico Arandu', 'ref_text': reference['text'],
                                          'language': 'Portuguese', 'kind': 'clone'}, reference['path'])
        atomic_json(profile_file, profile)
    started = time.monotonic()
    execution_device = client.json('/system/info')['device']
    completed = []
    failures = []
    limit = min(args.limit, 500)
    def status(state, current=None, error=None):
        recent = (completed[1:] if len(completed) > 1 else completed)[-20:]
        average = sum(row['generation_seconds'] for row in recent) / max(1, len(recent))
        atomic_json(output / 'status.json', {'state': state, 'pid': os.getpid(), 'total': 500,
                    'target_this_run': limit, 'completed': len(completed), 'failed': failures,
                    'current': current, 'elapsed_seconds': round(time.monotonic() - started, 1),
                    'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'error': error,
                    'estimated_remaining_seconds': round((limit - len(completed)) * average) if completed else None})
    status('running')
    for item in plan['phrases'][:limit]:
        number = item['id']
        stem = f'jaime_sintetico_{number:04d}'
        raw = output / 'audio_limpo' / f'{stem}.wav'
        robot = output / 'audio_robotico' / f'{stem}.wav'
        record_file = output / 'records' / f'{number:04d}.json'
        if record_file.exists():
            saved = json.loads(record_file.read_text(encoding='utf-8'))
            if saved['text'] != item['text'] or digest(raw) != saved['clean']['sha256'] or digest(robot) != saved['robotic']['sha256']:
                raise ValueError(f'Completed clip {number} changed; refusing to silently overwrite.')
            completed.append(saved)
            status('running', number)
            continue
        status('running', number)
        for attempt in range(1, 4):
            try:
                logging.info('[%s/500] attempt %s: %s', number, attempt, item['text'])
                began = time.monotonic()
                audio, headers = client.request('/generate', {'text': item['text'], 'profile_id': profile['id'],
                            'language': 'Portuguese', 'engine': 'omnivoice', 'seed': item['seed'],
                            'num_step': args.steps, 'effect_preset': 'raw', 'speed': '1.0',
                            'denoise': 'false', 'postprocess_output': 'false'}, timeout=1900)
                if headers.get('X-OmniVoice-Dropped-Chunks', '0') != '0' or not audio.startswith(b'RIFF'):
                    raise ValueError('Generation incomplete or response is not WAV.')
                source = output / f'{stem}.source.wav'
                source.write_bytes(audio)
                clean_stats = convert(source, raw)
                robot_stats = convert(raw, robot, robotic=True)
                source.unlink()
                record = {**item, 'synthetic': True, 'speaker': 'jaime_clone_synthetic',
                          'audio_filepath': str(robot), 'clean_filepath': str(raw), 'duration': robot_stats['duration'],
                          'clean': clean_stats, 'robotic': robot_stats, 'voice_profile_id': profile['id'],
                          'reference_sha256': reference['sha256'], 'num_step': args.steps,
                          'execution_device': execution_device,
                          'precision': 'float32' if execution_device == 'cpu' else 'float16',
                          'generation_seconds': round(time.monotonic() - began, 2)}
                atomic_json(record_file, record)
                completed.append(record)
                (output / 'manifest_synthetic.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in completed), encoding='utf-8')
                logging.info('Saved clip %04d: %.2f s audio, %.1f s generation', number, record['duration'], record['generation_seconds'])
                status('running', number)
                break
            except Exception as error:
                logging.exception('Clip %s attempt %s failed', number, attempt)
                if attempt == 3:
                    failures.append({'id': number, 'error': str(error)})
                    status('running', number, str(error))
                else:
                    time.sleep(10 * attempt)
        if len(failures) >= 5:
            status('failed', number, 'Five clips failed; inspect generation.log before restarting.')
            raise RuntimeError('Stopping after five failed clips.')
    state = 'completed' if limit == 500 and not failures else ('test_passed' if not failures else 'incomplete')
    status(state)
    logging.info('Finished: %s, %s clips saved, %s failures', state, len(completed), len(failures))
    if failures:
        raise RuntimeError('Some clips failed; rerun to retry missing clips.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--metadata', type=Path, default=ROOT / 'dataset/metadata.jsonl')
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--steps', type=int, default=16)
    parser.add_argument('--device', choices=['cpu', 'cuda', 'auto'], default='cpu')
    parser.add_argument('--port', type=int, default=3935)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 500 or not 8 <= args.steps <= 64:
        parser.error('limit must be 1..500 and steps must be 8..64')
    if not shutil.which('ffmpeg'):
        parser.error('ffmpeg not found on PATH')
    try:
        run(args)
    except AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:
        logging.exception('Generation stopped: %s', error)
        status_file = args.output.resolve() / 'status.json'
        state = json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
        state.update(state='failed', error=str(error))
        atomic_json(status_file, state)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Docker image build/runtime smoke check (pip check, native libs, isolated server endpoints)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def verify_image(image: str) -> None:
    # 1. pip check + core imports + binaries inside container
    probe = r"""
set -e
for v in decision llm phonon tts wake; do
  echo "=== pip check: $v ==="
  /opt/venvs/$v/bin/pip check
done
echo "=== pip check: stt (excluding native C++ libtranscribe.so) ==="
stt_check=$(/opt/venvs/stt/bin/pip check 2>&1 || true)
filtered=$(echo "$stt_check" | grep -v 'No broken requirements found' | grep -v 'transcribe-cpp .* requires transcribe-cpp-native' || true)
if [ -n "$filtered" ]; then
  echo "$filtered" >&2
  exit 1
fi
pip check
test -x /usr/bin/local-nlu
test -x /usr/local/bin/llama-server
test -f /usr/local/lib/libtranscribe.so
command -v ffmpeg >/dev/null
test -f /opt/model_registry/models.json
/opt/venvs/stt/bin/python -c "import numpy, sherpa_onnx, faster_whisper, ctranslate2, onnx_asr, onnxruntime, pywebrtc_audio, transcribe_cpp, wyoming, huggingface_hub, aiohttp, psutil, filelock"
/opt/venvs/phonon/bin/python -c "import fermion, torch; assert hasattr(fermion, 'load_speech')"
/opt/venvs/decision/bin/python -c "import aiohttp, sentence_transformers, torch, transformers, huggingface_hub, openvino, onnx"
/opt/venvs/llm/bin/python -c "import aiohttp, huggingface_hub, psutil"
/opt/venvs/tts/bin/python -c "import piper, pocket_tts, wyoming, aiohttp"
/opt/venvs/wake/bin/python -c "import wyoming, websocket"
echo "CONTAINER_IMPORTS_OK"
"""
    res = _run(['docker', 'run', '--rm', '--entrypoint', 'bash', image, '-c', probe], check=False)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        raise SystemExit(f'Container static/pip check failed (exit {res.returncode})')
    print('[OK] All venv pip check, binaries and imports verified inside image')

    # 2. Isolated runtime server smoke test (never touches production /data or host ports)
    cname = f'arandu-smoke-{_free_port()}'
    with tempfile.TemporaryDirectory(prefix='arandu-smoke-data-', ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp)
        try:
            _run([
                'docker', 'run', '-d', '--name', cname,
                '-v', f'{data_dir}:/data',
                '-e', 'ARANDU_LOCAL_CONTAINER=1',
                '-e', 'ARANDU_LOCAL_PORT=8099',
                '-e', 'ARANDU_RESIDENT_STT=OFF',
                '-e', 'ARANDU_RESIDENT_TTS=OFF',
                image,
            ])
            client_script = r"""
import json, time, urllib.request
deadline = time.monotonic() + 30.0
state = None
last_err = None
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8099/api/state', timeout=2) as r:
            if r.status == 200:
                state = json.loads(r.read().decode('utf-8'))
                break
    except Exception as exc:
        last_err = repr(exc)
        time.sleep(0.5)
if not state:
    raise SystemExit(f'Server failed on /api/state: {last_err}')
out = {'state_version': state.get('version'), 'endpoints': {}}
for ep in ('/api/stt/models', '/api/llm/models', '/api/tts/models'):
    with urllib.request.urlopen('http://127.0.0.1:8099' + ep, timeout=5) as r:
        assert r.status == 200, f'{ep} -> {r.status}'
        data = json.loads(r.read().decode('utf-8'))
        assert 'models' in data, f'Missing models in {ep}'
        out['endpoints'][ep] = len(data['models'])
print(json.dumps(out))
"""
            res = _run(['docker', 'exec', cname, 'python', '-c', client_script], check=False)
            if res.returncode != 0:
                logs = _run(['docker', 'logs', cname], check=False)
                raise SystemExit(f'Runtime smoke failed:\n{res.stdout}\n{res.stderr}\nContainer logs:\n{logs.stdout}\n{logs.stderr}')
            info = json.loads(res.stdout.strip().splitlines()[-1])
            print(f'[OK] /api/state responded (version={info["state_version"]})')
            for ep, count in info['endpoints'].items():
                print(f'[OK] {ep} HTTP 200 ({count} models)')
        finally:
            # Clean root-owned container artifacts inside bind mount so non-root CI runners can remove tmpdir.
            _run(['docker', 'exec', cname, 'sh', '-c', 'rm -rf /data/* /data/.[!.]* 2>/dev/null || true'], check=False)
            _run(['docker', 'rm', '-f', cname], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description='Docker image smoke test for Arandu Stack')
    parser.add_argument('--image', default='arandu:preflight', help='Docker image tag to verify')
    args = parser.parse_args()
    verify_image(args.image)
    print('[PASS] Docker smoke verification completed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

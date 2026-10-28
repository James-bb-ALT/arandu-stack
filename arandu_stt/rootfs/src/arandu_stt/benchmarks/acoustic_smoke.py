"""Load the real INT8 model and exercise inference plus the lexical pipeline.

Silence is a runtime smoke check, not speech accuracy evaluation. Optionally
provide a mono 16 kHz PCM16 WAV. Download uses the existing model downloader.
"""
import argparse
import json
from pathlib import Path
import platform
import sys
import time
import wave
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from contextual_lexical import ContextualResolver
from model_download import ensure_model
from server import Engine, MAX_SECONDS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=Path('/data/models/int8'))
    parser.add_argument('--inventory', type=Path, default=Path('/data/arandu_ha_inventory.json'))
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.threads <= 8 or not 1 <= args.repeats <= 100:
        parser.error('threads: 1–8; repeats: 1–100')
    if args.download:
        ensure_model(args.model_dir)
    if args.audio:
        with wave.open(str(args.audio), 'rb') as wav:
            if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (16000, 2, 1):
                parser.error('WAV must be mono 16 kHz PCM16')
            if wav.getnframes() > MAX_SECONDS*16000:
                parser.error('WAV exceeds 35 seconds')
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32)/32768
    else:
        audio = np.zeros(16000, dtype=np.float32)
    started = time.perf_counter()
    engine = Engine(args.model_dir, args.threads)
    load_seconds = time.perf_counter()-started
    resolver = ContextualResolver(args.inventory, HERE/'resources', config_path=HERE/'config/lexical.json')
    rows = []
    for _ in range(args.repeats):
        raw, inference, queue = engine.recognize(audio)
        correction = resolver.apply(raw)
        rows.append({'raw': raw, 'final': correction['text'], 'inference_s': inference,
                     'queue_s': queue, 'correction_ms': correction['correction_ms'],
                     'all_tokens_allowed': correction['all_tokens_allowed']})
    result = {'scope': 'real_model_audio_smoke' if args.audio else 'real_model_silence_smoke_not_accuracy',
              'platform': platform.platform(), 'python': platform.python_version(),
              'duration_s': len(audio)/16000, 'threads': args.threads,
              'model_load_s': load_seconds, 'lexical_stats': resolver.stats(), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'lexical_stats'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

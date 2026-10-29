"""Download a pinned OmniVoice snapshot over HTTPS with integrity verification.

Fallback for a stalled Hugging Face Hub download in the installed app runtime.
Run with VoiceStudio's Python environment (provides requests).
"""
import hashlib
import json
from pathlib import Path
import time
import requests

DESTINATION = Path(__file__).resolve().parents[1] / 'runs/jaime_robotico_500/model_checkpoint'
REPOSITORY = 'k2-fsa/OmniVoice'
FILENAMES = ['config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
             'chat_template.jinja', 'audio_tokenizer/config.json',
             'audio_tokenizer/model.safetensors', 'audio_tokenizer/preprocessor_config.json']


def verify(path, entry):
    data_hash = hashlib.sha256() if entry.get('lfs') else hashlib.sha1()
    if not entry.get('lfs'):
        data_hash.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            data_hash.update(chunk)
    expected = entry['lfs']['sha256'] if entry.get('lfs') else entry['blobId']
    if data_hash.hexdigest() != expected:
        raise ValueError(f'Hash mismatch for {path.name}')


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    plan_path = DESTINATION / 'download_manifest.json'
    if plan_path.exists():
        metadata = json.loads(plan_path.read_text(encoding='utf-8'))
    else:
        response = requests.get(f'https://huggingface.co/api/models/{REPOSITORY}?blobs=true', timeout=30)
        response.raise_for_status()
        metadata = response.json()
        plan_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    entries = {item['rfilename']: item for item in metadata['siblings']}
    for filename in FILENAMES:
        target = DESTINATION / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = entries[filename]
        if target.exists():
            verify(target, entry)
            print(f'Already verified: {filename}', flush=True)
            continue
        temporary = target.with_suffix(target.suffix + '.part')
        expected_size = entry.get('lfs', {}).get('size', entry.get('size'))
        if temporary.exists() and temporary.stat().st_size == expected_size:
            verify(temporary, entry)
            temporary.replace(target)
            print(f'Verified completed partial download: {filename}', flush=True)
            continue
        url = f'https://huggingface.co/{REPOSITORY}/resolve/{metadata["sha"]}/{filename}'
        for attempt in range(1, 4):
            try:
                offset = temporary.stat().st_size if temporary.exists() else 0
                headers = {'Range': f'bytes={offset}-'} if offset else {}
                with requests.get(url, headers=headers, stream=True, timeout=(20, 60)) as response:
                    response.raise_for_status()
                    if response.status_code == 206 and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise ValueError('Server returned an unexpected range.')
                    if response.status_code != 206:
                        offset = 0
                    count = offset
                    last_report = began = time.monotonic()
                    with temporary.open('ab' if offset else 'wb') as destination:
                        for chunk in response.iter_content(1024 * 1024):
                            destination.write(chunk)
                            count += len(chunk)
                            if time.monotonic() - last_report >= 10:
                                print(f'{filename}: {count / 1024**2:.0f} MiB, {(count-offset) / 1024**2 / (time.monotonic()-began):.1f} MiB/s', flush=True)
                                last_report = time.monotonic()
                verify(temporary, entry)
                temporary.replace(target)
                print(f'Verified: {filename} ({target.stat().st_size / 1024**2:.1f} MiB)', flush=True)
                break
            except Exception as error:
                print(f'Attempt {attempt} failed: {type(error).__name__}', flush=True)
                if attempt == 3:
                    raise
                time.sleep(5)
    (DESTINATION / 'READY').write_text(metadata['sha'], encoding='ascii')
    print(f'Checkpoint ready: {metadata["sha"]}', flush=True)


if __name__ == '__main__':
    main()

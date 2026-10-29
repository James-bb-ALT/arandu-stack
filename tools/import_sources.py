"""Create a source-only snapshot; never copy credentials, weights or environments."""
from pathlib import Path
import shutil
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SKIP = {'.git', '.venv', '.venv-ai-lab', '.venv-ai-lab-313', '__pycache__', '.pytest_cache', '.ruff_cache',
        'target', 'dist', 'build', 'node_modules', 'archive', 'hf-cache', 'models', 'voices', 'runs', 'logs',
        'state', 'data_private', 'inspection', 'evaluations', 'reference_assets', 'reports', 'docs',
        'implementation-clean-room', 'echomuse', 'dataset', 'datasets', '.cuda-wheels',
        'sophia-ha-app', 'work', '.claude', 'evaluation'}
EXT = {'.py', '.rs', '.toml', '.lock', '.yaml', '.yml', '.json', '.md', '.txt', '.sh', '.ps1', '.cmd',
       '.html', '.js', '.css', '.h', '.c', '.csv', '.tsv', '.xml', '.ino', '.properties', '.kt', '.kts', '.gradle', '.cpp'}
SOURCES = {'nlu': 'Arandu NLU', 'stt': 'Arandu STT/arandu-stt-github', 'tts': 'Arandu TTS',
           'wake': 'Arandu Wake', 'decision': 'Arandu Decision', 'llm': 'Arandu LLM',
           'wakegen': 'Arandu_Wake_Dataset_Generation'}
manifest = {}
for name, origin in SOURCES.items():
    source = WORKSPACE / origin
    folder = {'nlu': 'arandu_nlu', 'stt': 'arandu_stt', 'tts': 'arandu_tts', 'wake': 'arandu_wake', 'decision': 'arandu_decision', 'llm': 'arandu_llm', 'wakegen': 'arandu_wake'}[name]
    destination = ROOT / 'arandu_stack/rootfs/modules' / folder / 'rootfs' / ('dataset_generation' if name == 'wakegen' else 'src')
    count = 0
    paths = []
    for directory, directories, names in os.walk(source):
        directories[:] = [d for d in directories if d not in SKIP and not d.endswith('.egg-info') and not d.startswith('.venv') and not (name == 'wakegen' and d == 'data')]
        paths.extend(Path(directory) / n for n in names)
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(source)
        if any(part in SKIP or part.endswith('.egg-info') or part.startswith('.venv') for part in rel.parts):
            continue
        if path.name.startswith('.env') or path.name in {'local.json', 'secrets.yaml', 'credentials.json', 'external_assets.json', 'config.local.json'}:
            continue
        if path.suffix.lower() not in EXT and 'vendor' not in rel.parts and not path.name.startswith(('LICENSE', 'COPYING', 'NOTICE')) and path.name not in {'Dockerfile', 'Makefile', '.gitignore', '.dockerignore', '.gitattributes', 'run'}:
            continue
        if path.stat().st_size > 3_000_000:
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        count += 1
    manifest[name] = {'source': origin, 'files': count}
(ROOT / 'SOURCE-SNAPSHOT.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(json.dumps(manifest))

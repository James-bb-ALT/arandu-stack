"""Developer-only Linux staging for a Windows checkout; no runtime wrapper."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

source = Path('/source')
workspace = Path('/workspace')
task = sys.argv[1]
if task == 'versions':
    for command in (['rustc', '--version'], ['cargo', '--version'],
                    ['ruby', '--version'], ['python3', '--version']):
        subprocess.run(command, check=True)
    raise SystemExit(0)

if task == 'check':
    subprocess.run([sys.executable, str(source / 'tools/distribution.py')], check=True)

# A fresh container supplies an empty Linux workspace. Copy bytes, not NTFS modes.
# Restore archive modes; the existing exact-tree verifier still validates them.
for relative in ('addon', 'custom_components', 'data', 'tests/mlp', 'tools', 'evaluation', '.github'):
    if not (source / relative).is_dir():
        continue
    shutil.copytree(source / relative, workspace / relative,
                    copy_function=shutil.copyfile,
                    ignore=shutil.ignore_patterns('__pycache__', '__MACOSX', '.DS_Store', 'target'))
if (source / '.git').is_dir():
    shutil.copytree(source / '.git', workspace / '.git', copy_function=shutil.copyfile,
                    ignore=shutil.ignore_patterns('__pycache__', '*.lock'))
if (source / '.cargo').is_dir():
    shutil.copytree(source / '.cargo', workspace / '.cargo', copy_function=shutil.copyfile)
for name in ('Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', 'LICENSE', 'repository.yaml', 'hacs.json', 'release.json', 'README.md', 'INSTALL.md'):
    shutil.copyfile(source / name, workspace / name)
# Only executable vendor file in the current frozen dependency set. Verified from
# unicode-normalization-0.1.25.crate, SHA256:
# 5fd4f6878c9cb28d874b009da9e8d183b5abc80117c40bbd187a1fde336be6e8
(workspace / 'addon/vendor/unicode-normalization-0.1.25/scripts/unicode.py').chmod(0o755)
for path in (workspace / 'tools').rglob('*'):
    if path.is_file():
        with path.open('rb') as file:
            if file.read(2) == b'#!':
                # NTFS checkouts may have CRLF; executable Linux staging uses LF.
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
                path.chmod(0o755)
(workspace / 'target').symlink_to('/output', target_is_directory=True)
os.chdir(workspace)
commands = {
    'check': ['sh', './tools/mlp-check'],
    'build': ['cargo', 'build', '--workspace', '--release', '--locked', '--offline'],
    'corpus': ['ruby', 'tools/generate-mlp-corpus.rb'],
    'corpus-check': ['ruby', 'tools/generate-mlp-corpus.rb', '--check'],
}

evaluation = Path('/workspace/evaluation/ptbr-independent')
if task == 'evaluation-check':
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(evaluation), '-p', 'test_*.py'], check=True)
    subprocess.run([sys.executable, '-m', 'py_compile', *map(str, evaluation.glob('*.py'))], check=True)
    subprocess.run([sys.executable, str(evaluation / 'freeze_v2.py'), '--check'], check=True)
    subprocess.run([sys.executable, str(evaluation / 'phase_a_manifest.py'), '--check'], check=True)
    raise SystemExit(0)
if task == 'evaluation-freeze':
    subprocess.run([sys.executable, str(evaluation / 'freeze_v2.py')], check=True)
    raise SystemExit(0)
if task == 'evaluation-run':
    subprocess.run(['cargo', 'build', '--workspace', '--release', '--locked', '--offline'], check=True)
    binary = Path('/workspace/target/release/local-nlu')
    subprocess.run([sys.executable, str(evaluation / 'run_v2.py'), '--binary', str(binary), '--output-dir', '/output/ptbr-independent'], check=True)
    raise SystemExit(0)
if task == 'evaluation-benchmark':
    subprocess.run(['cargo', 'build', '--workspace', '--release', '--locked', '--offline'], check=True)
    binary = Path('/workspace/target/release/local-nlu')
    subprocess.run([sys.executable, str(evaluation / 'benchmark_v2.py'), '--binary', str(binary), '--output-dir', '/output/ptbr-independent'], check=True)
    raise SystemExit(0)
if task == 'evaluation-record':
    subprocess.run([sys.executable, str(evaluation / 'record.py'), '--version', '2', '--source', '/output/ptbr-independent', '--destination', '/baseline/v2'], check=True)
    raise SystemExit(0)
subprocess.run(commands[task], check=True)
if task == 'corpus':
    for name in ('project-authored-synthetic-catalog-v1.json', 'project-authored-synthetic-v1.jsonl'):
        shutil.copyfile(workspace / 'data/mlp' / name, Path('/corpus-output') / name)

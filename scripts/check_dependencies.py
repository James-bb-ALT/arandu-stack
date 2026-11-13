#!/usr/bin/env python3
"""Automated dependency resolution, pinning audit and `pip check` gate for Arandu Stack."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / 'arandu_stack' / 'rootfs' / 'modules'


@dataclass
class EnvSpec:
    name: str
    venv_dir: str
    requirements_files: tuple[Path, ...] = ()
    pyproject_spec: str | None = None
    pyproject_path: Path | None = None
    extras: tuple[str, ...] = ()


ENVS: tuple[EnvSpec, ...] = (
    EnvSpec(
        name='stt',
        venv_dir='/opt/venvs/stt',
        requirements_files=(MODULES / 'arandu_stt' / 'rootfs' / 'src' / 'arandu_stt' / 'requirements.txt',),
    ),
    EnvSpec(
        name='phonon',
        venv_dir='/opt/venvs/phonon',
        requirements_files=(MODULES / 'arandu_stt' / 'rootfs' / 'src' / 'arandu_stt' / 'requirements-phonon.txt',),
    ),
    EnvSpec(
        name='decision',
        venv_dir='/opt/venvs/decision',
        pyproject_path=MODULES / 'arandu_decision' / 'rootfs' / 'src' / 'pyproject.toml',
        extras=('gpu',),
    ),
    EnvSpec(
        name='llm',
        venv_dir='/opt/venvs/llm',
        pyproject_path=MODULES / 'arandu_llm' / 'rootfs' / 'src' / 'pyproject.toml',
    ),
    EnvSpec(
        name='tts',
        venv_dir='/opt/venvs/tts',
        pyproject_path=MODULES / 'arandu_tts' / 'rootfs' / 'src' / 'pyproject.toml',
        extras=('pocket',),
    ),
    EnvSpec(
        name='wake',
        venv_dir='/opt/venvs/wake',
        requirements_files=(MODULES / 'arandu_wake' / 'rootfs' / 'src' / 'requirements-detector.txt',),
        pyproject_path=MODULES / 'arandu_wake' / 'rootfs' / 'src' / 'pyproject.toml',
    ),
)

_PIN_RE = re.compile(r'==|>=|<=|~=|<|>|@\s*https?://')


def _parse_req_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _extract_specs(env: EnvSpec) -> list[str]:
    specs: list[str] = []
    for req in env.requirements_files:
        if not req.is_file():
            raise FileNotFoundError(f'[{env.name}] Missing requirements file: {req}')
        specs.extend(_parse_req_lines(req))
    if env.pyproject_path is not None:
        if not env.pyproject_path.is_file():
            raise FileNotFoundError(f'[{env.name}] Missing pyproject.toml: {env.pyproject_path}')
        data = tomllib.loads(env.pyproject_path.read_text(encoding='utf-8'))
        proj = data.get('project', {})
        specs.extend(proj.get('dependencies', []))
        opt = proj.get('optional-dependencies', {})
        for extra in env.extras:
            if extra not in opt:
                raise KeyError(f'[{env.name}] Missing extra [{extra}] in {env.pyproject_path}')
            specs.extend(opt[extra])
    return specs


def audit_static_metadata() -> list[str]:
    """Verify pinning, upper bounds and critical cross-module invariants."""
    errors: list[str] = []
    by_env: dict[str, list[str]] = {}
    for env in ENVS:
        specs = _extract_specs(env)
        by_env[env.name] = specs
        for spec in specs:
            if not _PIN_RE.search(spec):
                errors.append(f'DEPENDENCY_CONFLICT [{env.name}] unpinned dependency: {spec!r}')

    stt_joined = '\n'.join(by_env['stt']).lower()
    if 'fermion-research' in stt_joined or 'fermion_research' in stt_joined:
        errors.append('DEPENDENCY_CONFLICT [stt] fermion-research must stay isolated in requirements-phonon.txt (/opt/venvs/phonon)')
    if 'huggingface_hub==0.36.0' not in stt_joined and 'huggingface-hub==0.36.0' not in stt_joined:
        errors.append('DEPENDENCY_CONFLICT [stt] expected huggingface_hub==0.36.0 to match Decision/LLM')

    dec_joined = '\n'.join(by_env['decision']).lower()
    if 'transformers==4.57.3' not in dec_joined or 'huggingface-hub==0.36.0' not in dec_joined:
        errors.append('DEPENDENCY_CONFLICT [decision] expected transformers==4.57.3 and huggingface-hub==0.36.0')

    phonon_joined = '\n'.join(by_env['phonon']).lower()
    if 'fermion-research==' not in phonon_joined:
        errors.append('DEPENDENCY_CONFLICT [phonon] fermion-research must be pinned in requirements-phonon.txt')

    return errors


def run_dry_run_resolution(python_exe: str = sys.executable) -> list[str]:
    """Run `pip install --dry-run --ignore-installed` for each environment."""
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix='arandu-depcheck-') as tmp:
        tmp_path = Path(tmp)
        for env in ENVS:
            cmd = [python_exe, '-m', 'pip', 'install', '--dry-run', '--ignore-installed']
            for req in env.requirements_files:
                cmd.extend(['-r', str(req)])
            if env.pyproject_path is not None:
                # Copy package source to temp so pip does not write *.egg-info into repo
                pkg_copy = tmp_path / env.name
                shutil.copytree(env.pyproject_path.parent, pkg_copy)
                target = f'{pkg_copy}[{",".join(env.extras)}]' if env.extras else str(pkg_copy)
                cmd.append(target)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tail = '\n'.join((proc.stdout + '\n' + proc.stderr).splitlines()[-25:])
                errors.append(f'DEPENDENCY_CONFLICT [{env.name}] dry-run resolution failed:\n{tail}')
            else:
                print(f'[OK] dry-run resolution: {env.name}')
    return errors


def run_installed_pip_check() -> list[str]:
    """Run `pip check` in every installed /opt/venvs/* environment."""
    errors: list[str] = []
    for env in ENVS:
        pip_bin = Path(env.venv_dir) / 'bin' / 'pip'
        if not pip_bin.is_file():
            errors.append(f'MODULE_STARTUP_FAILED [{env.name}] missing venv pip at {pip_bin}')
            continue
        proc = subprocess.run([str(pip_bin), 'check'], capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(f'DEPENDENCY_CONFLICT [{env.name}] pip check failed:\n{proc.stdout}{proc.stderr}')
        else:
            print(f'[OK] pip check: {env.name} ({env.venv_dir})')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Arandu Stack dependency & pip check gate')
    parser.add_argument('--dry-run', action='store_true', help='Run pip install --dry-run --ignore-installed')
    parser.add_argument('--installed-only', action='store_true', help='Run pip check on /opt/venvs/*')
    parser.add_argument('--json-out', type=Path, help='Write JSON report')
    args = parser.parse_args()

    errors = audit_static_metadata()
    if args.dry_run:
        errors.extend(run_dry_run_resolution())
    if args.installed_only:
        errors.extend(run_installed_pip_check())

    report = {
        'ok': not errors,
        'errors': errors,
        'environments': [e.name for e in ENVS],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    if errors:
        for err in errors:
            print(f'[FAIL] {err}', file=sys.stderr)
        return 1
    print('[PASS] All dependency and metadata checks succeeded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

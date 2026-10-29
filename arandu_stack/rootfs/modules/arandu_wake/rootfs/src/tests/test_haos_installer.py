"""Simula install-haos.sh sem acessar Home Assistant ou executar o CLI real.

Requer Python 3.10+, GNU Bash e jq reais. Ausência deles produz skip explícito.
Linux: python -m unittest discover -s tests -p test_haos_installer.py -v
Windows: use Git Bash (não o launcher WSL) e informe jq já instalado:
  $env:ARANDU_BASH = 'C:/Program Files/Git/bin/bash.exe'
  $env:ARANDU_JQ = 'C:/ferramentas/jq.exe'
  python -m unittest discover -s tests -p test_haos_installer.py -v

O teste não baixa dependências, não pede tokens e não instala/inicia serviços.
Todo comando `ha` é substituído pela fixture abaixo em diretório temporário.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FAKE_HA = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >>"$HA_TEST_LOG"
if [[ $* == 'apps --help' ]]; then
  [[ $HA_TEST_CASE != old-cli ]]; exit
fi
if [[ $* == 'os info' ]]; then exit 0; fi
if [[ $* == 'apps --raw-json' || $* == 'addons --raw-json' ]]; then
  case "$HA_TEST_CASE" in
    existing|already-started)
      printf '%s' '{"result":"ok","data":{"addons":[{"slug":"core_openwakeword"}]}}' ;;
    malformed)
      printf '%s' '{"result":"ok","data":{"unexpected":[]}}' ;;
    *) printf '%s' '{"result":"ok","data":{"addons":[]}}' ;;
  esac
  exit 0
fi
if [[ $1 == backups ]]; then
  [[ $HA_TEST_CASE != backup-fails ]] || exit 23
  printf '%s' '{"result":"ok","data":{"slug":"fake-backup"}}'
  exit 0
fi
if [[ $2 == install && $3 == core_openwakeword ]]; then exit 0; fi
if [[ $2 == info && $3 == core_openwakeword ]]; then
  state=stopped
  if [[ -f $HA_TEST_STARTED || $HA_TEST_CASE == already-started ]]; then state=started; fi
  printf '{"result":"ok","data":{"state":"%s","version":"2.1.1"}}' "$state"
  exit 0
fi
if [[ $2 == start && $3 == core_openwakeword ]]; then
  [[ $HA_TEST_CASE != start-fails ]] || exit 24
  if [[ $HA_TEST_CASE != never-starts ]]; then touch "$HA_TEST_STARTED"; fi
  exit 0
fi
exit 97
'''


def shell_path(path):
    """Map a Windows absolute path for Git Bash; leave Unix paths intact."""
    text = Path(path).resolve().as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


class HaosInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        cls.bash = os.environ.get("ARANDU_BASH")
        if not cls.bash:
            cls.bash = str(git_bash) if os.name == "nt" and git_bash.exists() else shutil.which("bash")
        cls.jq = os.environ.get("ARANDU_JQ") or shutil.which("jq")
        if not cls.bash or not cls.jq:
            raise unittest.SkipTest("Instale GNU Bash e jq; ou defina ARANDU_BASH e ARANDU_JQ")
        try:
            probe = subprocess.run([cls.bash, "--version"], capture_output=True, text=True, timeout=10)
            if probe.returncode or "GNU bash" not in probe.stdout:
                raise unittest.SkipTest("ARANDU_BASH precisa apontar para GNU Bash funcional")
            probe = subprocess.run([cls.jq, "--version"], capture_output=True, text=True, timeout=10)
            if probe.returncode or not probe.stdout.startswith("jq-"):
                raise unittest.SkipTest("ARANDU_JQ precisa apontar para jq funcional")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"Bash/jq indisponível: {exc}") from exc
        cls.script = Path(__file__).resolve().parents[1] / "scripts/linux/install-haos.sh"

    def run_case(self, case, action="--install"):
        with tempfile.TemporaryDirectory(prefix="arandu-haos-test-") as temp:
            folder = Path(temp)
            ha = folder / "ha"
            ha.write_text(FAKE_HA, encoding="utf-8", newline="\n")
            ha.chmod(0o755)
            jq_copy = folder / ("jq.exe" if os.name == "nt" else "jq")
            shutil.copy2(self.jq, jq_copy)
            jq_copy.chmod(0o755)
            log = folder / "calls.log"
            env = dict(os.environ, HA_TEST_CASE=case, HA_TEST_LOG=shell_path(log),
                       HA_TEST_STARTED=shell_path(folder / "started"))
            result = subprocess.run(
                [self.bash, "--noprofile", "--norc", "-c",
                 'export PATH="$1:$PATH"; exec bash "$2" "$3"',
                 "_", shell_path(folder), shell_path(self.script), action],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    def assert_no_mutation(self, calls):
        self.assertFalse(any(" install " in c or " start " in c for c in calls), calls)

    def test_check_never_creates_backup_or_changes_app(self):
        result, calls = self.run_case("fresh", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c.startswith("backups ") for c in calls), calls)
        self.assert_no_mutation(calls)

    def test_fresh_install_backs_up_before_install_and_confirms_started(self):
        result, calls = self.run_case("fresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next(i for i, c in enumerate(calls) if c.startswith("backups new "))
        install = calls.index("apps install core_openwakeword")
        start = calls.index("apps start core_openwakeword")
        self.assertLess(backup, install)
        self.assertLess(install, start)
        self.assertEqual(calls[-1], "apps info core_openwakeword --raw-json")
        self.assertIn("2.1.1", result.stdout)
        self.assertIn("Aplicativo disponível", result.stdout)

    def test_existing_app_is_not_reinstalled(self):
        result, calls = self.run_case("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("apps install core_openwakeword", calls)
        self.assertIn("apps start core_openwakeword", calls)

    def test_started_app_is_neither_reinstalled_nor_restarted(self):
        result, calls = self.run_case("already-started")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_mutation(calls)

    def test_backup_failure_blocks_all_app_mutations(self):
        result, calls = self.run_case("backup-fails")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue(calls[-1].startswith("backups new "), calls)
        self.assert_no_mutation(calls)
        self.assertNotIn("Aplicativo disponível", result.stdout)

    def test_invalid_inventory_stops_before_backup(self):
        result, calls = self.run_case("malformed")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c.startswith("backups ") for c in calls), calls)
        self.assert_no_mutation(calls)

    def test_successful_start_command_does_not_mask_stopped_state(self):
        result, calls = self.run_case("never-starts")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(calls.count("apps info core_openwakeword --raw-json"), 6)
        self.assertIn("não confirmou estado started", result.stderr)
        self.assertNotIn("Aplicativo disponível", result.stdout)

    def test_failed_start_command_is_propagated(self):
        result, calls = self.run_case("start-fails")
        self.assertEqual(result.returncode, 24, result.stderr)
        self.assertEqual(calls[-1], "apps start core_openwakeword")
        self.assertNotIn("Aplicativo disponível", result.stdout)

    def test_older_cli_falls_back_to_addons_alias(self):
        result, calls = self.run_case("old-cli")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("addons --raw-json", calls)
        self.assertIn("addons install core_openwakeword", calls)
        self.assertIn("addons start core_openwakeword", calls)


if __name__ == "__main__":
    unittest.main()

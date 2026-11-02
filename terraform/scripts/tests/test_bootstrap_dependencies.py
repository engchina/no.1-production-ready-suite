"""Git の無い初回配備を実 bootstrap と隔離した command で検証する。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class BootstrapDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / "tmp"
        scratch.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="bootstrap-git-", dir=scratch)
        self.addCleanup(directory.cleanup)
        self.cwd = Path(directory.name)
        self.bin = self.cwd / "bin"
        self.bin.mkdir()
        for command in ("bash", "seq", "stat", "mkdir"):
            executable = shutil.which(command)
            assert executable
            (self.bin / command).symlink_to(executable)
        self.command("sleep", "exit 0")
        self.command("dos2unix", "exit 0")
        self.command("runuser", 'shift 3; exec "$@"')
        self.git_source = self.cwd / "git-source"
        self.git_source.write_text(
            '#!/bin/bash\nprintf "git %s\\n" "$*" >> "$TASK_EVENTS"\n'
            'if [ "$1" = clone ]; then\n'
            '  dest="${@: -1}"\n'
            '  mkdir -p "$dest/.git" "$dest/platform/deploy"\n'
            '  printf \'printf "suite-init\\\\n" >> "$TASK_EVENTS"\\n\' > "$dest/platform/deploy/suite-init.sh"\n'
            "fi\n"
        )
        self.git_source.chmod(0o755)
        template = (
            ROOT / "terraform/stack/cloud_init/bootstrap.template.yaml"
        ).read_text()
        embedded = template.split('  - path: "/u01/aipoc/bootstrap-suite.sh"', 1)[1]
        script = embedded.split("    content: |\n", 1)[1].split("\nruncmd:", 1)[0]
        script = textwrap.dedent(script).replace("$${", "${")
        script = script.replace('APP_ROOT="/u01/aipoc"', f'APP_ROOT="{self.cwd}/app"')
        script = script.replace(
            "${application_git_url}", "https://example.invalid/suite.git"
        )
        script = script.replace("${application_git_ref}", "main")
        self.script = self.cwd / "bootstrap.sh"
        self.script.write_text(script)
        self.env = os.environ | {
            "PATH": str(self.bin),
            "TASK_EVENTS": str(self.cwd / "events"),
        }

    def command(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def installer(self, *, transient: bool = False, broken: bool = False) -> None:
        body = 'printf "install %s\\n" "$*" >> "$TASK_EVENTS"\n'
        if transient:
            body += 'if [ ! -f "${TASK_EVENTS}.attempted" ]; then : > "${TASK_EVENTS}.attempted"; exit 1; fi\n'
        if not broken:
            # PATH に Git が無い状態から、導入成功時に初めて使えるようにする。
            body += f'/bin/ln -s "{self.git_source}" "{self.bin}/git"\n'
        self.command("dnf", body)

    def run_bootstrap(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.script)],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def events(self) -> list[str]:
        path = self.cwd / "events"
        return path.read_text().splitlines() if path.exists() else []

    def test_missing_git_is_installed_before_clone(self) -> None:
        self.installer()
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events()
        self.assertEqual(events[0], "install -y install ca-certificates git")
        self.assertTrue(events[1].startswith("git clone --branch main"), events)
        self.assertEqual(events[2], "suite-init")

    def test_transient_install_failure_is_retried_before_clone(self) -> None:
        self.installer(transient=True)
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(line.startswith("install ") for line in self.events()), 2)
        self.assertIn("suite-init", self.events())

    def test_permanent_install_failure_does_not_clone_or_initialize(self) -> None:
        self.command("dnf", 'printf "install\\n" >> "$TASK_EVENTS"; exit 23')
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(self.events(), ["install"] * 5)
        self.assertIn("Bootstrap failed", result.stdout)

    def test_successful_install_without_git_stops_before_clone(self) -> None:
        self.installer(broken=True)
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 127, result.stdout + result.stderr)
        self.assertEqual(len(self.events()), 1)
        self.assertIn("Git is still unavailable", result.stdout)

    def test_existing_git_skips_install_and_supports_reexecution(self) -> None:
        (self.bin / "git").symlink_to(self.git_source)
        self.command("dnf", "exit 99")
        first = self.run_bootstrap()
        second = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse(any(line.startswith("install ") for line in self.events()))
        self.assertTrue(any(" fetch " in line for line in self.events()), self.events())
        self.assertEqual(self.events().count("suite-init"), 2)


if __name__ == "__main__":
    unittest.main()

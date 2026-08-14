#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HERMES_RAW = os.environ.get("BWA_HERMES_EXECUTABLE", "")
HERMES = Path(HERMES_RAW).resolve() if HERMES_RAW else None


@unittest.skipUnless(HERMES and HERMES.is_file(), "需要 BWA_HERMES_EXECUTABLE 才能验证真实 Cron CLI")
class OptionalHermesRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.profile = "optionaltest"
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HERMES_HOME": str(self.home),
        }
        created = self.run_cli("profile", "create", self.profile, "--no-alias", "--no-skills")
        self.assertEqual(created.returncode, 0, created.stderr)
        configured = self.run_profile("config", "set", "cron.wrap_response", "false")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        read_back = self.run_profile("config", "get", "cron.wrap_response")
        self.assertEqual(read_back.returncode, 0, read_back.stderr)
        self.assertEqual(read_back.stdout.strip().lower(), "false")
        scripts = self.home / "profiles" / self.profile / "scripts"
        scripts.mkdir(mode=0o700)
        (scripts / "daily_acceptance.py").write_text(
            "print('【隔离日报验收】数据源正常，任务已完成。')\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(HERMES), *arguments], capture_output=True, text=True,
            env=self.env, timeout=30, check=False,
        )

    def run_profile(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("-p", self.profile, *arguments)

    def test_cron_create_manual_run_pause_boundary_resume_remove(self) -> None:
        created = self.run_profile(
            "cron", "create", "0 9 * * *", "--name", "隔离日报验收",
            "--deliver", "local", "--script", "daily_acceptance.py", "--no-agent",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        marker = "Created job:"
        self.assertIn(marker, created.stdout)
        job_id = created.stdout.split(marker, 1)[1].splitlines()[0].strip()
        self.assertRegex(job_id, r"^[0-9a-f]+$")

        executed = self.run_profile("cron", "run", job_id)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertIn("Ran now: succeeded", executed.stdout)
        history = self.run_profile("cron", "runs", job_id, "--limit", "5")
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertIn("completed", history.stdout)
        outputs = list((self.home / "profiles" / self.profile / "cron" / "output" / job_id).glob("*.md"))
        self.assertEqual(len(outputs), 1)
        self.assertIn("【隔离日报验收】数据源正常", outputs[0].read_text(encoding="utf-8"))

        paused = self.run_profile("cron", "pause", job_id)
        self.assertEqual(paused.returncode, 0, paused.stderr)
        listed = self.run_profile("cron", "list", "--all")
        self.assertIn("[paused]", listed.stdout)

        escaped = self.run_profile(
            "cron", "create", "0 10 * * *", "--name", "越界任务",
            "--deliver", "local", "--script", "../outside.py", "--no-agent",
        )
        escaped_output = escaped.stdout + escaped.stderr
        self.assertIn("Failed to create job", escaped_output)
        self.assertIn("escapes the scripts directory", escaped_output)
        after_escape = self.run_profile("cron", "list", "--all")
        self.assertNotIn("越界任务", after_escape.stdout)

        self.assertEqual(self.run_profile("cron", "resume", job_id).returncode, 0)
        self.assertEqual(self.run_profile("cron", "remove", job_id).returncode, 0)
        final = self.run_profile("cron", "list", "--all")
        self.assertIn("No scheduled jobs", final.stdout)

    def test_cron_failed_script_has_durable_failed_receipt(self) -> None:
        scripts = self.home / "profiles" / self.profile / "scripts"
        (scripts / "daily_failure.py").write_text(
            "raise SystemExit('数据源暂时不可用')\n", encoding="utf-8"
        )
        created = self.run_profile(
            "cron", "create", "0 9 * * *", "--name", "隔离日报失败验收",
            "--deliver", "local", "--script", "daily_failure.py", "--no-agent",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        marker = "Created job:"
        self.assertIn(marker, created.stdout)
        job_id = created.stdout.split(marker, 1)[1].splitlines()[0].strip()

        executed = self.run_profile("cron", "run", job_id)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertIn("Ran now: failed", executed.stdout)
        self.assertNotIn("Ran now: succeeded", executed.stdout)
        history = self.run_profile("cron", "runs", job_id, "--limit", "5")
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertIn("failed", history.stdout)
        self.assertNotIn("completed", history.stdout)

        removed = self.run_profile("cron", "remove", job_id)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        final = self.run_profile("cron", "list", "--all")
        self.assertIn("No scheduled jobs", final.stdout)


if __name__ == "__main__":
    unittest.main()

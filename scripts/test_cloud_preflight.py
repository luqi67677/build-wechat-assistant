#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_cloud_preflight.py"
SPEC = importlib.util.spec_from_file_location("check_cloud_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class CloudPreflightTests(unittest.TestCase):
    def test_public_hostname_validation_rejects_urls_and_local_names(self) -> None:
        self.assertTrue(CHECKER.safe_public_hostname("api.deepseek.com"))
        for value in ("https://api.deepseek.com", "localhost", "127.0.0.2", "api.local/path"):
            self.assertFalse(CHECKER.safe_public_hostname(value))

    def test_all_safe_signals_pass_without_printing_host(self) -> None:
        with patch.object(CHECKER.platform, "system", return_value="Linux"), patch.object(
            CHECKER.platform, "machine", return_value="x86_64"
        ), patch.object(CHECKER.os, "geteuid", return_value=1000), patch.object(
            CHECKER.os, "getuid", return_value=1000
        ), patch.object(CHECKER.shutil, "which", return_value="/safe/bin/tool"), patch.object(
            CHECKER.Path, "is_dir", return_value=True
        ), patch.object(CHECKER.Path, "exists", return_value=True), patch.object(
            CHECKER, "run_ok", return_value=True
        ), patch.object(CHECKER, "memory_gib", return_value=2), patch.object(
            CHECKER, "disk_free_gib", return_value=10
        ), patch.object(CHECKER.os, "cpu_count", return_value=2), patch.object(
            CHECKER, "tls_reachable", return_value=True
        ), patch.object(
            CHECKER, "systemd_user_manager_env_clean", return_value=True
        ):
            checks = CHECKER.evaluate("api.deepseek.com")
        self.assertTrue(all(checks.values()))
        output = json.dumps(checks)
        self.assertNotIn("api.deepseek.com", output)

    def test_root_or_missing_linger_fails(self) -> None:
        with patch.object(CHECKER.platform, "system", return_value="Linux"), patch.object(
            CHECKER.platform, "machine", return_value="x86_64"
        ), patch.object(CHECKER.os, "geteuid", return_value=0), patch.object(
            CHECKER.shutil, "which", return_value="/safe/bin/tool"
        ), patch.object(CHECKER, "run_ok", return_value=False), patch.object(
            CHECKER, "memory_gib", return_value=2
        ), patch.object(CHECKER, "disk_free_gib", return_value=10), patch.object(
            CHECKER, "tls_reachable", return_value=True
        ), patch.object(
            CHECKER, "systemd_user_manager_env_clean", return_value=False
        ):
            checks = CHECKER.evaluate("api.deepseek.com")
        self.assertFalse(checks["service_account_is_nonroot"])
        self.assertFalse(checks["linger_enabled"])

    def test_manager_only_weixin_override_fails_without_printing_value(self) -> None:
        canary = "manager-env-secret-canary"
        completed = CHECKER.subprocess.CompletedProcess(
            [], 0, stdout=f"PATH=/safe\nWEIXIN_BASE_URL=https://{canary}.example\n", stderr=""
        )
        with patch.object(CHECKER.subprocess, "run", return_value=completed):
            self.assertFalse(CHECKER.systemd_user_manager_env_clean())

    def test_manager_model_key_or_shared_auth_override_fails(self) -> None:
        for key in ("KIMI_API_KEY", "FUTURE_PROVIDER_API_KEY", "HERMES_SHARED_AUTH_DIR"):
            completed = CHECKER.subprocess.CompletedProcess(
                [], 0, stdout=f"PATH=/safe\n{key}=canary\n", stderr=""
            )
            with self.subTest(key=key), patch.object(CHECKER.subprocess, "run", return_value=completed):
                self.assertFalse(CHECKER.systemd_user_manager_env_clean())

    def test_main_outputs_only_boolean_evidence(self) -> None:
        safe = {"one": True, "two": True}
        stream = io.StringIO()
        with patch.object(CHECKER, "evaluate", return_value=safe), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--model-host", "api.deepseek.com"])
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        self.assertFalse(payload["secrets_printed"])
        self.assertNotIn("api.deepseek.com", stream.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

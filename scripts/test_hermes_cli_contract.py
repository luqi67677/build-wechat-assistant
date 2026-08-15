#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_hermes_cli_contract.py"
SPEC = importlib.util.spec_from_file_location("check_hermes_cli_contract", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / "scripts"))
SPEC.loader.exec_module(CHECKER)
CANARY = "cli-contract-canary-never-print"


class HermesCliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[list[str]] = []
        self.install_help = "--start-now --no-start-now --start-on-login --no-start-on-login"
        self.profile_create_help = "--no-alias --no-skills"
        self.gateway_help = "Manage Telegram, Discord, WhatsApp, Weixin; setup"
        self.status_help = "--deep --full"
        self.auth_add_help = "usage: auth add provider --type {api_key,oauth}"
        self.deep_status_returncode = 0

    def fake_run(self, command: list[str], env: dict[str, str]):
        self.calls.append(command)
        hermes = command[0]
        profile_dir = Path(env["HERMES_HOME"]) / "profiles" / "bwaclitest"
        if command == [hermes, "--version"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, "Hermes Agent v0.30.0 (test)\n", "")
        if command[1:4] == ["profile", "create", "bwaclitest"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, "created\n", "")
        if command[1:] == ["profile", "show", "bwaclitest"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, "shown\n", "")
        if command[-2:] == ["config", "path"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, f"{profile_dir / 'config.yaml'}\n", "")
        if command[-2:] == ["config", "env-path"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, f"{profile_dir / '.env'}\n", "")
        if command[-3:] == ["gateway", "install", "--help"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, self.install_help, "")
        if command[1:] == ["profile", "create", "--help"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, self.profile_create_help, "")
        if command[1:] == ["gateway", "--help"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, self.gateway_help, "")
        if command[1:] == ["gateway", "status", "--help"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, self.status_help, "")
        if command[-3:] == ["auth", "add", "--help"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, self.auth_add_help, "")
        if command[-3:] == ["gateway", "status", "--deep"]:
            return CHECKER.subprocess.CompletedProcess(
                command, self.deep_status_returncode, "Gateway is not running\n", ""
            )
        if command[-4:] == ["tools", "list", "--platform", "weixin"]:
            already_disabled = any("disable" in call for call in self.calls[:-1])
            body = "Built-in toolsets (weixin):\n  ✓ enabled  clarify  Clarify\n"
            if not already_disabled:
                body += "  ✓ enabled  web  Web\n"
            return CHECKER.subprocess.CompletedProcess(command, 0, body, "")
        if "apply_chat_safety_baseline.py" in " ".join(command):
            return CHECKER.subprocess.CompletedProcess(command, 0, json.dumps({"result": "PASS"}), "")
        if "check_pre_qr_safety.py" in " ".join(command):
            return CHECKER.subprocess.CompletedProcess(command, 0, json.dumps({"result": "PASS"}), "")
        if "--help" in command or "config" in command or "disable" in command:
            return CHECKER.subprocess.CompletedProcess(command, 0, f"ok {CANARY}\n", "")
        raise AssertionError(command)

    def run_main(self) -> tuple[int, dict, str]:
        stream = io.StringIO()
        with patch.object(CHECKER, "_run", side_effect=self.fake_run), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--hermes", "/fake/hermes"])
        output = stream.getvalue()
        return code, json.loads(output), output

    def test_contract_uses_isolated_profile_and_positional_show(self) -> None:
        code, payload, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        self.assertIn(["/fake/hermes", "profile", "show", "bwaclitest"], self.calls)
        self.assertIn(
            ["/fake/hermes", "profile", "create", "bwaclitest", "--no-alias", "--no-skills"],
            self.calls,
        )
        self.assertTrue(any("apply_chat_safety_baseline.py" in " ".join(call) for call in self.calls))
        self.assertNotIn(CANARY, output)

    def test_missing_service_flag_fails_closed(self) -> None:
        self.install_help = "--start-now --start-on-login"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["service_start_flags_advertised"])

    def test_missing_weixin_or_deep_status_fails_closed(self) -> None:
        self.gateway_help = "Manage Telegram; setup"
        self.status_help = "--full"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_gateway_advertised"])
        self.assertFalse(payload["checks"]["deep_gateway_status_advertised"])

    def test_missing_isolated_profile_flags_fails_closed(self) -> None:
        self.profile_create_help = "--clone --clone-all"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["isolated_profile_create_flags_advertised"])

    def test_missing_provider_scoped_auth_fails_closed(self) -> None:
        self.auth_add_help = "usage: auth wizard"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["provider_scoped_auth_advertised"])

    def test_deep_status_must_execute_not_only_appear_in_help(self) -> None:
        self.deep_status_returncode = 2
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["deep_gateway_status_executes"])

    def test_command_timeout_allows_slow_cloud_baseline(self) -> None:
        completed = CHECKER.subprocess.CompletedProcess(["/fake/hermes", "--version"], 0, "", "")
        with patch.object(CHECKER.subprocess, "run", return_value=completed) as run:
            CHECKER._run(["/fake/hermes", "--version"], {})
        self.assertEqual(run.call_args.kwargs["timeout"], 90)


if __name__ == "__main__":
    unittest.main(verbosity=2)

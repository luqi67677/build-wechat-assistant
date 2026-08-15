#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import ANY, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "launch_trusted_handoff.py"
SPEC = importlib.util.spec_from_file_location("launch_trusted_handoff", MODULE_PATH)
assert SPEC and SPEC.loader
HANDOFF = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / "scripts"))
SPEC.loader.exec_module(HANDOFF)


def args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "action": "plan",
        "mode": "ordinary",
        "kind": "model-oauth",
        "profile": "wechatassistant",
        "provider": "openai-codex",
        "hermes": "/opt/hermes/bin/hermes",
        "root": None,
        "ssh_target": None,
        "remote_skill_root": None,
        "remote_python": "python3",
        "remote_service_user": None,
        "remote_account_switch": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TrustedHandoffTests(unittest.TestCase):
    def test_explicit_oauth_route_never_uses_generic_model_wizard(self) -> None:
        command = HANDOFF.build_command(args())
        self.assertEqual(
            command,
            [
                "/opt/hermes/bin/hermes",
                "-p",
                "wechatassistant",
                "auth",
                "add",
                "openai-codex",
                "--type",
                "oauth",
            ],
        )
        self.assertNotIn("model", command)

    def test_api_key_route_uses_hidden_prompt(self) -> None:
        command = HANDOFF.build_command(args(kind="model-api-key", provider="deepseek"))
        self.assertEqual(
            command[-7:],
            ["auth", "add", "deepseek", "--type", "api_key", "--label", "wechatassistant"],
        )
        self.assertFalse(any("key=" in part.lower() for part in command))

    def test_native_handoff_waits_for_prompt_and_never_prints_secret(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            print("boot", flush=True)
            print("Paste your API key:", end="", flush=True)
            value = sys.stdin.readline().strip()
            if value != "TEST-SECRET":
                raise SystemExit(2)
            print('Added deepseek credential #1: "wechatassistant"', flush=True)
            """
        )
        stream = io.StringIO()
        with patch.object(HANDOFF, "_request_native_secret", return_value="TEST-SECRET"), contextlib.redirect_stdout(stream):
            HANDOFF.run_native_api_key_handoff([sys.executable, "-c", script], "deepseek")
        self.assertEqual(stream.getvalue(), "")
        self.assertNotIn("TEST-SECRET", stream.getvalue())

    @unittest.skipIf(os.name == "nt", "PTY is POSIX-only")
    def test_native_handoff_can_satisfy_three_tty_guard_without_echoing_secret(self) -> None:
        script = textwrap.dedent(
            """
            import os
            import sys
            import termios

            if not all(os.isatty(fd) for fd in (0, 1, 2)):
                raise SystemExit(3)
            settings = termios.tcgetattr(0)
            settings[3] &= ~termios.ECHO
            termios.tcsetattr(0, termios.TCSANOW, settings)
            print("Paste your API key:", end="", flush=True)
            value = sys.stdin.readline().strip()
            if value != "TEST-SECRET":
                raise SystemExit(2)
            print('Added deepseek credential #1: "wechatassistant"', flush=True)
            """
        )
        stream = io.StringIO()
        with patch.object(HANDOFF, "_request_native_secret", return_value="TEST-SECRET"), contextlib.redirect_stdout(stream):
            HANDOFF.run_native_api_key_handoff(
                [sys.executable, "-c", script], "deepseek", use_pty=True
            )
        self.assertEqual(stream.getvalue(), "")

    def test_native_handoff_rejects_echo_without_printing_secret(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            print("Paste your API key:", end="", flush=True)
            value = sys.stdin.readline().strip()
            print(value, flush=True)
            print('Added deepseek credential #1: "wechatassistant"', flush=True)
            """
        )
        stream = io.StringIO()
        with patch.object(HANDOFF, "_request_native_secret", return_value="TEST-SECRET"), contextlib.redirect_stdout(stream):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "secret_echo_detected"):
                HANDOFF.run_native_api_key_handoff([sys.executable, "-c", script], "deepseek")
        self.assertEqual(stream.getvalue(), "")

    def test_native_handoff_never_opens_dialog_before_prompt(self) -> None:
        with patch.object(HANDOFF, "_request_native_secret") as request:
            with self.assertRaisesRegex(HANDOFF.HandoffError, "secret_prompt_not_observed"):
                HANDOFF.run_native_api_key_handoff(
                    [sys.executable, "-c", "pass"],
                    "deepseek",
                    prompt_timeout=0.2,
                )
        request.assert_not_called()

    def test_macos_secret_dialog_is_native_hidden_and_returns_input(self) -> None:
        completed = HANDOFF.subprocess.CompletedProcess([], 0, "OK\nTEST-SECRET\n", "")
        with patch.object(HANDOFF.subprocess, "run", return_value=completed) as run:
            self.assertEqual(HANDOFF._request_macos_secret(), "TEST-SECRET")
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("default answer", command[2])
        self.assertIn("with hidden answer", command[2])
        self.assertIn("default button \"确定\"", command[2])
        self.assertNotIn("TEST-SECRET", command[2])
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_macos_secret_dialog_cancel_is_fail_closed(self) -> None:
        completed = HANDOFF.subprocess.CompletedProcess([], 0, "CANCEL\n", "")
        with patch.object(HANDOFF.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "secret_input_cancelled"):
                HANDOFF._request_macos_secret()

    def test_macos_secret_dialog_script_error_is_not_treated_as_input(self) -> None:
        completed = HANDOFF.subprocess.CompletedProcess([], 1, "", "ignored")
        with patch.object(HANDOFF.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "native_secret_dialog_unavailable"):
                HANDOFF._request_macos_secret()

    def test_native_secret_uses_macos_dialog_on_darwin(self) -> None:
        with patch.object(HANDOFF.sys, "platform", "darwin"), patch.object(
            HANDOFF, "_request_macos_secret", return_value="TEST-SECRET"
        ) as macos, patch.object(HANDOFF, "_request_tk_secret") as tk:
            self.assertEqual(HANDOFF._request_native_secret(), "TEST-SECRET")
        macos.assert_called_once_with()
        tk.assert_not_called()

    def test_secret_input_rejects_multiline_and_oversized_values(self) -> None:
        for secret in ("line1\nline2", "line1\rline2", "x" * (HANDOFF.SECRET_INPUT_LIMIT + 1)):
            with self.subTest(secret_length=len(secret)):
                with self.assertRaisesRegex(HANDOFF.HandoffError, "secret_input_invalid"):
                    HANDOFF._validate_secret_input(secret)

    def test_protected_route_uses_isolation_guard(self) -> None:
        command = HANDOFF.build_command(
            args(mode="protected", root="/private/tmp/bwa-root", kind="weixin-setup", provider=None)
        )
        self.assertIn("setup_weixin_direct.py", command[1])
        self.assertIn("run", command)
        self.assertIn("protected", command)
        self.assertNotIn("gateway", command)
        self.assertNotIn("setup", command)

    def test_ordinary_weixin_route_skips_platform_menu(self) -> None:
        command = HANDOFF.build_command(args(kind="weixin-setup", provider=None))
        self.assertIn("setup_weixin_direct.py", command[1])
        self.assertIn("ordinary", command)
        self.assertNotIn("gateway", command)

    def test_cloud_route_uses_interactive_ssh_and_run_cloud(self) -> None:
        command = HANDOFF.build_command(
            args(
                mode="cloud",
                root="/srv/bwa/root",
                hermes="/srv/bwa/bin/hermes",
                ssh_target="bwa-test-host",
                remote_skill_root="/srv/bwa/release/skill",
                remote_service_user="bwatestuser",
                remote_account_switch="root-runuser",
            )
        )
        self.assertEqual(command[:4], ["ssh", "-tt", "--", "bwa-test-host"])
        self.assertIn("run-cloud", command[-1])
        self.assertIn("runuser --login bwatestuser --command", command[-1])
        self.assertIn("auth add openai-codex --type oauth", command[-1])
        self.assertNotIn(" model", command[-1])

    def test_cloud_weixin_route_uses_direct_helper_under_service_account(self) -> None:
        command = HANDOFF.build_command(
            args(
                mode="cloud",
                kind="weixin-setup",
                provider=None,
                root="/srv/bwa/root",
                hermes="/srv/bwa/bin/hermes",
                ssh_target="root@bwa-test-host",
                remote_skill_root="/srv/bwa/release/skill",
                remote_service_user="bwatestuser",
                remote_account_switch="root-runuser",
            )
        )
        self.assertIn("setup_weixin_direct.py", command[-1])
        self.assertIn("--mode cloud", command[-1])
        self.assertNotIn("gateway setup", command[-1])
        self.assertIn("runuser --login bwatestuser --command", command[-1])

    def test_cloud_route_rejects_missing_service_account_switch(self) -> None:
        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.build_command(
                args(
                    mode="cloud",
                    root="/srv/bwa/root",
                    hermes="/srv/bwa/bin/hermes",
                    ssh_target="root@bwa-test-host",
                    remote_skill_root="/srv/bwa/release/skill",
                )
            )

    def test_cloud_direct_route_requires_explicit_matching_ssh_user(self) -> None:
        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.build_command(
                args(
                    mode="cloud",
                    root="/srv/bwa/root",
                    hermes="/srv/bwa/bin/hermes",
                    ssh_target="root@bwa-test-host",
                    remote_skill_root="/srv/bwa/release/skill",
                    remote_service_user="bwatestuser",
                    remote_account_switch="direct",
                )
            )

    def test_cloud_direct_route_accepts_matching_explicit_ssh_user(self) -> None:
        command = HANDOFF.build_command(
            args(
                mode="cloud",
                root="/srv/bwa/root",
                hermes="/srv/bwa/bin/hermes",
                ssh_target="bwatestuser@bwa-test-host",
                remote_skill_root="/srv/bwa/release/skill",
                remote_service_user="bwatestuser",
                remote_account_switch="direct",
            )
        )
        self.assertNotIn("runuser", command[-1])
        self.assertIn("run-cloud", command[-1])

    def test_cloud_sudo_route_switches_to_service_account(self) -> None:
        command = HANDOFF.build_command(
            args(
                mode="cloud",
                root="/srv/bwa/root",
                hermes="/srv/bwa/bin/hermes",
                ssh_target="admin@bwa-test-host",
                remote_skill_root="/srv/bwa/release/skill",
                remote_service_user="bwatestuser",
                remote_account_switch="sudo",
            )
        )
        self.assertIn("sudo --login --user bwatestuser --", command[-1])
        self.assertIn("run-cloud", command[-1])

    def test_cloud_service_account_injection_is_rejected(self) -> None:
        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.build_command(
                args(
                    mode="cloud",
                    root="/srv/bwa/root",
                    hermes="/srv/bwa/bin/hermes",
                    ssh_target="root@bwa-test-host",
                    remote_skill_root="/srv/bwa/release/skill",
                    remote_service_user="bwa;id",
                    remote_account_switch="root-runuser",
                )
            )

    def test_invalid_ssh_target_is_rejected(self) -> None:
        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.build_command(
                args(
                    mode="cloud",
                    root="/srv/bwa/root",
                    hermes="/srv/bwa/bin/hermes",
                    ssh_target="host;touch /tmp/pwned",
                    remote_skill_root="/srv/bwa/release/skill",
                )
            )

    def test_missing_cloud_root_fails_with_redacted_error(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = HANDOFF.main(
                [
                    "plan",
                    "--mode",
                    "cloud",
                    "--kind",
                    "weixin-setup",
                    "--profile",
                    "wechatassistant",
                    "--hermes",
                    "/srv/bwa/bin/hermes",
                    "--ssh-target",
                    "bwa-test-host",
                    "--remote-skill-root",
                    "/srv/bwa/release/skill",
                    "--remote-service-user",
                    "bwatestuser",
                    "--remote-account-switch",
                    "root-runuser",
                ]
            )
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["result"], "ERROR")
        self.assertEqual(payload["error"], "root_invalid")
        self.assertNotIn("/srv/bwa", stream.getvalue())

    def test_plan_output_contains_no_command_or_paths(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = HANDOFF.main(
                [
                    "plan",
                    "--mode",
                    "ordinary",
                    "--kind",
                    "model-oauth",
                    "--profile",
                    "wechatassistant",
                    "--provider",
                    "openai-codex",
                    "--hermes",
                    "/opt/hermes/bin/hermes",
                ]
            )
        output = stream.getvalue()
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "READY")
        self.assertFalse(payload["user_terminal_typing_required"])
        self.assertNotIn("openai-codex", output)
        self.assertNotIn("/opt/hermes", output)

    def test_launch_reports_only_opened_boolean_contract(self) -> None:
        stream = io.StringIO()
        with patch.object(HANDOFF, "launch_in_terminal") as launch, contextlib.redirect_stdout(stream):
            code = HANDOFF.main(
                [
                    "launch",
                    "--mode",
                    "ordinary",
                    "--kind",
                    "weixin-setup",
                    "--profile",
                    "wechatassistant",
                    "--hermes",
                    "/opt/hermes/bin/hermes",
                ]
            )
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "OPENED")
        self.assertTrue(payload["trusted_terminal"])
        self.assertFalse(payload["command_printed"])
        launch.assert_called_once()

    def test_cloud_api_key_launch_uses_native_dialog_and_reports_saved(self) -> None:
        stream = io.StringIO()
        with patch.object(HANDOFF, "run_native_api_key_handoff") as handoff, patch.object(
            HANDOFF, "launch_in_terminal"
        ) as terminal, contextlib.redirect_stdout(stream):
            code = HANDOFF.main(
                [
                    "launch",
                    "--mode",
                    "cloud",
                    "--kind",
                    "model-api-key",
                    "--profile",
                    "wechatassistant",
                    "--provider",
                    "deepseek",
                    "--hermes",
                    "/srv/bwa/bin/hermes",
                    "--root",
                    "/srv/bwa/root",
                    "--ssh-target",
                    "root@bwa-test-host",
                    "--remote-skill-root",
                    "/srv/bwa/release/skill",
                    "--remote-service-user",
                    "bwatestuser",
                    "--remote-account-switch",
                    "root-runuser",
                ]
            )
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "SAVED")
        self.assertTrue(payload["native_secret_dialog"])
        self.assertTrue(payload["credential_saved"])
        self.assertFalse(payload["secrets_printed"])
        handoff.assert_called_once_with(
            ANY, "deepseek", use_pty=False
        )
        terminal.assert_not_called()

    def test_protected_api_key_launch_uses_native_dialog_and_reports_saved(self) -> None:
        stream = io.StringIO()
        with patch.object(HANDOFF, "run_native_api_key_handoff") as handoff, patch.object(
            HANDOFF, "launch_in_terminal"
        ) as terminal, contextlib.redirect_stdout(stream):
            code = HANDOFF.main(
                [
                    "launch",
                    "--mode",
                    "protected",
                    "--kind",
                    "model-api-key",
                    "--profile",
                    "wechatassistant",
                    "--provider",
                    "deepseek",
                    "--hermes",
                    "/opt/hermes/bin/hermes",
                    "--root",
                    "/private/tmp/bwa-hermes-root",
                ]
            )
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "SAVED")
        self.assertTrue(payload["native_secret_dialog"])
        self.assertTrue(payload["credential_saved"])
        self.assertFalse(payload["secrets_printed"])
        handoff.assert_called_once_with(
            ANY, "deepseek", use_pty=os.name != "nt"
        )
        terminal.assert_not_called()

    def test_windows_console_keeps_qr_output_visible(self) -> None:
        with patch.object(HANDOFF.subprocess, "Popen") as popen:
            HANDOFF._launch_windows(["hermes", "weixin"])
        kwargs = popen.call_args.kwargs
        self.assertNotIn("stdin", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        self.assertTrue(kwargs["close_fds"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

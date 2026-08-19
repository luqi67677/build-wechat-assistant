#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "apply_chat_safety_baseline.py"
SPEC = importlib.util.spec_from_file_location("apply_chat_safety_baseline", MODULE_PATH)
assert SPEC and SPEC.loader
BASELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASELINE)


class ApplyChatSafetyBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.profile = self.root / "profiles" / "wechatassistant"
        self.profile.mkdir(parents=True, mode=0o700)
        self.config = self.profile / "config.yaml"
        self.env = self.profile / ".env"
        self.config.write_text("{}\n", encoding="utf-8")
        self.env.write_text("", encoding="utf-8")
        self.config.chmod(0o600)
        self.env.chmod(0o600)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_run(self, command: list[str]):
        self.calls.append(command)
        if command[-4:-2] == ["tools", "list"]:
            platform = command[-1]
            output = (
                f"Built-in toolsets ({platform}):\n"
                "  ✓ enabled  terminal  Terminal\n"
                "  ✓ enabled  web  Web\n"
                "  ✓ enabled  clarify  Clarify\n"
            )
            return BASELINE.subprocess.CompletedProcess(command, 0, output, "")
        return BASELINE.subprocess.CompletedProcess(command, 0, "ok\n", "")

    def run_main(self) -> tuple[int, dict, str]:
        stream = io.StringIO()
        safe_checks = {"model_and_qr_boundary_safe": True}
        with (
            patch.object(BASELINE, "_run", side_effect=self.fake_run),
            patch.object(BASELINE, "_profile_binding_valid", return_value=True),
            patch.object(BASELINE, "workspace_is_safe", return_value=True),
            patch.object(BASELINE, "evaluate", return_value=safe_checks),
            contextlib.redirect_stdout(stream),
        ):
            code = BASELINE.main(
                [
                    "--profile", "wechatassistant",
                    "--hermes", "/fake/hermes",
                    "--expected-hermes-root", str(self.root),
                    "--workspace", str(self.workspace),
                ]
            )
        output = stream.getvalue()
        return code, json.loads(output), output

    def test_applies_exact_baseline_to_cli_and_weixin_then_rechecks(self) -> None:
        code, payload, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        for platform in ("cli", "weixin"):
            disable = [
                call for call in self.calls
                if "tools" in call and "disable" in call and platform in call
            ]
            self.assertEqual(len(disable), 1)
            self.assertIn("terminal", disable[0])
            self.assertIn("web", disable[0])
            self.assertNotIn("clarify", disable[0][disable[0].index("disable") + 1 :])
            self.assertIn(
                ["/fake/hermes", "-p", "wechatassistant", "tools", "enable", "--platform", platform, "clarify"],
                self.calls,
            )
        configured = {call[-2]: call[-1] for call in self.calls if call[-4:-2] == ["config", "set"]}
        self.assertEqual(configured["terminal.cwd"], str(self.workspace))
        self.assertEqual(configured["display.language"], "zh")
        self.assertEqual(configured["display.show_reasoning"], "false")
        self.assertEqual(configured["display.platforms.weixin.show_reasoning"], "false")
        self.assertEqual(configured["display.tool_progress"], "off")
        self.assertEqual(configured["display.platforms.weixin.tool_progress"], "off")
        self.assertEqual(configured["display.interim_assistant_messages"], "false")
        self.assertEqual(configured["display.platforms.weixin.interim_assistant_messages"], "false")
        self.assertEqual(configured["display.long_running_notifications"], "false")
        self.assertEqual(configured["display.platforms.weixin.long_running_notifications"], "false")
        self.assertEqual(configured["display.busy_ack_detail"], "false")
        self.assertEqual(configured["display.platforms.weixin.busy_ack_detail"], "false")
        self.assertEqual(configured["display.background_process_notifications"], "off")
        self.assertEqual(configured["session_reset.notify"], "false")
        self.assertEqual(configured["memory.memory_enabled"], "false")
        self.assertEqual(configured["memory.user_profile_enabled"], "false")
        self.assertNotIn(str(self.workspace), output)

    def test_mcp_presence_fails_before_any_mutation(self) -> None:
        original = self.fake_run

        def with_mcp(command: list[str]):
            result = original(command)
            if command[-4:] == ["tools", "list", "--platform", "cli"]:
                result.stdout += "MCP servers:\n  example  enabled\n"
            return result

        stream = io.StringIO()
        with (
            patch.object(BASELINE, "_run", side_effect=with_mcp),
            patch.object(BASELINE, "_profile_binding_valid", return_value=True),
            patch.object(BASELINE, "workspace_is_safe", return_value=True),
            contextlib.redirect_stdout(stream),
        ):
            code = BASELINE.main(
                ["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.root), "--workspace", str(self.workspace)]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())["error"], "mcp_present")
        self.assertFalse(any("disable" in call or "config" in call for call in self.calls))

    def test_invalid_binding_or_workspace_fails_without_mutation(self) -> None:
        for binding, workspace_safe, error in (
            (False, True, "profile_path_mismatch"),
            (True, False, "workspace_unsafe"),
        ):
            with self.subTest(error=error):
                self.calls.clear()
                stream = io.StringIO()
                with (
                    patch.object(BASELINE, "_profile_binding_valid", return_value=binding),
                    patch.object(BASELINE, "workspace_is_safe", return_value=workspace_safe),
                    contextlib.redirect_stdout(stream),
                ):
                    code = BASELINE.main(
                        ["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.root), "--workspace", str(self.workspace)]
                    )
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(stream.getvalue())["error"], error)
                self.assertEqual(self.calls, [])

    def test_fresh_profile_binding_allows_config_to_be_created_by_baseline(self) -> None:
        self.config.unlink()

        def paths(command: list[str]):
            if command[1:] == ["profile", "show", "wechatassistant"]:
                return BASELINE.subprocess.CompletedProcess(command, 0, "profile ok\n", "")
            if command[-2:] == ["config", "path"]:
                return BASELINE.subprocess.CompletedProcess(command, 0, f"{self.config}\n", "")
            if command[-2:] == ["config", "env-path"]:
                return BASELINE.subprocess.CompletedProcess(command, 0, f"{self.env}\n", "")
            raise AssertionError(command)

        with patch.object(BASELINE, "_run", side_effect=paths):
            self.assertTrue(
                BASELINE._profile_binding_valid("wechatassistant", "/fake/hermes", self.root)
            )

    @unittest.skipIf(os.name == "nt", "POSIX 权限回归")
    def test_runtime_cache_permissions_are_hardened_after_model_probe(self) -> None:
        cache = self.profile / "cache"
        cache.mkdir(mode=0o755)
        probe = cache / "local_endpoint_probes.json"
        probe.write_text("{}\n", encoding="utf-8")
        probe.chmod(0o644)

        BASELINE._harden_profile_runtime_permissions("wechatassistant", self.root)

        self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
        self.assertEqual(probe.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX 权限回归")
    def test_runtime_cache_symlink_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.profile / "cache").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(BASELINE.BaselineError, "runtime_permission_target_unsafe"):
            BASELINE._harden_profile_runtime_permissions("wechatassistant", self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)

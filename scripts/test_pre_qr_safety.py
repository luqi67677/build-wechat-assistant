#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_pre_qr_safety.py"
SPEC = importlib.util.spec_from_file_location("check_pre_qr_safety", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
CANARY = "pre-qr-secret-canary-never-print"


class PreQrSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        root = Path(self.temp.name)
        root.chmod(0o700)
        self.profile_dir = root / "profiles" / "wechatassistant"
        self.profile_dir.mkdir(parents=True, mode=0o700)
        self.config = self.profile_dir / "config.yaml"
        self.env = self.profile_dir / ".env"
        self.config.write_text("{}\n", encoding="utf-8")
        self.env.write_text(f"TOKEN={CANARY}\n", encoding="utf-8")
        self.config.chmod(0o600)
        self.env.chmod(0o600)
        self.workspace = root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.values = {
            "platform_toolsets.weixin": ["clarify", "kanban"],
            "toolsets": ["hermes-cli"],
            "approvals.mode": "smart",
            "display.language": "zh",
            "display.show_reasoning": False,
            "display.platforms.weixin.show_reasoning": False,
            "display.tool_progress": "off",
            "display.platforms.weixin.tool_progress": "off",
            "display.interim_assistant_messages": False,
            "display.platforms.weixin.interim_assistant_messages": False,
            "display.long_running_notifications": False,
            "display.platforms.weixin.long_running_notifications": False,
            "display.busy_ack_detail": False,
            "display.platforms.weixin.busy_ack_detail": False,
            "display.background_process_notifications": "off",
            "session_reset.notify": False,
            "memory.memory_enabled": False,
            "memory.user_profile_enabled": False,
            "terminal.cwd": str(self.workspace),
        }
        self.tool_outputs = {
            "cli": (
                "Built-in toolsets (cli):\n"
                "  ✗ disabled  terminal  Terminal\n"
                "  ✗ disabled  skills  Skills\n"
                "  ✓ enabled  clarify  Clarifying Questions\n"
            ),
            "weixin": (
            "Built-in toolsets (weixin):\n"
            "  ✗ disabled  terminal  Terminal\n"
            "  ✗ disabled  skills  Skills\n"
            "  ✓ enabled  clarify  Clarifying Questions\n"
            ),
        }
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_run(self, command: list[str], **_: object):
        self.calls.append(command)
        if command[1:] == ["profile", "show", "wechatassistant"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, "profile ok\n", "")
        if command[-2:] == ["config", "path"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, f"{self.config}\n", "")
        if command[-2:] == ["config", "env-path"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, f"{self.env}\n", "")
        if len(command) >= 4 and command[-4:-2] == ["tools", "list"] and command[-2] == "--platform":
            return CHECKER.subprocess.CompletedProcess(command, 0, self.tool_outputs[command[-1]], "")
        if command[-2:] == ["gateway", "status"]:
            return CHECKER.subprocess.CompletedProcess(command, 0, "Gateway is not running\n", "")
        if len(command) >= 4 and command[-4:-2] == ["config", "get"] and command[-1] == "--json":
            key = command[-2]
            return CHECKER.subprocess.CompletedProcess(command, 0, json.dumps(self.values[key]), "")
        raise AssertionError(command)

    def run_main(self) -> tuple[int, dict, str]:
        stream = io.StringIO()
        with patch.object(CHECKER.subprocess, "run", side_effect=self.fake_run), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.profile_dir.parent.parent)])
        output = stream.getvalue()
        return code, json.loads(output), output

    def test_safe_chat_only_profile_passes_without_leaking_values(self) -> None:
        code, payload, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        for forbidden in (CANARY, str(self.workspace), str(self.profile_dir)):
            self.assertNotIn(forbidden, output)
        self.assertIn(["/fake/hermes", "profile", "show", "wechatassistant"], self.calls)
        self.assertNotIn(["/fake/hermes", "-p", "wechatassistant", "profile", "show"], self.calls)
        self.assertFalse(any("mcp_servers" in call for call in self.calls))

    def test_profile_path_must_match_requested_profile_name(self) -> None:
        wrong = self.profile_dir.parent / "default"
        wrong.mkdir(mode=0o700)
        wrong_config = wrong / "config.yaml"
        wrong_env = wrong / ".env"
        wrong_config.write_text("{}\n", encoding="utf-8")
        wrong_env.write_text("", encoding="utf-8")
        wrong_config.chmod(0o600)
        wrong_env.chmod(0o600)
        self.config = wrong_config
        self.env = wrong_env
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["profile_cli_contract_valid"])

    def test_existing_weixin_state_is_rejected_before_qr(self) -> None:
        self.env.write_text(f"MODEL_TOKEN={CANARY}\nWEIXIN_TOKEN=old-secret\n", encoding="utf-8")
        self.env.chmod(0o600)
        code, payload, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_state_absent_before_qr"])
        self.assertNotIn("old-secret", output)

    def test_unknown_future_process_weixin_key_is_rejected(self) -> None:
        with patch.dict(os.environ, {"WEIXIN_FUTURE_OVERRIDE": "canary"}, clear=False):
            code, payload, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["process_weixin_overrides_absent_before_qr"])
        self.assertNotIn("canary", output)

    def test_unsafe_default_tools_are_rejected(self) -> None:
        self.tool_outputs["weixin"] += "  ✓ enabled  web  Web Search\n"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_only_clarify_enabled"])

    def test_unknown_enabled_plugin_is_rejected(self) -> None:
        self.tool_outputs["weixin"] += "\nPlugin toolsets (weixin):\n  ✓ enabled  surprise  Surprise\n"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_only_clarify_enabled"])

    def test_unparseable_tool_inventory_fails_closed(self) -> None:
        self.tool_outputs["weixin"] += "  maybe  mystery  Unknown format\n"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_tool_inventory_complete"])

    def test_mcp_server_section_is_rejected(self) -> None:
        self.tool_outputs["weixin"] += "\nMCP servers:\n  example  all tools enabled\n"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_mcp_servers_absent"])

    def test_nonminimal_hidden_platform_toolset_is_rejected(self) -> None:
        self.values["platform_toolsets.weixin"] = ["clarify", "kanban", "hidden_write"]
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_platform_toolsets_minimal"])

    def test_kanban_top_level_or_dispatch_environment_is_rejected(self) -> None:
        self.values["toolsets"] = ["kanban"]
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["kanban_runtime_disabled"])
        self.values["toolsets"] = ["hermes-cli"]
        with patch.dict(os.environ, {"HERMES_KANBAN_TASK": "canary"}, clear=False):
            code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["kanban_runtime_disabled"])

    def test_off_approval_or_visible_reasoning_is_rejected(self) -> None:
        self.values["approvals.mode"] = "off"
        self.values["display.platforms.weixin.show_reasoning"] = True
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["approval_mode_safe"])
        self.assertFalse(payload["checks"]["weixin_reasoning_disabled"])

    def test_cli_tool_surface_is_also_exactly_minimal(self) -> None:
        self.tool_outputs["cli"] += "  ✓ enabled  terminal  Terminal\n"
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["cli_only_clarify_enabled"])

    def test_global_reasoning_must_be_disabled_before_model_probe(self) -> None:
        self.values["display.show_reasoning"] = True
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["cli_reasoning_disabled"])

    def test_english_or_noisy_chat_settings_are_rejected(self) -> None:
        cases = {
            "display.language": ("en", "simplified_chinese_selected"),
            "display.tool_progress": ("all", "cli_tool_progress_disabled"),
            "display.platforms.weixin.tool_progress": ("all", "weixin_tool_progress_disabled"),
            "display.interim_assistant_messages": (True, "cli_interim_messages_disabled"),
            "display.platforms.weixin.interim_assistant_messages": (True, "weixin_interim_messages_disabled"),
            "display.long_running_notifications": (True, "cli_long_running_notifications_disabled"),
            "display.platforms.weixin.long_running_notifications": (True, "weixin_long_running_notifications_disabled"),
            "display.busy_ack_detail": (True, "cli_busy_ack_detail_disabled"),
            "display.platforms.weixin.busy_ack_detail": (True, "weixin_busy_ack_detail_disabled"),
            "display.background_process_notifications": ("all", "background_process_notifications_disabled"),
            "session_reset.notify": (True, "session_reset_notifications_disabled"),
        }
        for key, (unsafe_value, failed_check) in cases.items():
            with self.subTest(key=key):
                original = self.values[key]
                self.values[key] = unsafe_value
                code, payload, _ = self.run_main()
                self.values[key] = original
                self.assertEqual(code, 1)
                self.assertFalse(payload["checks"][failed_check])

    def test_memory_injection_is_rejected(self) -> None:
        self.values["memory.memory_enabled"] = True
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["builtin_memory_disabled"])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits do not represent Windows ACLs")
    def test_model_and_profile_secret_stores_must_be_private(self) -> None:
        shared = self.profile_dir.parent.parent / "shared"
        shared.mkdir(mode=0o700)
        auth = shared / "nous_auth.json"
        auth.write_text("secret-canary", encoding="utf-8")
        auth.chmod(0o640)
        code, payload, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["model_and_profile_secret_stores_private"])
        self.assertNotIn("secret-canary", output)
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o640)

    def test_public_or_documents_workspace_is_rejected(self) -> None:
        self.workspace.chmod(0o755)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["dedicated_workspace_private"])
        self.workspace.chmod(0o700)
        documents = Path.home() / "Documents" / "bwa-pre-qr-forbidden"
        self.values["terminal.cwd"] = str(documents)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["dedicated_workspace_private"])

    def test_running_gateway_is_rejected(self) -> None:
        original = self.fake_run

        def running(command: list[str], **kwargs: object):
            if command[-2:] == ["gateway", "status"]:
                return CHECKER.subprocess.CompletedProcess(command, 0, "Gateway is running\n", "")
            return original(command, **kwargs)

        stream = io.StringIO()
        with patch.object(CHECKER.subprocess, "run", side_effect=running), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.profile_dir.parent.parent)])
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["service_absent_and_gateway_stopped_before_qr"])

    def test_installed_but_stopped_service_is_rejected(self) -> None:
        original = self.fake_run

        def installed(command: list[str], **kwargs: object):
            if command[-2:] == ["gateway", "status"]:
                return CHECKER.subprocess.CompletedProcess(command, 0, "User gateway service is stopped\n", "")
            return original(command, **kwargs)

        stream = io.StringIO()
        with patch.object(CHECKER.subprocess, "run", side_effect=installed), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.profile_dir.parent.parent)])
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["service_absent_and_gateway_stopped_before_qr"])

    def test_approved_root_must_match_resolved_profile(self) -> None:
        other_root = self.profile_dir.parent.parent / "other-root"
        other_root.mkdir(mode=0o700)
        stream = io.StringIO()
        with patch.object(CHECKER.subprocess, "run", side_effect=self.fake_run), contextlib.redirect_stdout(stream):
            code = CHECKER.main(["--profile", "wechatassistant", "--hermes", "/fake/hermes", "--expected-hermes-root", str(other_root)])
        payload = json.loads(stream.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["profile_cli_contract_valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

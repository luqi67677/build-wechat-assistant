#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from launch_feishu_oauth import FeishuOAuthError, _official_url, authorize, initialize_application


class LaunchFeishuOAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "home"
        self.hermes = base / "hermes"
        self.home.mkdir()
        self.hermes.mkdir()
        self.cli = base / "lark-cli"
        self.cli.write_text("#!/bin/sh\n", encoding="utf-8")
        self.cli.chmod(0o700)
        self.node = base / "node"
        self.node.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then printf 'v22.0.0\\n'; exit 0; fi\n"
            "if [ \"$1\" = -e ]; then printf '{\"release\":\"node\",\"version\":\"22.0.0\",\"execPath\":\"%s\",\"v8\":\"12.0\"}\\n' \"$0\"; exit 0; fi\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        self.node.chmod(0o700)
        self.scopes = ("docx:document:create", "wiki:node:create")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_authorization_opens_official_url_and_hides_device_code(self) -> None:
        runner = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps({
                "device_code": "secret-device-code",
                "verification_url": "https://open.feishu.cn/device",
            }), ""),
            subprocess.CompletedProcess([], 0, '{"ok":true}', ""),
        ])
        opener = Mock(return_value=True)
        result = authorize(self.node, self.cli, self.home, self.hermes, "assistant-test", self.scopes,
                           opener=opener, runner=runner)
        self.assertEqual(result["result"], "AUTHORIZED")
        self.assertFalse(result["secrets_printed"])
        opener.assert_called_once_with("https://open.feishu.cn/device")
        self.assertNotIn("secret-device-code", json.dumps(result))

    def test_dedicated_app_creation_is_isolated_and_hides_cli_output(self) -> None:
        process = Mock()
        process.stdout = io.StringIO("https://open.feishu.cn/page/cli?secret=do-not-print\n")
        process.stderr = io.StringIO("")
        process.poll.return_value = 0
        process.returncode = 0
        popen = Mock(return_value=process)
        opener = Mock(return_value=True)
        result = initialize_application(
            self.node, self.cli, self.home, self.hermes, "assistant-feishu-test",
            opener=opener, popen=popen,
        )
        self.assertEqual(result, {
            "result": "APPLICATION_READY",
            "profile": "assistant-feishu-test",
            "secrets_printed": False,
        })
        command = popen.call_args.args[0]
        self.assertEqual(command, [
            str(self.node.resolve()), str(self.cli), "config", "init", "--new", "--name", "assistant-feishu-test",
            "--brand", "feishu", "--lang", "zh", "--force-init",
        ])
        self.assertEqual(popen.call_args.kwargs["env"]["HOME"], str(self.home))
        opener.assert_called_once_with("https://open.feishu.cn/page/cli?secret=do-not-print")
        self.assertNotIn("secret=do-not-print", json.dumps(result))

    def test_dedicated_app_creation_failure_is_actionable_without_raw_output(self) -> None:
        process = Mock()
        process.stdout = io.StringIO("")
        process.stderr = io.StringIO("app_secret=do-not-print\n")
        process.poll.return_value = 1
        process.returncode = 1
        popen = Mock(return_value=process)
        with self.assertRaisesRegex(FeishuOAuthError, "没有创建完成") as raised:
            initialize_application(
                self.node, self.cli, self.home, self.hermes, "assistant-feishu-test", popen=popen,
            )
        self.assertNotIn("app_secret", str(raised.exception))

    def test_permission_console_is_opened_without_returning_url(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, "", json.dumps({
            "error": {"type": "missing_scope", "console_url": "https://open.feishu.cn/app/demo/auth"}
        })))
        result = authorize(self.node, self.cli, self.home, self.hermes, "assistant-test", self.scopes,
                           opener=Mock(return_value=True), runner=runner)
        self.assertEqual(result, {
            "result": "APP_PERMISSION_CONFIRMATION_REQUIRED",
            "secrets_printed": False,
        })

    def test_lookalike_permission_host_is_rejected(self) -> None:
        self.assertIsNone(_official_url("https://open.feishu.cn.attacker.invalid/app"))

    def test_missing_device_data_fails_closed(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', ""))
        with self.assertRaisesRegex(FeishuOAuthError, "授权会话"):
            authorize(self.node, self.cli, self.home, self.hermes, "assistant-test", self.scopes,
                      opener=Mock(return_value=True), runner=runner)


if __name__ == "__main__":
    unittest.main()

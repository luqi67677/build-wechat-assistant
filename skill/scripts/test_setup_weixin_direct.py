#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "setup_weixin_direct.py"
SPEC = importlib.util.spec_from_file_location("setup_weixin_direct", MODULE_PATH)
assert SPEC and SPEC.loader
DIRECT = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / "scripts"))
SPEC.loader.exec_module(DIRECT)


class DirectWeixinSetupTests(unittest.TestCase):
    def test_safe_configuration_is_fixed_to_owner_only_and_groups_off(self) -> None:
        saved: dict[str, str] = {}
        credentials = {
            "account_id": "bot-account",
            "token": "secret-token",
            "base_url": DIRECT.OFFICIAL_BASE_URL,
            "user_id": "owner-user",
        }
        DIRECT.save_safe_configuration(credentials, saved.__setitem__)
        self.assertEqual(saved["WEIXIN_DM_POLICY"], "allowlist")
        self.assertEqual(saved["WEIXIN_ALLOWED_USERS"], "owner-user")
        self.assertEqual(saved["WEIXIN_HOME_CHANNEL"], "owner-user")
        self.assertEqual(saved["WEIXIN_ALLOW_ALL_USERS"], "false")
        self.assertEqual(saved["WEIXIN_GROUP_POLICY"], "disabled")
        self.assertEqual(saved["WEIXIN_GROUP_ALLOWED_USERS"], "")
        self.assertEqual(set(saved), set(DIRECT.WEIXIN_KEYS))

    @unittest.skipIf(os.name == "nt", "Windows permissions use ACL checks")
    def test_weixin_runtime_store_is_rehardened_after_qr(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile = Path(raw) / "wechatassistant"
            accounts = profile / "weixin" / "accounts"
            accounts.mkdir(parents=True)
            token = accounts / "context-token.json"
            token.write_text("{}", encoding="utf-8")
            (profile / "weixin").chmod(0o755)
            accounts.chmod(0o755)
            token.chmod(0o644)

            DIRECT.reharden_weixin_store(profile)

            self.assertEqual((profile / "weixin").stat().st_mode & 0o777, 0o700)
            self.assertEqual(accounts.stat().st_mode & 0o777, 0o700)
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "Windows permissions use ACL checks")
    def test_weixin_runtime_store_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile = Path(raw) / "wechatassistant"
            store = profile / "weixin"
            store.mkdir(parents=True)
            target = profile / "outside"
            target.write_text("secret", encoding="utf-8")
            (store / "linked").symlink_to(target)
            with self.assertRaisesRegex(DIRECT.SetupError, "weixin_store_unsafe"):
                DIRECT.reharden_weixin_store(profile)

    def test_incomplete_or_unexpected_credentials_fail_closed(self) -> None:
        for credentials, error in (
            ({"account_id": "a", "token": "t", "user_id": ""}, "weixin_login_incomplete"),
            (
                {
                    "account_id": "a",
                    "token": "t",
                    "user_id": "u",
                    "base_url": "https://example.invalid",
                },
                "weixin_endpoint_unexpected",
            ),
        ):
            with self.subTest(credentials=credentials), self.assertRaisesRegex(DIRECT.SetupError, error):
                DIRECT.validate_credentials(credentials)

    def test_terminal_filter_hides_url_and_account_id_but_keeps_qr_rows(self) -> None:
        target = io.StringIO()
        writer = DIRECT.RedactingWriter(target)
        writer.write("https://liteapp.example/temporary\n")
        writer.write("请打开 https://liteapp.example/another 完成授权\n")
        writer.write("██  ██\n")
        writer.write("微信连接成功，account_id=secret-id\n")
        output = target.getvalue()
        self.assertNotIn("https://", output)
        self.assertNotIn("secret-id", output)
        self.assertIn("██  ██", output)
        self.assertIn("微信扫码确认成功", output)

    def test_runtime_python_is_resolved_from_official_posix_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            runtime = directory / "python"
            runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            runtime.chmod(0o700)
            hermes_script = directory / "hermes-entry"
            launcher = directory / "hermes"
            launcher.write_text(
                f'#!/usr/bin/env bash\nexec "{runtime}" "{hermes_script}" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o700)
            self.assertEqual(DIRECT.resolve_runtime_python(launcher), runtime.resolve())

    def test_venv_python_symlink_keeps_virtual_environment_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            target = directory / "system-python"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o700)
            venv = directory / "venv"
            runtime = venv / "bin" / "python"
            runtime.parent.mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
            runtime.symlink_to(target)
            launcher = directory / "hermes"
            launcher.write_text(
                f'#!/usr/bin/env bash\nexec "{runtime}" "{directory / "hermes-entry"}" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o700)
            resolved = DIRECT.resolve_runtime_python(launcher)
            self.assertEqual(resolved, runtime.parent.resolve() / runtime.name)
            self.assertNotEqual(resolved, target.resolve())

    def test_runtime_python_symlink_without_venv_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            target = directory / "system-python"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o700)
            runtime = directory / "bin" / "python"
            runtime.parent.mkdir()
            runtime.symlink_to(target)
            launcher = directory / "hermes"
            launcher.write_text(
                f'#!/usr/bin/env bash\nexec "{runtime}" "{directory / "hermes-entry"}" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o700)
            with self.assertRaisesRegex(DIRECT.SetupError, "hermes_runtime_python_unresolved"):
                DIRECT.resolve_runtime_python(launcher)

    def test_run_refuses_non_tty_before_starting_qr(self) -> None:
        args = [
            "run",
            "--mode",
            "ordinary",
            "--profile",
            "wechatassistant",
            "--hermes",
            "/opt/hermes/bin/hermes",
        ]
        with patch.object(DIRECT, "resolve_profile") as resolve, patch.object(
            DIRECT.guard, "trusted_tty_available", return_value=False
        ), patch("sys.stdout", new=io.StringIO()):
            code = DIRECT.main(args)
        self.assertEqual(code, 2)
        resolve.assert_not_called()

    def test_windows_runtime_python_uses_sibling_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            launcher = directory / "hermes.exe"
            runtime = directory / "python.exe"
            launcher.write_bytes(b"MZ")
            runtime.write_bytes(b"MZ")
            with patch.object(DIRECT.os, "name", "nt"), patch.object(DIRECT.os, "access", return_value=True):
                self.assertEqual(DIRECT.resolve_runtime_python(launcher), runtime.resolve())


if __name__ == "__main__":
    unittest.main(verbosity=2)

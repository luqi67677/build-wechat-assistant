#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "isolation_guard.py"
SPEC = importlib.util.spec_from_file_location("isolation_guard", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class IsolationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "isolated-hermes"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_root_is_exclusive_private_and_inode_bound(self) -> None:
        checks = GUARD.create_root(str(self.root))
        self.assertTrue(all(checks.values()))
        with self.assertRaisesRegex(GUARD.IsolationError, "root_must_not_exist"):
            GUARD.create_root(str(self.root))
        replacement = self.parent / "replacement"
        replacement.mkdir(mode=0o700)
        marker = self.root / GUARD.MARKER_NAME
        copied = replacement / GUARD.MARKER_NAME
        copied.write_bytes(marker.read_bytes())
        copied.chmod(0o600)
        (replacement / "os-home").mkdir(mode=0o700)
        (replacement / "shared").mkdir(mode=0o700)
        self.assertFalse(GUARD.root_checks(replacement)["isolated_root_marker_binds_current_directory"])

    def test_root_purpose_confines_local_tests_and_cloud_service_home(self) -> None:
        with patch.object(GUARD, "_temporary_roots", return_value=(self.parent / "other",)):
            with self.assertRaisesRegex(GUARD.IsolationError, "local_test_root_outside_temporary_scope"):
                GUARD.create_root(str(self.root), purpose="local-test")
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            checks = GUARD.create_root(str(self.root), purpose="cloud-service")
        self.assertTrue(all(checks.values()))
        self.assertEqual(GUARD.root_purpose(self.root), "cloud-service")
        nested = self.parent / "nested"
        nested.mkdir(mode=0o700)
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            with self.assertRaisesRegex(GUARD.IsolationError, "cloud_root_must_be_direct_child_of_service_home"):
                GUARD.create_root(str(nested / "root"), purpose="cloud-service")

    def test_local_persistent_root_uses_private_application_scope_and_survives_runner_reuse(self) -> None:
        scope = self.parent / "persistent-scope"
        persistent = scope / "assistant-test"
        with patch.object(GUARD, "_local_persistent_scope", return_value=scope):
            checks = GUARD.create_root(str(persistent), purpose="local-persistent")
            self.assertTrue(all(checks.values()))
            self.assertEqual(GUARD.root_purpose(persistent), "local-persistent")
            GUARD.validate_interactive_root(persistent, "run")
            self.assertEqual(
                GUARD.interactive_environment_for_root(persistent, str(self._fake_launcher()))["HERMES_HOME"],
                str(persistent),
            )
        self.assertTrue(persistent.is_dir())

    def test_local_persistent_root_rejects_arbitrary_parent_or_unsafe_scope(self) -> None:
        scope = self.parent / "persistent-scope"
        with patch.object(GUARD, "_local_persistent_scope", return_value=scope):
            with self.assertRaisesRegex(
                GUARD.IsolationError, "local_persistent_root_outside_private_scope"
            ):
                GUARD.create_root(str(self.root), purpose="local-persistent")
            scope.mkdir(mode=0o755)
            scope.chmod(0o755)
            with self.assertRaisesRegex(GUARD.IsolationError, "local_persistent_scope_unsafe"):
                GUARD.create_root(str(scope / "assistant-test"), purpose="local-persistent")

    def test_clean_environment_drops_existing_assistant_and_model_secrets(self) -> None:
        GUARD.create_root(str(self.root))
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        with patch.dict(
            os.environ,
            {
                "HERMES_HOME": "/production/hermes",
                "HERMES_SHARED_AUTH_DIR": "/production/shared",
                "OPENAI_API_KEY": "secret",
                "KIMI_API_KEY": "secret",
                "WEIXIN_TOKEN": "secret",
                "LANG": "zh_CN.UTF-8",
            },
            clear=False,
        ):
            env = GUARD.isolated_environment(self.root, str(fake))
        self.assertEqual(env["HERMES_HOME"], str(self.root))
        self.assertEqual(env["HERMES_SHARED_AUTH_DIR"], str(self.root / "shared"))
        self.assertEqual(env["HOME"], str(self.root / "os-home"))
        for key in ("OPENAI_API_KEY", "KIMI_API_KEY", "WEIXIN_TOKEN"):
            self.assertNotIn(key, env)
        self.assertEqual(env["LANG"], "zh_CN.UTF-8")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def _fake_launcher(self) -> Path:
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        return fake

    def test_service_environment_keeps_real_home_but_drops_secrets(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "_service_account_home", return_value=self.parent
        ), patch.object(
            GUARD,
            "_service_runtime_environment",
            return_value={
                "XDG_RUNTIME_DIR": "/run/user/test",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/test/bus",
            },
        ), patch.dict(
            os.environ,
            {
                "HERMES_HOME": "/production/hermes",
                "OPENAI_API_KEY": "secret",
                "WEIXIN_TOKEN": "secret",
                "XDG_CONFIG_HOME": "/production/xdg",
            },
            clear=False,
        ):
            env = GUARD.service_environment(self.root, str(fake))
        self.assertEqual(env["HOME"], str(self.parent))
        self.assertEqual(env["HERMES_HOME"], str(self.root))
        self.assertEqual(env["HERMES_SHARED_AUTH_DIR"], str(self.root / "shared"))
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/test")
        for key in ("OPENAI_API_KEY", "WEIXIN_TOKEN", "XDG_CONFIG_HOME"):
            self.assertNotIn(key, env)
        if not sys.platform.startswith("linux"):
            with self.assertRaisesRegex(GUARD.IsolationError, "service_runner_requires_linux"):
                GUARD.service_environment(self.root, str(fake))

    def test_cloud_interactive_environment_matches_runtime_home_without_user_bus(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "_service_account_home", return_value=self.parent
        ), patch.object(
            GUARD, "_service_runtime_environment", side_effect=AssertionError("must not need user bus")
        ), patch.dict(
            os.environ,
            {
                "HERMES_HOME": "/production/hermes",
                "QWEN_ACCESS_TOKEN": "secret",
                "WEIXIN_TOKEN": "secret",
            },
            clear=False,
        ):
            env = GUARD.cloud_interactive_environment(self.root, str(fake))
        self.assertEqual(env["HOME"], str(self.parent))
        self.assertEqual(env["HERMES_HOME"], str(self.root))
        self.assertEqual(env["HERMES_SHARED_AUTH_DIR"], str(self.root / "shared"))
        self.assertNotIn("QWEN_ACCESS_TOKEN", env)
        self.assertNotIn("WEIXIN_TOKEN", env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_service_runner_only_allows_profile_bound_gateway_lifecycle(self) -> None:
        GUARD.validate_service_command(
            ["-p", "smalltest", "gateway", "install", "--no-start-now", "--no-start-on-login"]
        )
        GUARD.validate_service_command(["-p", "smalltest", "gateway", "status", "--deep"])
        for command in (
            ["-p", "default", "gateway", "start"],
            ["gateway", "start"],
            ["-p", "smalltest", "model"],
            ["-p", "smalltest", "gateway", "start", "--force"],
            ["-p", "smalltest", "gateway", "install", "--no-start-on-login", "--no-start-now"],
            ["-p", "smalltest", "gateway", "install", "--help", "--force"],
        ):
            with self.subTest(command=command), self.assertRaisesRegex(
                GUARD.IsolationError, "service_command_invalid"
            ):
                GUARD.validate_service_command(command)
        self.assertTrue(
            GUARD.is_persistent_service_command(["-p", "smalltest", "gateway", "install"])
        )
        self.assertTrue(GUARD.is_protected_foreground_stop(["-p", "smalltest", "gateway", "stop"]))
        self.assertFalse(
            GUARD.is_protected_foreground_stop(["-p", "smalltest", "gateway", "stop", "--all"])
        )
        self.assertFalse(GUARD.is_protected_foreground_stop(["-p", "default", "gateway", "stop"]))

    def test_isolated_runner_requires_leading_nondefault_profile_and_blocks_service_bypasses(self) -> None:
        for command in (
            ["profile", "create", "smalltest", "--no-alias", "--no-skills"],
            ["profile", "show", "smalltest"],
            ["-p", "smalltest", "config", "path"],
            ["--profile", "smalltest", "gateway", "setup"],
        ):
            with self.subTest(command=command):
                GUARD.validate_isolated_command(command)
        for command in (
            ["profile", "list"],
            ["model"],
            ["-p", "default", "config", "path"],
            ["--verbose", "-p", "smalltest", "config", "path"],
        ):
            with self.subTest(command=command), self.assertRaisesRegex(
                GUARD.IsolationError, "isolated_command_requires_nondefault_profile"
            ):
                GUARD.validate_isolated_command(command)
        for command in (
            ["gateway", "install"],
            ["gateway", "start"],
            ["--profile", "smalltest", "gateway", "restart"],
            ["-p", "smalltest", "gateway", "stop", "--all"],
            ["--verbose", "-p", "smalltest", "gateway", "uninstall"],
        ):
            with self.subTest(command=command):
                self.assertTrue(GUARD.is_persistent_service_command(command))

    def test_local_and_cloud_interactive_actions_cannot_cross_root_purposes(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        with self.assertRaisesRegex(GUARD.IsolationError, "local_runner_requires_local_test_root"):
            GUARD.validate_interactive_root(self.root, "run")
        GUARD.validate_interactive_root(self.root, "run-cloud")

        local_root = self.parent / "local-root"
        GUARD.create_root(str(local_root), purpose="local-test")
        GUARD.validate_interactive_root(local_root, "run")
        with self.assertRaisesRegex(GUARD.IsolationError, "cloud_runner_requires_cloud_service_root"):
            GUARD.validate_interactive_root(local_root, "run-cloud")

    def test_local_runner_allows_only_profile_scoped_foreground_stop(self) -> None:
        GUARD.create_root(str(self.root), purpose="local-test")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD.subprocess, "run", return_value=completed) as run:
            code = GUARD.main(
                [
                    "run",
                    "--root",
                    str(self.root),
                    "--hermes",
                    str(fake),
                    "--",
                    "-p",
                    "smalltest",
                    "gateway",
                    "stop",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0][-4:], ["-p", "smalltest", "gateway", "stop"])

        for tail in (["gateway", "restart"], ["gateway", "stop", "--all"]):
            stream = io.StringIO()
            with self.subTest(tail=tail), contextlib.redirect_stdout(stream):
                code = GUARD.main(
                    [
                        "run",
                        "--root",
                        str(self.root),
                        "--hermes",
                        str(fake),
                        "--",
                        "-p",
                        "smalltest",
                        *tail,
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stream.getvalue())["error"], "service_command_requires_run_service")

    def test_run_cloud_main_preserves_runtime_home_and_run_rejects_cloud_root(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "_service_account_home", return_value=self.parent
        ), patch.object(GUARD.subprocess, "run", return_value=completed) as run:
            code = GUARD.main(
                [
                    "run-cloud",
                    "--root",
                    str(self.root),
                    "--hermes",
                    str(fake),
                    "--",
                    "-p",
                    "smalltest",
                    "config",
                    "path",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(self.parent))
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_HOME"], str(self.root))

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = GUARD.main(
                [
                    "run",
                    "--root",
                    str(self.root),
                    "--hermes",
                    str(fake),
                    "--",
                    "-p",
                    "smalltest",
                    "config",
                    "path",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())["error"], "local_runner_requires_local_test_root")

    @unittest.skipIf(os.name == "nt", "Windows has no POSIX umask")
    def test_run_cloud_forces_private_umask_for_real_child_directories(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        fake = self.parent / "hermes"
        fake.write_text('#!/bin/sh\nmkdir "$HOME/umask-child"\n', encoding="utf-8")
        fake.chmod(0o700)
        previous_umask = os.umask(0o002)
        try:
            with patch.object(GUARD.sys, "platform", "linux"), patch.object(
                GUARD, "_service_account_home", return_value=self.parent
            ):
                code = GUARD.main(
                    [
                        "run-cloud",
                        "--root",
                        str(self.root),
                        "--hermes",
                        str(fake),
                        "--",
                        "profile",
                        "create",
                        "smalltest",
                        "--no-alias",
                        "--no-skills",
                    ]
                )
        finally:
            os.umask(previous_umask)
        self.assertEqual(code, 0)
        self.assertEqual((self.parent / "umask-child").stat().st_mode & 0o777, 0o700)

    def test_qwen_auth_runner_has_fixed_commands_and_uses_mode_correct_home(self) -> None:
        local_root = self.parent / "local-root"
        GUARD.create_root(str(local_root), purpose="local-test")
        qwen = self.parent / "qwen"
        qwen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        qwen.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD, "trusted_tty_available", return_value=True), patch.object(
            GUARD.subprocess, "run", return_value=completed
        ) as run:
            code = GUARD.main(
                [
                    "run-qwen-auth",
                    "--root",
                    str(local_root),
                    "--qwen",
                    str(qwen),
                    "--mode",
                    "login",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0], [str(qwen), "auth", "qwen-oauth"])
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(local_root / "os-home"))

        cloud_root = self.parent / "cloud-root"
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(cloud_root), purpose="cloud-service")
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "_service_account_home", return_value=self.parent
        ), patch.object(GUARD.subprocess, "run", return_value=completed) as run:
            code = GUARD.main(
                [
                    "run-qwen-auth",
                    "--root",
                    str(cloud_root),
                    "--qwen",
                    str(qwen),
                    "--mode",
                    "help",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0], [str(qwen), "auth", "--help"])
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(self.parent))

    def test_secret_interactive_commands_fail_closed_without_trusted_tty(self) -> None:
        GUARD.create_root(str(self.root), purpose="local-test")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        for tail in (["model"], ["gateway", "setup"], ["auth", "add", "openai"]):
            with self.subTest(tail=tail), patch.object(
                GUARD, "trusted_tty_available", return_value=False
            ), patch.object(GUARD.subprocess, "run") as run:
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    code = GUARD.main(
                        [
                            "run",
                            "--root",
                            str(self.root),
                            "--hermes",
                            str(fake),
                            "--",
                            "-p",
                            "smalltest",
                            *tail,
                        ]
                    )
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(stream.getvalue())["error"], "trusted_tty_required")
                run.assert_not_called()

    def test_qwen_login_requires_trusted_tty_but_help_does_not(self) -> None:
        GUARD.create_root(str(self.root), purpose="local-test")
        qwen = self.parent / "qwen"
        qwen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        qwen.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD, "trusted_tty_available", return_value=False), patch.object(
            GUARD.subprocess, "run", return_value=completed
        ) as run:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = GUARD.main(
                    [
                        "run-qwen-auth",
                        "--root",
                        str(self.root),
                        "--qwen",
                        str(qwen),
                        "--mode",
                        "login",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stream.getvalue())["error"], "trusted_tty_required")
            run.assert_not_called()

        with patch.object(GUARD, "trusted_tty_available", return_value=False), patch.object(
            GUARD.subprocess, "run", return_value=completed
        ) as run:
            code = GUARD.main(
                [
                    "run-qwen-auth",
                    "--root",
                    str(self.root),
                    "--qwen",
                    str(qwen),
                    "--mode",
                    "help",
                ]
            )
        self.assertEqual(code, 0)
        run.assert_called_once()

    def test_fresh_profile_rejects_old_sessions_memories_or_auth(self) -> None:
        GUARD.create_root(str(self.root))
        profile = self.root / "profiles" / "wechatassistant"
        (profile / "sessions").mkdir(parents=True, mode=0o700)
        (profile / "memories").mkdir(mode=0o700)
        (self.root / "sessions").mkdir(mode=0o700)
        (self.root / "memories").mkdir(mode=0o700)
        config = profile / "config.yaml"
        env_path = profile / ".env"
        env_path.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
        env_path.chmod(0o600)

        def fake_run(command: list[str], _env: dict[str, str]):
            if command[-2:] == ["config", "path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{config}\n", "")
            if command[-2:] == ["config", "env-path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{env_path}\n", "")
            if "gateway" in command:
                return GUARD.subprocess.CompletedProcess(command, 0, "Gateway is not running\n", "")
            return GUARD.subprocess.CompletedProcess(command, 0, "profile ok\n", "")

        with patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")), patch.object(
            GUARD, "isolated_environment", return_value={}
        ), patch.object(GUARD, "_run", side_effect=fake_run):
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertTrue(all(checks.values()))
        (profile / "sessions" / "old.json").write_text("{}", encoding="utf-8")
        (profile / "memories" / "old.md").write_text("old", encoding="utf-8")
        (self.root / "auth.json").write_text("{}", encoding="utf-8")
        env_path.write_text("OPENAI_API_KEY=old-secret\n", encoding="utf-8")
        with patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")), patch.object(
            GUARD, "isolated_environment", return_value={}
        ), patch.object(GUARD, "_run", side_effect=fake_run):
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertFalse(checks["fresh_profile_sessions_are_empty"])
        self.assertFalse(checks["fresh_profile_memories_are_empty"])
        self.assertFalse(checks["fresh_profile_has_no_auth_store"])
        self.assertFalse(checks["fresh_profile_env_has_no_nonempty_secret"])

    def test_fresh_profile_rejects_installed_but_stopped_service(self) -> None:
        GUARD.create_root(str(self.root))
        profile = self.root / "profiles" / "wechatassistant"
        (profile / "sessions").mkdir(parents=True, mode=0o700)
        (profile / "memories").mkdir(mode=0o700)
        (self.root / "sessions").mkdir(mode=0o700)
        (self.root / "memories").mkdir(mode=0o700)
        env_path = profile / ".env"
        env_path.write_text("", encoding="utf-8")
        env_path.chmod(0o600)

        def fake_run(command: list[str], _env: dict[str, str]):
            if command[-2:] == ["config", "path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{profile / 'config.yaml'}\n", "")
            if command[-2:] == ["config", "env-path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{env_path}\n", "")
            if "gateway" in command:
                return GUARD.subprocess.CompletedProcess(command, 0, "User gateway service is stopped\n", "")
            return GUARD.subprocess.CompletedProcess(command, 0, "profile ok\n", "")

        with patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")), patch.object(
            GUARD, "isolated_environment", return_value={}
        ), patch.object(GUARD, "_run", side_effect=fake_run):
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertFalse(checks["fresh_profile_service_absent_and_gateway_stopped"])

    def test_fresh_profile_rejects_root_state_or_external_home_auth_source(self) -> None:
        GUARD.create_root(str(self.root))
        profile = self.root / "profiles" / "wechatassistant"
        (profile / "sessions").mkdir(parents=True, mode=0o700)
        (profile / "memories").mkdir(mode=0o700)
        (self.root / "sessions").mkdir(mode=0o700)
        (self.root / "memories").mkdir(mode=0o700)
        env_path = profile / ".env"
        env_path.write_text("", encoding="utf-8")
        env_path.chmod(0o600)

        def fake_run(command: list[str], _env: dict[str, str]):
            if command[-2:] == ["config", "path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{profile / 'config.yaml'}\n", "")
            if command[-2:] == ["config", "env-path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{env_path}\n", "")
            if "gateway" in command:
                return GUARD.subprocess.CompletedProcess(command, 0, "Gateway is not running\n", "")
            return GUARD.subprocess.CompletedProcess(command, 0, "profile ok\n", "")

        patches = (
            patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")),
            patch.object(GUARD, "isolated_environment", return_value={}),
            patch.object(GUARD, "_run", side_effect=fake_run),
        )
        with patches[0], patches[1], patches[2]:
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertTrue(all(checks.values()))
        (self.root / "memories" / "old.md").write_text("old", encoding="utf-8")
        (self.root / "shared" / "future-auth.json").write_text("{}", encoding="utf-8")
        (self.root / "os-home" / ".codex").mkdir()
        with patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")), patch.object(
            GUARD, "isolated_environment", return_value={}
        ), patch.object(GUARD, "_run", side_effect=fake_run):
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertFalse(checks["fresh_root_memories_are_empty"])
        self.assertFalse(checks["fresh_shared_auth_directory_is_empty"])
        self.assertFalse(checks["fresh_isolated_os_home_is_empty"])

    def test_cloud_fresh_gate_rejects_preexisting_runtime_home_oauth(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        profile = self.root / "profiles" / "wechatassistant"
        (profile / "sessions").mkdir(parents=True, mode=0o700)
        (profile / "memories").mkdir(mode=0o700)
        (self.root / "sessions").mkdir(mode=0o700)
        (self.root / "memories").mkdir(mode=0o700)
        env_path = profile / ".env"
        env_path.write_text("", encoding="utf-8")
        env_path.chmod(0o600)
        qwen = self.parent / ".qwen"
        qwen.mkdir(mode=0o700)
        (qwen / "oauth_creds.json").write_text("old-secret", encoding="utf-8")

        def fake_run(command: list[str], _env: dict[str, str]):
            if command[-2:] == ["config", "path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{profile / 'config.yaml'}\n", "")
            if command[-2:] == ["config", "env-path"]:
                return GUARD.subprocess.CompletedProcess(command, 0, f"{env_path}\n", "")
            if "gateway" in command:
                return GUARD.subprocess.CompletedProcess(command, 0, "Gateway is not running\n", "")
            return GUARD.subprocess.CompletedProcess(command, 0, "profile ok\n", "")

        with patch.object(GUARD, "_validated_launcher", return_value=Path("/fake/hermes")), patch.object(
            GUARD, "cloud_interactive_environment", return_value={"HOME": str(self.parent)}
        ), patch.object(GUARD, "_run", side_effect=fake_run):
            checks = GUARD.fresh_profile_checks(self.root, "wechatassistant", "/fake/hermes")
        self.assertFalse(checks["fresh_runtime_home_known_auth_sources_are_absent"])

    def test_main_never_prints_root_or_secret_on_error(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = GUARD.main(["check-root", "--root", str(self.root)])
        self.assertEqual(code, 2)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["result"], "ERROR")
        self.assertNotIn(str(self.root), stream.getvalue())

    def test_run_checker_is_allowlisted_and_receives_clean_environment(self) -> None:
        GUARD.create_root(str(self.root))
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD.subprocess, "run", return_value=completed) as run, patch.dict(
            os.environ, {"KIMI_API_KEY": "production-secret"}, clear=False
        ):
            code = GUARD.main(
                [
                    "run-checker",
                    "--root",
                    str(self.root),
                    "--hermes",
                    str(fake),
                    "--checker",
                    "check_pre_qr_safety.py",
                    "--",
                    "--profile",
                    "smalltest",
                ]
            )
        self.assertEqual(code, 0)
        self.assertNotIn("KIMI_API_KEY", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_HOME"], str(self.root))
        self.assertIn("check_pre_qr_safety.py", run.call_args.args[0][1])

    def test_cloud_checker_uses_same_home_as_cloud_runtime(self) -> None:
        with patch.object(GUARD, "_service_account_home", return_value=self.parent):
            GUARD.create_root(str(self.root), purpose="cloud-service")
        fake = self.parent / "hermes"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        completed = GUARD.subprocess.CompletedProcess([], 0)
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "_service_account_home", return_value=self.parent
        ), patch.object(GUARD.subprocess, "run", return_value=completed) as run:
            code = GUARD.main(
                [
                    "run-checker",
                    "--root",
                    str(self.root),
                    "--hermes",
                    str(fake),
                    "--checker",
                    "check_pre_qr_safety.py",
                    "--",
                    "--profile",
                    "smalltest",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(self.parent))
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_HOME"], str(self.root))

    def test_chat_baseline_helper_is_allowlisted_for_isolated_execution(self) -> None:
        self.assertIn("apply_chat_safety_baseline.py", GUARD.ALLOWED_CHECKERS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

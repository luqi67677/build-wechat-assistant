#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "systemd_env_guard.py"
SPEC = importlib.util.spec_from_file_location("systemd_env_guard", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)
CANARY = "systemd-manager-secret-canary"


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class SystemdEnvGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.unit_dir = self.home / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True, mode=0o700)
        for directory in (self.home / ".config", self.home / ".config" / "systemd", self.unit_dir):
            directory.chmod(0o700)
        self.unit = self.unit_dir / "hermes-gateway-wechatassistant.service"
        self.expected_home = Path("/safe")
        self.service_home = GUARD._service_account_home()
        self.interpreter = "/safe/python"
        self.execstart = (
            "{ path=/safe/python ; argv[]=/safe/python -m hermes_cli.main "
            "--profile wechatassistant gateway run ; ignore_errors=no ; }"
        )
        self.unit.write_text(
            "[Service]\n"
            "ExecStart=/safe/python -m hermes_cli.main --profile wechatassistant gateway run\n"
            "Environment=HERMES_HOME=/safe\n",
            encoding="utf-8",
        )
        self.unit.chmod(0o600)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manager_only_weixin_override_is_rejected_without_value_output(self) -> None:
        def runner(_arguments: list[str]) -> subprocess.CompletedProcess[str]:
            return completed(f"PATH=/safe\nWEIXIN_BASE_URL=https://{CANARY}.example\n")

        self.assertFalse(GUARD.manager_env_clean(runner))
        stream = io.StringIO()
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD, "manager_env_clean", return_value=False
        ), contextlib.redirect_stdout(stream):
            code = GUARD.main(["check-manager"])
        self.assertEqual(code, 1)
        self.assertNotIn(CANARY, stream.getvalue())
        self.assertFalse(json.loads(stream.getvalue())["checks"]["systemd_manager_has_no_uncontrolled_secret_environment"])

    def test_main_rejects_non_linux_before_systemd_access(self) -> None:
        stream = io.StringIO()
        with patch.object(GUARD.sys, "platform", "darwin"), contextlib.redirect_stdout(stream):
            code = GUARD.main(["check-manager"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())["error"], "systemd_guard_requires_linux")

    def test_manager_or_unit_model_secret_and_shared_auth_override_are_rejected(self) -> None:
        for key in (
            "KIMI_API_KEY",
            "FUTURE_MODEL_API_KEY",
            "HERMES_SHARED_AUTH_DIR",
            "CODEX_HOME",
            "AWS_SHARED_CREDENTIALS_FILE",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ):
            with self.subTest(scope="manager", key=key):
                self.assertFalse(
                    GUARD.manager_environment_is_clean(f"PATH=/safe\n{key}=canary\n")
                )

        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            values = {
                "Environment": "HERMES_HOME=/safe FUTURE_MODEL_API_KEY=canary",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "ExecStart": self.execstart,
            }
            return completed(values[arguments[-2]])

        checks = GUARD.unit_guard_checks(
            "wechatassistant", self.expected_home, self.interpreter, runner
        )
        self.assertFalse(checks["unit_environment_has_no_uncontrolled_secret"])

    def test_resolver_binds_exact_expected_root_and_scrubs_process_secrets(self) -> None:
        root = (self.home / "isolated-root").resolve()
        profile_home = root / "profiles" / "wechatassistant"
        profile_home.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        root_info = root.lstat()
        marker = root / GUARD.ISOLATED_ROOT_MARKER
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "root_device": root_info.st_dev,
                    "root_inode": root_info.st_ino,
                    "owner_uid": os.geteuid(),
                    "nonce": "a" * 32,
                }
            ),
            encoding="utf-8",
        )
        marker.chmod(0o600)
        config = profile_home / "config.yaml"
        config.write_text("model: test\n", encoding="utf-8")
        config.chmod(0o600)
        launcher = self.home / "hermes"
        launcher.write_text("#!/usr/bin/python3\n", encoding="utf-8")
        launcher.chmod(0o700)
        result = completed(f"{config}\n")
        with patch.object(GUARD.subprocess, "run", return_value=result) as run, patch.dict(
            os.environ,
            {
                "HERMES_HOME": "/production/root",
                "HERMES_SHARED_AUTH_DIR": "/production/shared",
                "OPENAI_API_KEY": "production-secret",
                "WEIXIN_TOKEN": "production-secret",
            },
            clear=False,
        ):
            resolved_home, interpreter = GUARD.resolve_expected_binding(
                "wechatassistant", str(launcher), str(root)
            )
        self.assertEqual(resolved_home, profile_home)
        self.assertEqual(interpreter, "/usr/bin/python3")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["HERMES_HOME"], str(root))
        self.assertEqual(env["HERMES_SHARED_AUTH_DIR"], str(root / "shared"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("WEIXIN_TOKEN", env)

        wrong = (self.home / "wrong-root").resolve()
        wrong.mkdir(mode=0o700)
        wrong_info = wrong.lstat()
        wrong_marker = wrong / GUARD.ISOLATED_ROOT_MARKER
        wrong_marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "root_device": wrong_info.st_dev,
                    "root_inode": wrong_info.st_ino,
                    "owner_uid": os.geteuid(),
                    "nonce": "b" * 32,
                }
            ),
            encoding="utf-8",
        )
        wrong_marker.chmod(0o600)
        with patch.object(GUARD.subprocess, "run", return_value=result), self.assertRaisesRegex(
            GUARD.GuardError, "profile_path_mismatch"
        ):
            GUARD.resolve_expected_binding("wechatassistant", str(launcher), str(wrong))

    def test_install_guard_is_exact_idempotent_and_does_not_overwrite_conflict(self) -> None:
        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments[-2:] == ["ExecStart", "--value"]:
                return completed(self.execstart)
            if arguments[-2:] == ["Environment", "--value"]:
                return completed("HERMES_HOME=/safe")
            return completed()

        self.assertTrue(
            GUARD.install_guard(
                "wechatassistant", self.expected_home, self.interpreter, self.home, runner
            )
        )
        target = self.unit_dir / "hermes-gateway-wechatassistant.service.d" / "10-weixin-env-guard.conf"
        expected = GUARD.guard_dropin_text()
        self.assertEqual(target.read_text(encoding="utf-8"), expected)
        self.assertTrue(
            GUARD.install_guard(
                "wechatassistant", self.expected_home, self.interpreter, self.home, runner
            )
        )
        target.write_text("[Service]\n", encoding="utf-8")
        with self.assertRaisesRegex(GUARD.GuardError, "guard_file_conflict"):
            GUARD.install_guard(
                "wechatassistant", self.expected_home, self.interpreter, self.home, runner
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "[Service]\n")

    def test_unit_guard_requires_clean_manager_no_env_file_and_complete_unset(self) -> None:
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            property_name = arguments[-2]
            values = {
                "Environment": "PATH=/safe HERMES_HOME=/safe",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "ExecStart": self.execstart,
            }
            return completed(values[property_name])

        self.assertTrue(
            all(
                GUARD.unit_guard_checks(
                    "wechatassistant", self.expected_home, self.interpreter, runner
                ).values()
            )
        )

    def test_unit_environment_or_missing_unset_fails_closed(self) -> None:
        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            property_name = arguments[-2]
            values = {
                "Environment": "WEIXIN_DM_POLICY=open HOME=/wrong-home",
                "EnvironmentFiles": "/unsafe/.env",
                "UnsetEnvironment": "",
                "ExecStart": self.execstart,
            }
            return completed(values[property_name])

        checks = GUARD.unit_guard_checks(
            "wechatassistant", self.expected_home, self.interpreter, runner
        )
        self.assertFalse(checks["unit_environment_has_no_uncontrolled_secret"])
        self.assertFalse(checks["unit_home_override_absent_or_matches_service_account"])
        self.assertFalse(checks["unit_environment_files_absent"])
        self.assertFalse(checks["unit_unsets_all_known_secret_keys"])

    def test_runtime_rejects_manager_inherited_process_environment(self) -> None:
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            if arguments[0] == "is-active":
                return completed("active\n")
            if arguments[0] == "is-enabled":
                return completed("enabled\n")
            property_name = arguments[-2]
            values = {
                "Environment": "PATH=/safe HERMES_HOME=/safe",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "MainPID": "4321",
                "ExecStart": self.execstart,
            }
            return completed(values[property_name])

        environ = self.home / "4321" / "environ"
        environ.parent.mkdir()
        environ.write_bytes(
            f"PATH=/safe\0HOME={self.service_home}\0HERMES_HOME=/safe\0WEIXIN_DM_POLICY=open\0".encode()
        )
        (environ.parent / "cmdline").write_bytes(
            b"/safe/python\0-m\0hermes_cli.main\0--profile\0wechatassistant\0gateway\0run\0"
        )
        with patch.object(GUARD.os, "geteuid", return_value=os.geteuid()):
            checks = GUARD.runtime_checks(
                "wechatassistant", self.expected_home, self.interpreter, "enabled", proc_root=self.home, runner=runner
            )
        self.assertFalse(checks["service_initial_environment_has_no_uncontrolled_secret"])

    def test_runtime_clean_environment_passes(self) -> None:
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            if arguments[0] == "is-active":
                return completed("active\n")
            if arguments[0] == "is-enabled":
                return completed("enabled\n")
            property_name = arguments[-2]
            values = {
                "Environment": "PATH=/safe HERMES_HOME=/safe",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "MainPID": "4321",
                "ExecStart": self.execstart,
            }
            return completed(values[property_name])

        environ = self.home / "4321" / "environ"
        environ.parent.mkdir()
        environ.write_bytes(f"PATH=/safe\0HOME={self.service_home}\0HERMES_HOME=/safe\0".encode())
        (environ.parent / "cmdline").write_bytes(
            b"/safe/python\0-m\0hermes_cli.main\0--profile\0wechatassistant\0gateway\0run\0"
        )
        checks = GUARD.runtime_checks(
            "wechatassistant", self.expected_home, self.interpreter, "enabled", proc_root=self.home, runner=runner
        )
        self.assertTrue(all(checks.values()))

    def test_same_service_user_stray_or_second_gateway_is_rejected(self) -> None:
        stray = self.home / "777"
        stray.mkdir()
        (stray / "cmdline").write_bytes(
            b"/safe/hermes\0-p\0otherprofile\0gateway\0run\0"
        )
        stopped = GUARD.stopped_process_scope_checks(self.home)
        self.assertTrue(stopped["same_service_user_gateway_process_scan_complete"])
        self.assertFalse(stopped["same_service_user_has_no_gateway_process"])

        target = self.home / "4321"
        target.mkdir()
        (target / "environ").write_bytes(
            f"PATH=/safe\0HOME={self.service_home}\0HERMES_HOME=/safe\0".encode()
        )
        (target / "cmdline").write_bytes(
            b"/safe/python\0-m\0hermes_cli.main\0--profile\0wechatassistant\0gateway\0run\0"
        )
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            if arguments[0] == "is-active":
                return completed("active\n")
            if arguments[0] == "is-enabled":
                return completed("enabled\n")
            values = {
                "Environment": "PATH=/safe HERMES_HOME=/safe",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "MainPID": "4321",
                "ExecStart": self.execstart,
            }
            return completed(values[arguments[-2]])

        checks = GUARD.runtime_checks(
            "wechatassistant", self.expected_home, self.interpreter, "enabled", proc_root=self.home, runner=runner
        )
        self.assertFalse(checks["target_is_only_gateway_for_service_user"])

    def test_wrong_unit_or_runtime_profile_is_rejected(self) -> None:
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))
        wrong_exec = self.execstart.replace("wechatassistant", "default")

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed(f"PATH=/safe\nHOME={self.service_home}\n")
            if arguments[0] == "is-active":
                return completed("active\n")
            if arguments[0] == "is-enabled":
                return completed("enabled\n")
            property_name = arguments[-2]
            values = {
                "Environment": "PATH=/safe HERMES_HOME=/wrong",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "MainPID": "4321",
                "ExecStart": wrong_exec,
            }
            return completed(values[property_name])

        proc = self.home / "4321"
        proc.mkdir()
        (proc / "environ").write_bytes(
            b"PATH=/safe\0HOME=/wrong-home\0HERMES_HOME=/wrong\0"
        )
        (proc / "cmdline").write_bytes(
            b"/safe/python\0-m\0hermes_cli.main\0--profile\0default\0gateway\0run\0"
        )
        checks = GUARD.runtime_checks(
            "wechatassistant", self.expected_home, self.interpreter, "enabled", proc_root=self.home, runner=runner
        )
        self.assertFalse(checks["unit_execstart_binds_requested_profile"])
        self.assertFalse(checks["unit_hermes_home_matches_requested_profile"])
        self.assertFalse(checks["service_process_hermes_home_matches_requested_profile"])
        self.assertFalse(checks["service_process_home_matches_service_account"])
        self.assertFalse(checks["service_process_argv_matches_requested_profile"])

    def test_manager_home_must_match_operating_system_account(self) -> None:
        unset = " ".join(sorted(GUARD.BLOCKED_SYSTEMD_ENV_KEYS))

        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments == ["show-environment"]:
                return completed("PATH=/safe\nHOME=/wrong-home\n")
            values = {
                "Environment": "HERMES_HOME=/safe",
                "EnvironmentFiles": "",
                "UnsetEnvironment": unset,
                "ExecStart": self.execstart,
            }
            return completed(values[arguments[-2]])

        checks = GUARD.unit_guard_checks(
            "wechatassistant", self.expected_home, self.interpreter, runner
        )
        self.assertFalse(checks["systemd_manager_home_matches_service_account"])

    def test_service_active_and_enabled_states_are_stage_specific(self) -> None:
        def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            if arguments[0] == "is-active":
                return completed("inactive\n", returncode=3)
            if arguments[0] == "is-enabled":
                return completed("disabled\n", returncode=1)
            raise AssertionError(arguments)

        staged = GUARD.service_state_checks(
            "wechatassistant", "inactive", "disabled", runner
        )
        self.assertTrue(all(staged.values()))
        wrong = GUARD.service_state_checks(
            "wechatassistant", "active", "enabled", runner
        )
        self.assertFalse(wrong["service_active_state_matches_expected"])
        self.assertFalse(wrong["service_enabled_state_matches_expected"])

    def test_main_requires_explicit_enabled_state(self) -> None:
        stream = io.StringIO()
        with patch.object(GUARD.sys, "platform", "linux"), contextlib.redirect_stdout(stream):
            code = GUARD.main(
                [
                    "check-prestart",
                    "--profile",
                    "wechatassistant",
                    "--hermes",
                    "/fake/hermes",
                    "--expected-hermes-root",
                    "/safe-root",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())["error"], "expected_enabled_required")

    def test_prerestart_requires_active_expected_state(self) -> None:
        stream = io.StringIO()
        with patch.object(GUARD.sys, "platform", "linux"), patch.object(
            GUARD,
            "resolve_expected_binding",
            return_value=(self.expected_home, self.interpreter),
        ), patch.object(
            GUARD,
            "runtime_checks",
            return_value={"runtime_safe": True},
        ) as runtime, contextlib.redirect_stdout(stream):
            code = GUARD.main(
                [
                    "check-prerestart",
                    "--profile",
                    "wechatassistant",
                    "--hermes",
                    "/fake/hermes",
                    "--expected-hermes-root",
                    "/safe-root",
                    "--expect-enabled",
                    "enabled",
                ]
            )
        self.assertEqual(code, 0)
        runtime.assert_called_once_with(
            "wechatassistant", self.expected_home, self.interpreter, "enabled"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

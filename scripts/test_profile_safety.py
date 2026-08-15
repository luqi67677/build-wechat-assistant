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
MODULE_PATH = ROOT / "scripts" / "check_profile_safety.py"
SPEC = importlib.util.spec_from_file_location("check_profile_safety", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
CANARY = "bwa-secret-canary-never-print"


class ProfileSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        profiles = Path(self.temp.name) / "profiles"
        profiles.mkdir(mode=0o700)
        self.profile_dir = profiles / "wechatassistant"
        self.profile_dir.mkdir(mode=0o700)
        self.config = self.profile_dir / "config.yaml"
        self.env = self.profile_dir / ".env"
        self.write_config("gateway:\n  platforms:\n    weixin:\n      extra:\n        split_multiline_messages: false\n")
        self.write_env(
            "\n".join(
                [
                    "WEIXIN_ACCOUNT_ID=account-canary",
                    f"WEIXIN_TOKEN={CANARY}",
                    "WEIXIN_DM_POLICY=allowlist",
                    "WEIXIN_GROUP_POLICY=disabled",
                    "WEIXIN_ALLOW_ALL_USERS=false",
                    "GATEWAY_ALLOW_ALL_USERS=false",
                    "WEIXIN_ALLOWED_USERS=owner-canary",
                    "WEIXIN_HOME_CHANNEL=owner-canary",
                    "WEIXIN_BASE_URL=https://ilinkai.weixin.qq.com",
                    "WEIXIN_CDN_BASE_URL=https://novac2c.cdn.weixin.qq.com/c2c",
                ]
            )
            + "\n"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, text: str) -> None:
        self.config.write_text(text, encoding="utf-8")
        self.config.chmod(0o600)

    def write_env(self, text: str) -> None:
        self.env.write_text(text, encoding="utf-8")
        self.env.chmod(0o600)

    def run_main(self, profile: str = "wechatassistant") -> tuple[int, dict, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), patch.dict(
            os.environ,
            {
                "WEIXIN_ACCOUNT_ID": "",
                "WEIXIN_TOKEN": "",
                "WEIXIN_DM_POLICY": "",
                "WEIXIN_GROUP_POLICY": "",
                "WEIXIN_ALLOWED_USERS": "",
                "WEIXIN_HOME_CHANNEL": "",
                "WEIXIN_ALLOW_ALL_USERS": "",
                "GATEWAY_ALLOW_ALL_USERS": "",
                "WEIXIN_BASE_URL": "",
                "WEIXIN_CDN_BASE_URL": "",
            },
            clear=False,
        ), patch.object(CHECKER, "service_absent_and_gateway_stopped", return_value=True):
            with patch.object(CHECKER, "resolve_profile_paths", return_value=(self.config, self.env)):
                code = CHECKER.main(["--profile", profile, "--hermes", "/fake/hermes", "--expected-hermes-root", str(self.profile_dir.parent.parent)])
        output = stream.getvalue()
        return code, json.loads(output), output

    def test_safe_profile_passes_without_printing_secrets_or_ids(self) -> None:
        code, payload, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        for forbidden in (CANARY, "owner-canary", "account-canary", str(self.profile_dir)):
            self.assertNotIn(forbidden, output)

    def test_default_profile_is_rejected(self) -> None:
        code, payload, _ = self.run_main("default")
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["profile_is_named_nondefault"])

    def test_profile_path_must_match_requested_name(self) -> None:
        code, payload, _ = self.run_main("otherprofile")
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["profile_path_matches_requested_name"])

    def test_public_resolver_rejects_cli_that_returns_another_profile(self) -> None:
        wrong = self.profile_dir.parent / "default"
        wrong.mkdir(mode=0o700)
        wrong_config = wrong / "config.yaml"
        wrong_env = wrong / ".env"
        wrong_config.write_text("{}\n", encoding="utf-8")
        wrong_env.write_text("", encoding="utf-8")
        wrong_config.chmod(0o600)
        wrong_env.chmod(0o600)
        results = [
            CHECKER.subprocess.CompletedProcess([], 0, f"{wrong_config}\n", ""),
            CHECKER.subprocess.CompletedProcess([], 0, f"{wrong_env}\n", ""),
        ]
        with patch.object(CHECKER.subprocess, "run", side_effect=results):
            with self.assertRaisesRegex(CHECKER.SafetyCheckError, "profile_path_mismatch"):
                CHECKER.resolve_profile_paths("wechatassistant", "/fake/hermes", self.profile_dir.parent.parent)

    def test_owner_and_home_must_match(self) -> None:
        self.write_env(self.env.read_text().replace("WEIXIN_HOME_CHANNEL=owner-canary", "WEIXIN_HOME_CHANNEL=other"))
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["home_channel_matches_owner"])

    def test_allow_all_in_file_or_process_is_rejected(self) -> None:
        self.write_env(self.env.read_text().replace("WEIXIN_ALLOW_ALL_USERS=false", "WEIXIN_ALLOW_ALL_USERS=true"))
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["allow_all_flags_are_false"])
        self.write_env(self.env.read_text().replace("WEIXIN_ALLOW_ALL_USERS=true", "WEIXIN_ALLOW_ALL_USERS=false"))
        with patch.dict(os.environ, {"GATEWAY_ALLOW_ALL_USERS": "true"}, clear=False):
            checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=True)
        self.assertFalse(checks["process_weixin_overrides_absent"])

    def test_non_allow_all_process_policy_override_is_rejected(self) -> None:
        with patch.dict(os.environ, {"WEIXIN_DM_POLICY": "open"}, clear=False):
            checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=True)
        self.assertFalse(checks["process_weixin_overrides_absent"])

    def test_unknown_future_process_or_profile_weixin_key_is_rejected(self) -> None:
        with patch.dict(os.environ, {"WEIXIN_FUTURE_OVERRIDE": "canary"}, clear=False):
            checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=True)
        self.assertFalse(checks["process_weixin_overrides_absent"])
        self.write_env(self.env.read_text(encoding="utf-8") + "WEIXIN_FUTURE_PROFILE_KEY=other\n")
        checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=True)
        self.assertFalse(checks["unknown_profile_weixin_keys_absent"])

    def test_config_access_override_is_rejected(self) -> None:
        self.write_config("gateway:\n  platforms:\n    weixin:\n      extra:\n        dm_policy: open\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_access_overrides_absent"])

    def test_v020_generated_weixin_toolset_lists_are_scannable(self) -> None:
        self.write_config(
            "display:\n"
            "  platforms:\n"
            "    weixin:\n"
            "      show_reasoning: false\n"
            "platform_toolsets:\n"
            "  cli:\n"
            "    - clarify\n"
            "  weixin:\n"
            "    - clarify\n"
            "    - kanban\n"
            "known_plugin_toolsets:\n"
            "  weixin:\n"
            "    - spotify\n"
            "known_builtin_toolsets:\n"
            "  weixin:\n"
            "    - clarify\n"
        )
        code, payload, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(payload["checks"]["config_scan_supported"])
        self.assertTrue(payload["checks"]["config_access_overrides_absent"])

    def test_unknown_weixin_list_path_still_fails_closed(self) -> None:
        self.write_config("future:\n  weixin:\n    - open\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_scan_supported"])

    def test_inline_weixin_config_fails_closed(self) -> None:
        self.write_config("gateway:\n  platforms:\n    weixin: {extra: {dm_policy: open}}\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_scan_supported"])

    def test_whole_gateway_inline_mapping_fails_closed(self) -> None:
        self.write_config("gateway: {platforms: {weixin: {extra: {dm_policy: open}}}}\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_scan_supported"])

    def test_yaml_alias_injected_weixin_policy_fails_closed(self) -> None:
        self.write_config(
            "weixin_platforms: &wx\n"
            "  weixin:\n"
            "    extra:\n"
            "      dm_policy: open\n"
            "gateway:\n"
            "  platforms: *wx\n"
        )
        supported, override = CHECKER.config_has_weixin_security_override(self.config)
        self.assertFalse(supported)
        self.assertFalse(override)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_scan_supported"])

    def test_config_weixin_endpoint_override_is_rejected(self) -> None:
        self.write_config(
            "gateway:\n"
            "  platforms:\n"
            "    weixin:\n"
            "      extra:\n"
            "        base_url: https://attacker.example\n"
            "        cdn_base_url: https://attacker.example/c2c\n"
        )
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["config_access_overrides_absent"])

    def test_profile_env_weixin_endpoint_override_is_rejected(self) -> None:
        self.write_env(self.env.read_text().replace("https://ilinkai.weixin.qq.com", "https://attacker.example"))
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_endpoints_are_official_or_builtin"])

    def test_qr_generated_official_endpoints_pass(self) -> None:
        self.write_env(self.env.read_text(encoding="utf-8") + "WEIXIN_GROUP_ALLOWED_USERS=\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(payload["checks"]["weixin_endpoints_are_official_or_builtin"])

    def test_official_optional_weixin_keys_are_accepted(self) -> None:
        self.write_env(
            self.env.read_text(encoding="utf-8")
            + "WEIXIN_HOME_CHANNEL_NAME=测试小号\n"
            + "WEIXIN_SPLIT_MULTILINE_MESSAGES=false\n"
        )
        code, payload, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(payload["checks"]["unknown_profile_weixin_keys_absent"])

    def test_process_weixin_endpoint_override_is_rejected(self) -> None:
        with patch.dict(os.environ, {"WEIXIN_CDN_BASE_URL": "https://attacker.example/c2c"}, clear=False):
            checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=True)
        self.assertFalse(checks["process_weixin_overrides_absent"])
        self.assertTrue(checks["weixin_endpoints_are_official_or_builtin"])

    def test_duplicate_security_key_is_rejected(self) -> None:
        self.write_env(self.env.read_text() + "WEIXIN_DM_POLICY=open\n")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["env_has_no_duplicate_keys"])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits do not represent Windows ACLs")
    def test_group_readable_secret_file_is_rejected(self) -> None:
        self.env.chmod(0o640)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["secret_files_and_directory_private"])
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o640)

    def test_missing_credentials_is_rejected_without_error_details(self) -> None:
        self.write_env(self.env.read_text().replace(f"WEIXIN_TOKEN={CANARY}", "WEIXIN_TOKEN="))
        code, payload, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_credentials_present"])
        self.assertNotIn(CANARY, output)

    def test_running_gateway_is_rejected(self) -> None:
        checks = CHECKER.evaluate("wechatassistant", self.config, self.env, gateway_stopped=False)
        self.assertFalse(checks["service_absent_and_gateway_not_running_until_persona_ready"])

    def test_unresolved_global_auth_scope_is_rejected(self) -> None:
        other = Path(self.temp.name) / "custom" / "wechatassistant"
        other.mkdir(parents=True, mode=0o700)
        config = other / "config.yaml"
        env = other / ".env"
        config.write_text(self.config.read_text(encoding="utf-8"), encoding="utf-8")
        env.write_text(self.env.read_text(encoding="utf-8"), encoding="utf-8")
        config.chmod(0o600)
        env.chmod(0o600)
        checks = CHECKER.evaluate("wechatassistant", config, env, gateway_stopped=True)
        self.assertFalse(checks["profile_store_path_scope_resolved"])

    def test_gateway_service_status_parser_fails_closed(self) -> None:
        cases = [
            ("✗ Gateway is not running", True),
            ("✗ User gateway service is stopped", False),
            ("✗ Gateway service is not loaded", False),
            ("✗ No gateway process detected", False),
            ("✓ Gateway is running (PID: 7)", False),
            ("unknown status", False),
        ]
        for output, expected in cases:
            completed = CHECKER.subprocess.CompletedProcess([], 0, stdout=output, stderr="")
            with self.subTest(output=output), patch.object(CHECKER.subprocess, "run", return_value=completed):
                self.assertEqual(CHECKER.service_absent_and_gateway_stopped("wechatassistant", "hermes"), expected)

    def test_weixin_store_permissions_and_symlinks_are_checked(self) -> None:
        accounts = self.profile_dir / "weixin" / "accounts"
        accounts.mkdir(parents=True, mode=0o700)
        token = accounts / "context-token.json"
        token.write_text("{}", encoding="utf-8")
        token.chmod(0o640)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_and_auth_stores_private"])

    def test_media_cache_permissions_are_checked(self) -> None:
        cache = self.profile_dir / "cache" / "images"
        cache.mkdir(parents=True, mode=0o700)
        media = cache / "image.jpg"
        media.write_bytes(b"test")
        media.chmod(0o644)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse(payload["checks"]["weixin_and_auth_stores_private"])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits do not represent Windows ACLs")
    def test_runtime_home_qwen_oauth_store_is_in_the_permission_boundary(self) -> None:
        runtime_home = Path(self.temp.name) / "service-home"
        qwen = runtime_home / ".qwen"
        qwen.mkdir(parents=True, mode=0o700)
        oauth = qwen / "oauth_creds.json"
        oauth.write_text("secret-canary", encoding="utf-8")
        oauth.chmod(0o640)
        with patch.object(CHECKER.Path, "home", return_value=runtime_home):
            paths = CHECKER.collect_sensitive_paths(self.config, self.env)
        self.assertIn(qwen.resolve(), paths)
        self.assertIn(oauth.resolve(), paths)
        self.assertFalse(CHECKER.posix_permissions_private(paths))

    def test_snapshot_changes_without_printing_secret_values(self) -> None:
        first = CHECKER.snapshot_profile_state(self.config, self.env)
        self.write_env(self.env.read_text(encoding="utf-8") + "# fresh\n")
        second = CHECKER.snapshot_profile_state(self.config, self.env)
        self.assertNotEqual(first, second)
        self.assertNotIn(CANARY, first + second)

    def test_snapshot_is_order_independent_and_includes_relative_identity(self) -> None:
        first_path = self.profile_dir / "auth" / "first.json"
        first_path.parent.mkdir(mode=0o700)
        first_path.write_text("{}", encoding="utf-8")
        first_path.chmod(0o600)
        second_path = first_path.with_name("second.json")
        second_path.write_text("{}", encoding="utf-8")
        second_path.chmod(0o600)
        same_ns = min(first_path.stat().st_mtime_ns, second_path.stat().st_mtime_ns)
        os.utime(first_path, ns=(same_ns, same_ns))
        os.utime(second_path, ns=(same_ns, same_ns))
        paths = [self.config, self.env, first_path]
        with patch.object(CHECKER, "collect_sensitive_paths", return_value=paths):
            forward = CHECKER.snapshot_profile_state(self.config, self.env)
        with patch.object(CHECKER, "collect_sensitive_paths", return_value=list(reversed(paths))):
            reverse = CHECKER.snapshot_profile_state(self.config, self.env)
        with patch.object(CHECKER, "collect_sensitive_paths", return_value=[self.config, self.env, second_path]):
            renamed = CHECKER.snapshot_profile_state(self.config, self.env)
        self.assertEqual(forward, reverse)
        self.assertNotEqual(forward, renamed)

    @unittest.skipIf(os.name == "nt", "临时可执行文件路径由 Windows 专项环境验证")
    def test_public_profile_cli_resolves_paths_without_leaking_them(self) -> None:
        fake = Path(self.temp.name) / "fake-hermes"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"print('Gateway is not running' if 'gateway' in sys.argv else ({str(self.config)!r} if sys.argv[-1] == 'path' else {str(self.env)!r}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        stream = io.StringIO()
        clean_env = {
            key: ""
            for key in (
                "WEIXIN_ACCOUNT_ID",
                "WEIXIN_TOKEN",
                "WEIXIN_DM_POLICY",
                "WEIXIN_GROUP_POLICY",
                "WEIXIN_ALLOWED_USERS",
                "WEIXIN_HOME_CHANNEL",
                "WEIXIN_ALLOW_ALL_USERS",
                "GATEWAY_ALLOW_ALL_USERS",
                "WEIXIN_BASE_URL",
                "WEIXIN_CDN_BASE_URL",
            )
        }
        with contextlib.redirect_stdout(stream), patch.dict(os.environ, clean_env, clear=False):
            code = CHECKER.main(["--profile", "wechatassistant", "--hermes", str(fake), "--expected-hermes-root", str(self.profile_dir.parent.parent)])
        output = stream.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["result"], "PASS")
        self.assertNotIn(str(self.profile_dir), output)
        self.assertNotIn(CANARY, output)

    def test_windows_acl_probe_returns_only_boolean_to_checker(self) -> None:
        completed = CHECKER.subprocess.CompletedProcess([], 0, stdout="true\n", stderr="")
        with patch.object(CHECKER.subprocess, "run", return_value=completed) as run:
            self.assertTrue(CHECKER.windows_permissions_private([self.config, self.env]))
        kwargs = run.call_args.kwargs
        self.assertIn("BWA_ACL_PATHS", kwargs["env"])
        self.assertNotIn(CANARY, kwargs["env"]["BWA_ACL_PATHS"])
        script = run.call_args.args[0][-1]
        self.assertIn("$allowedSids -notcontains $sid", script)
        self.assertIn("ReparsePoint", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)

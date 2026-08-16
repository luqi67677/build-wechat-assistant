#!/usr/bin/env python3
"""本机兼容快照核验：文档引用必须与当前安装一致，但不冒充官方干净版本证据。

本机未安装 Hermes 时全部跳过并标记未验证，不伪造通过。
这是静态字符串契约测试之外的第二层防线：契约测试证明文档自洽，
本测试证明文档描述与现实一致。
"""
from __future__ import annotations

import re
import subprocess
import unittest
import importlib.util
import os
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HERMES = os.environ.get("BWA_HERMES_EXECUTABLE")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def find_hermes_install() -> Path | None:
    if not HERMES:
        return None
    try:
        result = subprocess.run(
            [HERMES, "--version"], capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^Install directory:\s*(\S.+)$", result.stdout, re.MULTILINE)
    if not match:
        return None
    install = Path(match.group(1).strip())
    return install if (install / "hermes_cli").is_dir() else None


HERMES_INSTALL = find_hermes_install()


class SourceFactIsolationTests(unittest.TestCase):
    def test_global_hermes_is_not_probed_without_explicit_release_launcher(self) -> None:
        with mock.patch(f"{__name__}.HERMES", None), mock.patch("subprocess.run") as run:
            self.assertIsNone(find_hermes_install())
        run.assert_not_called()


def hermes_cli_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (HERMES_INSTALL / "hermes_cli").rglob("*.py")
    )


@unittest.skipUnless(HERMES_INSTALL, "本机未安装 Hermes，源码事实核验未验证")
class WizardSourceFactTests(unittest.TestCase):
    """weixin-setup-zh.md 引用的每一屏英文文案必须存在于真实源码。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = hermes_cli_source()
        cls.temp_home = tempfile.TemporaryDirectory()
        temp_root = Path(cls.temp_home.name)
        cls.hermes_env = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "HERMES_HOME": str(temp_root / "hermes"),
            "HERMES_SHARED_AUTH_DIR": str(temp_root / "shared-auth"),
            "TMPDIR": str(temp_root),
            "PYTHONPYCACHEPREFIX": str(temp_root / "pycache"),
        }
        result = subprocess.run(
            [HERMES, "profile", "create", "sourcefacttest", "--no-alias", "--no-skills"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=cls.hermes_env,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("无法创建隔离的源码事实测试 Profile")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_home.cleanup()

    def assert_screen_in_source(self, screen: str) -> None:
        self.assertIn(screen, self.source, f"向导文案与真实源码不符：{screen}")

    def test_00_clean_tag_checker_is_distinct_from_local_snapshot(self) -> None:
        setup = read("references/setup-guide.md")
        self.assertIn("官方干净 tag", setup)
        self.assertIn("check_hermes_cli_contract.py", setup)
        self.assertIn("本 Skill 不再随包提供跨版本源码补丁", read("SKILL.md"))

    def test_01_qr_login_screen_exists(self) -> None:
        self.assert_screen_in_source("Start QR login now?")

    def test_02_dm_authorization_screen_exists(self) -> None:
        self.assert_screen_in_source("How should direct messages be authorized?")

    def test_03_dm_choices_exist(self) -> None:
        self.assert_screen_in_source("Use DM pairing approval")
        self.assert_screen_in_source("Allow all direct messages")
        self.assert_screen_in_source("Only allow listed user IDs")

    def test_04_home_channel_screen_exists(self) -> None:
        self.assert_screen_in_source("as the home channel?")

    def test_05_start_now_screen_exists(self) -> None:
        self.assert_screen_in_source("Start the gateway now?")

    def test_06_start_on_login_screen_matches_document(self) -> None:
        # V7.3.1 修复：文档曾引用不存在的 `Start automatically on login/boot?`
        weixin_doc = read("references/weixin-setup-zh.md")
        self.assertNotIn("`Start automatically on login/boot? [Y/n]`", weixin_doc)
        self.assert_screen_in_source("Start the gateway automatically on login/boot")

    def test_07_done_menu_item_exists(self) -> None:
        self.assert_screen_in_source('"Done"')

    def test_08_weixin_env_vars_exist(self) -> None:
        for var in ("WEIXIN_DM_POLICY", "WEIXIN_ALLOWED_USERS", "WEIXIN_GROUP_POLICY"):
            self.assertIn(var, self.source, f"文档引用的环境变量在源码中不存在：{var}")

    def test_09_allowlist_prefills_scanned_user_id(self) -> None:
        # 文档声称“向导自动填入本次扫码用户 ID”，源码必须真的把 user_id 作为默认值
        self.assertRegex(self.source, r"default_allow\s*=\s*user_id")

    def test_10_profile_flag_is_hidden_as_documented(self) -> None:
        # 文档声称 -p 不出现在 --help；若未来版本公开显示，文档说明必须同步更新
        result = subprocess.run(
            [HERMES, "--help"], capture_output=True, text=True, timeout=30, check=False, env=self.hermes_env,
        )
        self.assertNotRegex(result.stdout, r"\s-p[,\s]")
        setup_doc = read("references/setup-guide.md")
        self.assertIn("`-p` 是隐藏全局参数", setup_doc)

    def test_11_documented_config_keys_are_resolvable(self) -> None:
        for key in (
            "display.language",
            "skills.write_approval",
            "terminal.cwd",
            "approvals.mode",
            "cron.wrap_response",
            "terminal.backend",
        ):
            result = subprocess.run(
                [HERMES, "-p", "sourcefacttest", "config", "get", key],
                capture_output=True, text=True, timeout=30, check=False, env=self.hermes_env,
            )
            self.assertEqual(result.returncode, 0, f"配置键无法读取：{key}：{result.stderr.strip()}")

    def test_12_cron_deliver_platform_only_uses_home_channel(self) -> None:
        # tools.md 声称 --deliver weixin（不带 chat_id）投递到 home channel
        delivery = (HERMES_INSTALL / "gateway" / "delivery.py").read_text(encoding="utf-8")
        self.assertIn("None means use home channel", delivery)

    def test_13_pairing_approve_help_lists_platforms(self) -> None:
        # setup-guide.md 声称平台名以 --help 实际列出为准；记录当前列出值供审计
        result = subprocess.run(
            [HERMES, "-p", "sourcefacttest", "pairing", "approve", "--help"],
            capture_output=True, text=True, timeout=30, check=False, env=self.hermes_env,
        )
        self.assertEqual(result.returncode, 0)
        setup_doc = read("references/setup-guide.md")
        self.assertIn("实际列出为准", setup_doc)

    def test_14_platform_selection_screen_exists(self) -> None:
        self.assert_screen_in_source("Select a platform to configure:")
        self.assert_screen_in_source("Weixin / WeChat")

    def test_15_memory_injection_config_keys_are_resolvable(self) -> None:
        for key in ("memory.memory_enabled", "memory.user_profile_enabled"):
            result = subprocess.run(
                [HERMES, "-p", "sourcefacttest", "config", "get", key],
                capture_output=True, text=True, timeout=30, check=False, env=self.hermes_env,
            )
            self.assertEqual(result.returncode, 0, f"记忆注入配置键无法读取：{key}")

    def test_16_weixin_reasoning_override_wins_over_global_setting(self) -> None:
        module_path = HERMES_INSTALL / "gateway" / "display_config.py"
        spec = importlib.util.spec_from_file_location("hermes_display_config_fact", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = {
            "display": {
                "show_reasoning": True,
                "platforms": {"weixin": {"show_reasoning": False}},
            }
        }
        self.assertFalse(module.resolve_display_setting(config, "weixin", "show_reasoning", True))

    def test_17_memory_prompt_injection_is_gated_by_both_config_flags(self) -> None:
        init_source = (HERMES_INSTALL / "agent" / "agent_init.py").read_text(encoding="utf-8")
        prompt_source = (HERMES_INSTALL / "agent" / "system_prompt.py").read_text(encoding="utf-8")
        self.assertIn('agent._memory_enabled = mem_config.get("memory_enabled", False)', init_source)
        self.assertIn('agent._user_profile_enabled = mem_config.get("user_profile_enabled", False)', init_source)
        self.assertIn("if agent._memory_enabled:", prompt_source)
        self.assertIn("if agent._user_profile_enabled:", prompt_source)

    def test_18_weixin_setup_writes_observable_markers_in_order(self) -> None:
        gateway_source = (HERMES_INSTALL / "hermes_cli" / "gateway.py").read_text(encoding="utf-8")
        positions = [
            gateway_source.index('save_env_value("WEIXIN_ACCOUNT_ID"'),
            gateway_source.index('save_env_value("WEIXIN_DM_POLICY"'),
            gateway_source.index('save_env_value("WEIXIN_GROUP_POLICY"'),
            gateway_source.index('save_env_value("WEIXIN_HOME_CHANNEL"'),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_19_weixin_config_extra_precedes_env_policy(self) -> None:
        source = (HERMES_INSTALL / "gateway" / "platforms" / "weixin.py").read_text(encoding="utf-8")
        self.assertIn('extra.get("dm_policy") or os.getenv("WEIXIN_DM_POLICY"', source)
        self.assertIn('extra.get("group_policy") or os.getenv("WEIXIN_GROUP_POLICY"', source)
        self.assertIn('allow_from = extra.get("allow_from")', source)
        self.assertIn('os.getenv("GATEWAY_ALLOW_ALL_USERS"', source)
        self.assertIn('os.getenv("WEIXIN_ALLOW_ALL_USERS"', source)
        security = read("references/security-boundary.md")
        self.assertIn("`extra` 都可能改变 `.env`", security)
        self.assertIn("config_access_overrides_absent", read("references/flow-contract.json"))

    def test_20_named_profile_has_global_and_process_secret_fallbacks(self) -> None:
        auth_source = (HERMES_INSTALL / "hermes_cli" / "auth.py").read_text(encoding="utf-8")
        scope_source = (HERMES_INSTALL / "agent" / "secret_scope.py").read_text(encoding="utf-8")
        self.assertIn("falls back to the global-root ``auth.json``", auth_source)
        self.assertIn("shared/nous_auth.json", auth_source)
        self.assertIn("Multiplex off: the scope is an overlay over the process environment", scope_source)
        setup = read("references/setup-guide.md")
        self.assertIn("命名 Profile 只隔离状态目录，不等于凭据隔离", setup)

    def test_21_chat_only_time_is_optional_when_terminal_is_disabled(self) -> None:
        prompt_source = (HERMES_INSTALL / "agent" / "prompt_builder.py").read_text(encoding="utf-8")
        self.assertIn("Current time, date, timezone → use terminal", prompt_source)
        contract = read("references/flow-contract.json")
        soul = read("assets/SOUL.zh-CN.md")
        self.assertIn('"terminal"', contract)
        self.assertIn("当前没有启用实时查询", soul)
        self.assertIn("不能为了报时自行启用终端", soul)

    def test_22_weixin_credentials_and_media_cache_are_disclosed(self) -> None:
        source = (HERMES_INSTALL / "gateway" / "platforms" / "weixin.py").read_text(encoding="utf-8")
        self.assertIn('Path(hermes_home) / "weixin" / "accounts"', source)
        self.assertIn("await self._collect_media(item, media_paths, media_types)", source)
        security = read("references/security-boundary.md")
        checker = read("scripts/check_profile_safety.py")
        self.assertIn("weixin/accounts", security)
        self.assertIn('profile_dir / "cache"', checker)

    def test_23_profile_show_requires_positional_profile_name(self) -> None:
        result = subprocess.run(
            [HERMES, "profile", "show", "sourcefacttest"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=self.hermes_env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hermes profile show <Profile>", read("references/setup-guide.md"))

    def test_24_qr_wizard_keys_fit_post_qr_allowlist(self) -> None:
        gateway_source = (HERMES_INSTALL / "hermes_cli" / "gateway.py").read_text(encoding="utf-8")
        block = gateway_source.split("def _setup_weixin():", 1)[1].split("\ndef ", 1)[0]
        written = set(re.findall(r'save_env_value\(\s*"([A-Z0-9_]+)"', block))
        expected = {
            "WEIXIN_ACCOUNT_ID",
            "WEIXIN_TOKEN",
            "WEIXIN_BASE_URL",
            "WEIXIN_CDN_BASE_URL",
            "WEIXIN_DM_POLICY",
            "WEIXIN_ALLOW_ALL_USERS",
            "WEIXIN_ALLOWED_USERS",
            "WEIXIN_GROUP_POLICY",
            "WEIXIN_GROUP_ALLOWED_USERS",
            "WEIXIN_HOME_CHANNEL",
        }
        self.assertEqual(written, expected)
        module_path = ROOT / "scripts" / "check_profile_safety.py"
        spec = importlib.util.spec_from_file_location("profile_safety_source_fact", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(written <= module.ALLOWED_PROFILE_WEIXIN_KEYS)

    def test_25_weixin_settings_are_saved_before_post_setup_service_prompts(self) -> None:
        gateway_source = (HERMES_INSTALL / "hermes_cli" / "gateway.py").read_text(encoding="utf-8")
        weixin_block = gateway_source.split("def _setup_weixin():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('save_env_value("WEIXIN_HOME_CHANNEL"', weixin_block)
        self.assertLess(
            gateway_source.index('save_env_value("WEIXIN_HOME_CHANNEL"'),
            gateway_source.index("# ── Post-setup: offer to install/restart gateway ──"),
        )
        direct_helper = read("scripts/setup_weixin_direct.py")
        self.assertIn("save_safe_configuration", direct_helper)
        self.assertNotIn('"gateway", "setup"', direct_helper)

    def test_26_post_setup_service_prompts_default_to_yes(self) -> None:
        gateway_source = (HERMES_INSTALL / "hermes_cli" / "gateway.py").read_text(encoding="utf-8")
        post_setup = gateway_source.split("# ── Post-setup: offer to install/restart gateway ──", 1)[1]
        self.assertIn('prompt_yes_no("  Start the gateway now?", True)', post_setup)
        self.assertRegex(
            post_setup,
            r'prompt_yes_no\(\s*f"  Start the gateway automatically on login/boot[\s\S]*?True,\s*\)',
        )

    def test_27_uninstalled_and_stopped_gateway_has_distinct_status(self) -> None:
        gateway_source = (HERMES_INSTALL / "hermes_cli" / "gateway.py").read_text(encoding="utf-8")
        status_branch = gateway_source.split('elif subcmd == "status":', 1)[1].split('\n    elif subcmd == ', 1)[0]
        self.assertIn("get_systemd_unit_path(system=False).exists()", status_branch)
        self.assertIn("get_launchd_plist_path().exists()", status_branch)
        self.assertIn("_windows_service_installed", status_branch)
        self.assertIn('print("✗ Gateway is not running")', status_branch)
        self.assertIn("只有官方 v0.40.0 的精确状态行 `Gateway is not running` 才放行", read("references/security-boundary.md"))

    def test_28_official_optional_weixin_keys_and_qwen_auth_path_are_guarded(self) -> None:
        config_source = (HERMES_INSTALL / "gateway" / "config.py").read_text(encoding="utf-8")
        optional = {"WEIXIN_HOME_CHANNEL_NAME", "WEIXIN_SPLIT_MULTILINE_MESSAGES"}
        for key in optional:
            self.assertIn(key, config_source)
        module_path = ROOT / "scripts" / "check_profile_safety.py"
        spec = importlib.util.spec_from_file_location("profile_safety_optional_source_fact", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(optional <= module.ALLOWED_PROFILE_WEIXIN_KEYS)
        auth_source = (HERMES_INSTALL / "hermes_cli" / "auth.py").read_text(encoding="utf-8")
        self.assertIn('Path.home() / ".qwen" / "oauth_creds.json"', auth_source)
        model_flows = (HERMES_INSTALL / "hermes_cli" / "model_setup_flows.py").read_text(encoding="utf-8")
        self.assertIn("qwen auth qwen-oauth", model_flows)
        self.assertIn('hermes_root / "os-home" / ".qwen"', read("scripts/check_profile_safety.py"))
        self.assertIn('runtime_home / ".qwen"', read("scripts/check_profile_safety.py"))
        self.assertIn("isolation_guard.py run-qwen-auth", read("references/model-routing.md"))

    def test_29_provider_scoped_auth_route_is_advertised(self) -> None:
        result = subprocess.run(
            [HERMES, "-p", "sourcefacttest", "auth", "add", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=self.hermes_env,
        )
        help_text = f"{result.stdout}\n{result.stderr}".lower()
        self.assertEqual(result.returncode, 0)
        self.assertIn("provider", help_text)
        self.assertIn("--type", help_text)
        self.assertIn("oauth", help_text)
        self.assertIn("auth add <provider> --type oauth", read("references/model-routing.md"))
        self.assertIn("禁止运行通用 `model` 向导", read("references/model-routing.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

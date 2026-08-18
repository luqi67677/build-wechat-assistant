#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow_policy import VERSION
from validate_skill import (
    count_source_fact_results,
    run_flow_tests,
    run_hermes_contract,
    validate_no_private_identifiers,
    validate_optional_runtime_arguments,
)


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class InstallAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(read("references/flow-contract.json"))
        cls.readme = read("README.md")
        cls.skill = read("SKILL.md")
        cls.setup = read("references/setup-guide.md")
        cls.install = read("references/install-skill.md")
        cls.security = read("references/security-boundary.md")
        cls.acceptance = read("references/acceptance.md")
        cls.cloud = read("references/cloud-deployment.md")
        cls.model = read("references/model-routing.md")
        cls.weixin = read("references/weixin-setup-zh.md")
        cls.faq = read("references/operation-faq.md")

    def test_01_schema_and_version(self) -> None:
        self.assertEqual(self.contract["schema_version"], 7)
        self.assertEqual(self.contract["skill_version"], VERSION)

    def test_python311_source_fact_output_is_counted(self) -> None:
        output = "\n".join(
            (
                "test_one (test_source_facts.WizardSourceFactTests.test_one) ... ok",
                "test_two (test_source_facts.WizardSourceFactTests.test_two) ... ok",
                "test_other (test_source_facts.OtherTests.test_other) ... ok",
            )
        )
        self.assertEqual(count_source_fact_results(output), 2)

    def test_cli_contract_outer_timeout_fails_closed_after_600_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            hermes = Path(raw_temp) / "hermes"
            hermes.touch()
            failures: list[str] = []
            with patch(
                "validate_skill.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["checker"], 600),
            ) as run:
                result = run_hermes_contract(ROOT, hermes, failures)
        self.assertIsNone(result)
        self.assertEqual(run.call_args.kwargs["timeout"], 600)
        self.assertTrue(any("执行超时或不可用" in failure for failure in failures))

    def test_flow_regression_timeout_fails_closed_after_180_seconds(self) -> None:
        failures: list[str] = []
        with patch(
            "validate_skill.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["unittest"], 180),
        ) as run:
            verified = run_flow_tests(ROOT, failures, None)
        self.assertFalse(verified)
        self.assertEqual(run.call_args.kwargs["timeout"], 180)
        self.assertTrue(any("流程回归执行超时或不可用" in failure for failure in failures))

    def test_mcp_runtime_arguments_must_be_provided_as_a_pair(self) -> None:
        failures: list[str] = []
        validate_optional_runtime_arguments(Path("/python"), None, failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("--mcp-python 还需要 --node", failures[0])

        failures.clear()
        validate_optional_runtime_arguments(None, Path("/node"), failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("--node 只与 --mcp-python", failures[0])

    def test_private_identifiers_are_rejected_across_release_files(self) -> None:
        failures: list[str] = []
        validate_no_private_identifiers("profile=" + "xiao" + "qi", failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("私人助手或账号标识", failures[0])

    def test_02_supported_platform_matrix(self) -> None:
        supported = set(self.contract["platform_gate"]["supported"])
        self.assertIn("macos_apple_silicon", supported)
        self.assertIn("windows_10_11_x86_64_aarch64", supported)
        self.assertIn("linux_x86_64_aarch64", supported)

    def test_03_intel_mac_is_blocked_before_download(self) -> None:
        self.assertIn("macos_intel", self.contract["platform_gate"]["unsupported"])
        self.assertIn("Intel Mac", self.setup)

    def test_04_windows_preflight_is_powershell(self) -> None:
        self.assertIn("Get-Command hermes", self.setup)
        self.assertIn("Get-CimInstance", self.setup)
        self.assertIn("不得运行 `uname`", self.skill)

    def test_05_linux_local_dependencies_are_real_commands(self) -> None:
        for command in ("git --version", "curl --version", "xz --version"):
            self.assertIn(command, self.setup)

    def test_06_macos_checks_arch_and_real_dependencies(self) -> None:
        for command in ("uname -m", "sw_vers -productVersion", "git --version", "g++ --version"):
            self.assertIn(command, self.setup)

    def test_07_capability_gate_is_explicit(self) -> None:
        capabilities = set(self.contract["platform_gate"]["required_hermes_capabilities"])
        self.assertIn("profile_selector", capabilities)
        self.assertIn("gateway_run_foreground", capabilities)
        self.assertIn("platform_tool_policy", capabilities)
        self.assertIn("clean_root_cli_contract_check", capabilities)
        self.assertIn("pre_qr_exact_tool_gate", capabilities)

    def test_08_single_source_has_posix_and_windows_link_steps(self) -> None:
        self.assertIn("ln -s", self.install)
        self.assertIn("New-Item -ItemType Junction", self.install)

    def test_09_hermes_discovery_uses_actual_home_and_profile(self) -> None:
        self.assertIn("HERMES_HOME", self.install)
        self.assertIn("%LOCALAPPDATA%", self.install)
        self.assertIn("命名 Profile", self.install)

    def test_10_broken_link_has_external_recovery(self) -> None:
        self.assertIn("Vault 移动后的恢复", self.install)
        self.assertIn("从 Obsidian 直接打开本文件", self.install)

    def test_11_chatgpt_codex_and_hermes_scope_is_explicit(self) -> None:
        self.assertIn("普通 ChatGPT", self.install)
        self.assertIn("Codex 本地任务", self.install)
        self.assertIn("Hermes", self.install)

    def test_readme_hermes_install_uses_actual_profile_home(self) -> None:
        self.assertIn("确认目标 Profile 的实际根目录", self.readme)
        self.assertIn("不要把 `~/.hermes/skills/` 当作所有 Profile 的固定路径", self.readme)
        self.assertNotIn(
            "git clone https://github.com/luqi67677/build-wechat-assistant.git ~/.hermes/skills/build-wechat-assistant",
            self.readme,
        )

    def test_readme_install_requires_content_and_trigger_acceptance(self) -> None:
        self.assertIn("references/flow-contract.json", self.readme)
        self.assertIn("assets/SOUL.zh-CN.md", self.readme)
        self.assertIn("新会话能用", self.readme)
        self.assertIn("只在列表里出现名称不算安装完成", self.readme)

    def test_12_local_service_is_absent_before_persona(self) -> None:
        lifecycle = self.contract["local_service_lifecycle"]
        self.assertEqual(lifecycle["before_persona"], "service_not_installed_and_gateway_not_running")
        self.assertIn("第 4 步不安装本地受管服务", self.skill)

    def test_13_local_acceptance_uses_foreground_gateway(self) -> None:
        lifecycle = self.contract["local_service_lifecycle"]
        self.assertEqual(lifecycle["acceptance_command"], "hermes -p <Profile> gateway run")
        self.assertEqual(
            lifecycle["protected_foreground_stop_command"],
            "isolation_guard.py run ... -- -p <Profile> gateway stop",
        )
        self.assertTrue(lifecycle["protected_foreground_stop_is_profile_scoped"])
        self.assertFalse(lifecycle["protected_foreground_restart_counts_as_persistence"])
        self.assertIn("hermes -p <Profile> gateway run", self.skill)
        self.assertIn("仍只能登记“聊天可用”", self.acceptance)

    def test_14_local_persistence_is_post_acceptance_and_explicit(self) -> None:
        lifecycle = self.contract["local_service_lifecycle"]
        self.assertTrue(lifecycle["persistence_only_after_acceptance"])
        self.assertIn("--force --start-now --start-on-login", lifecycle["persistence_command"])

    def test_15_every_cloud_command_binds_profile(self) -> None:
        for command in self.contract["cloud_service"]["commands"].values():
            self.assertIn("-p <Profile>", command)
            self.assertNotIn("--system", command)
            self.assertNotIn("sudo", command)

    def test_16_cloud_persistence_forces_enable_transition(self) -> None:
        command = self.contract["cloud_service"]["commands"]["persist_stage"]
        self.assertIn("--force", command)
        self.assertIn("--no-start-now", command)
        self.assertIn("--start-on-login", command)
        self.assertNotIn(" --start-now", command)

    def test_17_cloud_preflight_proves_systemd_runtime(self) -> None:
        required = set(self.contract["cloud_service"]["preflight_required"])
        self.assertIn("systemd_is_running", required)
        self.assertIn("user_scope_unit_manageable", required)
        self.assertIn("linger_enabled", required)
        self.assertIn("systemctl is-system-running", self.cloud)

    def test_18_cloud_unit_pins_profile_home(self) -> None:
        service = self.contract["cloud_service"]
        self.assertTrue(service["profile_binding_required_for_every_command"])
        self.assertTrue(service["unit_must_pin_profile_home"])
        self.assertIn("HERMES_HOME", self.cloud)
        self.assertIn("unit `Environment=`", self.cloud)
        self.assertIn("`EnvironmentFile=`", self.cloud)

    def test_19_pre_qr_gate_is_static_only(self) -> None:
        checks = self.contract["execution_boundary"]["pre_qr_static_checks"]
        self.assertGreaterEqual(len(checks), 4)
        self.assertNotIn("dm_allowlist_is_nonempty_and_owner_only", checks)
        pre_start = self.contract["execution_boundary"]["post_qr_pre_start_checks"]
        self.assertIn("dm_allowlist_is_nonempty_and_owner_only", pre_start)
        self.assertIn("扫码前静态门禁", self.security)
        self.assertIn("扫码后启动前门禁", self.security)
        self.assertIn("不能完成 Weixin 提示注入", self.security)

    def test_20_runtime_negative_tests_are_post_qr(self) -> None:
        tests = self.contract["execution_boundary"]["post_qr_runtime_negative_tests"]
        self.assertGreaterEqual(len(tests), 5)
        self.assertIn("扫码后运行时门禁", self.security)

    def test_21_chat_only_disables_skills_toolset(self) -> None:
        disabled = self.contract["execution_boundary"]["chat_only_disable_if_present"]
        self.assertIn("skills", disabled)
        self.assertIn("skill_manage", self.security)

    def test_22_skills_write_requires_approval_if_enabled(self) -> None:
        self.assertTrue(self.contract["execution_boundary"]["skills_write_approval_required_if_enabled"])
        self.assertIn("skills.write_approval true", self.security)

    def test_23_optional_tools_are_not_always_enabled(self) -> None:
        boundary = self.contract["execution_boundary"]
        self.assertEqual(boundary["chat_only_always_enabled_toolsets"], ["clarify"])
        self.assertNotIn("skills", boundary["chat_only_enable_only_if_selected_and_verified"])

    def test_24_access_control_has_required_configuration_evidence(self) -> None:
        access = self.contract["weixin_access"]
        self.assertTrue(access["configuration_evidence_required"])
        self.assertTrue(access["allowed_users_exact_owner_only"])

    def test_25_second_account_is_optional_and_honestly_reported(self) -> None:
        access = self.contract["weixin_access"]
        self.assertFalse(access["second_account_negative_test_required_for_base_completion"])
        self.assertEqual(access["second_account_negative_test_status_when_unavailable"], "not_verified")

    def test_26_qr_url_avoids_agent_stdout_capture(self) -> None:
        self.assertIn("不会把 stdout 回传", self.weixin)
        self.assertIn("普通 Agent PTY", self.weixin)

    def test_27_api_key_clipboard_claim_is_honest(self) -> None:
        self.assertIn("不能证明操作系统剪贴板历史", self.model)
        self.assertNotIn("不进入对话、普通文档、命令参数、脚本内容、日志或剪贴板历史", self.model)

    def test_28_recovery_semantics_are_platform_specific(self) -> None:
        lifecycle = self.contract["local_service_lifecycle"]
        self.assertEqual(lifecycle["macos_recovery_point"], "after_target_user_login")
        self.assertEqual(lifecycle["windows_recovery_point"], "after_target_user_login")
        self.assertIn("目标用户登录后", self.weixin)

    def test_29_quality_acceptance_covers_soak_and_recovery(self) -> None:
        required = set(self.contract["quality_acceptance"]["release_grade_required"])
        self.assertIn("ten_turn_chinese_soak", required)
        self.assertIn("long_message_round_trip", required)
        self.assertIn("gateway_restart_recovery", required)
        self.assertIn("连续进行 10 轮", self.skill)

    def test_31_external_recipient_does_not_need_authors_obsidian(self) -> None:
        self.assertIn("外部接收者安装", self.install)
        self.assertIn("不需要拥有作者的 Obsidian", self.install)
        for secret_artifact in ("`.env`", "token", "会话", "日志", "备份"):
            self.assertIn(secret_artifact, self.install)

    def test_32_profile_lifecycle_precedes_model_and_weixin(self) -> None:
        lifecycle = self.contract["profile_lifecycle"]
        self.assertTrue(lifecycle["required_before_stateful_setup"])
        self.assertFalse(lifecycle["clone_for_new_user"])
        self.assertIn("hermes profile create wechatassistant", self.skill)

    def test_33_windows_acl_and_effective_access_checker_are_required(self) -> None:
        self.assertIn("profile_secret_acl_private", self.contract["platform_gate"]["windows_required_checks"])
        self.assertEqual(self.contract["weixin_access"]["prestart_checker"], "scripts/check_profile_safety.py")
        self.assertIn("config_access_overrides_absent", self.contract["execution_boundary"]["post_qr_pre_start_checks"])

    def test_34_completion_states_cannot_be_merged(self) -> None:
        completion = self.contract["completion_states"]
        self.assertTrue(completion["simulation_cannot_satisfy_real_state"])
        self.assertIn("real_machine_reboot", completion["boot_recovery_verified"])
        self.assertIn("聊天可用", self.skill)
        self.assertIn("开机恢复已验证", self.skill)

    def test_35_cloud_preflight_dependencies_exist_before_script_runs(self) -> None:
        sequence = self.contract["cloud_service"]["deployment_sequence"]
        self.assertLess(sequence.index("service_account_created"), sequence.index("service_account_preflight_from_release"))
        self.assertLess(sequence.index("linger_enabled"), sequence.index("service_account_preflight_from_release"))
        self.assertLess(sequence.index("release_uploaded_and_hash_verified"), sequence.index("service_account_preflight_from_release"))
        self.assertLess(sequence.index("model_host_selected_without_secret"), sequence.index("service_account_preflight_from_release"))
        self.assertIn("不能运行尚不存在的发布包脚本", self.cloud)

    def test_36_shared_server_requires_resource_ledger_before_write(self) -> None:
        service = self.contract["cloud_service"]
        sequence = service["deployment_sequence"]
        self.assertTrue(service["test_resource_ledger_required_before_write_on_shared_server"])
        self.assertLess(sequence.index("test_resource_ledger_frozen"), sequence.index("service_account_created"))
        self.assertIn("测试资源台账", self.cloud)

    def test_37_cleanup_is_exact_and_test_scoped(self) -> None:
        self.assertTrue(self.contract["cloud_service"]["cleanup_only_exact_test_created_resources"])
        for phrase in ("本轮新建", "禁止 glob", "preview-cleanup", "verify-cleanup", "既有生产助手"):
            self.assertIn(phrase, self.cloud + self.faq)

    def test_38_systemd_states_are_stage_specific(self) -> None:
        guard = self.contract["cloud_service"]["systemd_environment_guard"]
        for key in (
            "stage_must_be_inactive_disabled",
            "prestart_must_be_inactive",
            "prerestart_must_be_active",
            "runtime_expected_enabled_required",
        ):
            self.assertTrue(guard[key])
        self.assertIn("check-prerestart", self.cloud)
        self.assertIn("--expect-enabled enabled|disabled", self.cloud)

    def test_39_local_service_binding_is_not_automatically_claimed(self) -> None:
        self.assertIn("macOS launchd", self.skill)
        self.assertIn("等价自动检查器", self.skill)
        self.assertIn("只保留“聊天可用”", self.skill)

    def test_36_cloud_admin_path_does_not_require_sudo_for_root(self) -> None:
        service = self.contract["cloud_service"]
        self.assertEqual(service["admin_privilege_path"], "root_without_sudo_or_nonroot_with_sudo")
        self.assertIn("enable_linger_as_root", service["admin_commands"])
        self.assertIn("enable_linger_with_sudo", service["admin_commands"])
        self.assertIn("root 不要求安装 sudo", self.cloud)

    def test_37_systemd_environment_guard_closes_manager_unit_and_runtime_layers(self) -> None:
        service = self.contract["cloud_service"]
        sequence = service["deployment_sequence"]
        self.assertLess(sequence.index("service_staged_stopped_disabled"), sequence.index("systemd_env_guard_installed"))
        self.assertLess(sequence.index("systemd_prestart_env_verified"), sequence.index("service_started"))
        guard = service["systemd_environment_guard"]
        self.assertTrue(guard["manager_check_before_install"])
        self.assertTrue(guard["dropin_unsets_current_weixin_keys"])
        self.assertTrue(guard["runtime_proc_environ_boolean_check_required"])
        self.assertIn("UnsetEnvironment=", self.cloud)
        self.assertIn("check-runtime", self.cloud)

    def test_38_cloud_persistence_rechecks_rewritten_unit_before_explicit_start(self) -> None:
        sequence = self.contract["cloud_service"]["systemd_environment_guard"]["persistence_sequence"]
        self.assertEqual(
            sequence,
            [
                "stop_verified",
                "force_rewrite_enabled_but_stopped",
                "prestart_check_on_rewritten_unit",
                "explicit_gateway_start",
                "runtime_environment_check",
            ],
        )
        self.assertIn("--force --no-start-now --start-on-login", self.cloud)

    def test_39_pre_qr_gate_is_executable_and_fail_closed(self) -> None:
        boundary = self.contract["execution_boundary"]
        self.assertEqual(boundary["pre_qr_checker"], "scripts/check_pre_qr_safety.py")
        self.assertEqual(boundary["chat_only_effective_enabled_toolsets_exact"], ["clarify"])
        self.assertFalse(boundary["chat_only_mcp_servers_allowed"])
        self.assertIn("check_pre_qr_safety.py", self.security)

    def test_40_model_probe_is_inside_the_same_minimal_chat_boundary(self) -> None:
        isolation = self.contract["acceptance_isolation"]
        boundary = self.contract["execution_boundary"]
        self.assertTrue(isolation["chat_safety_baseline_required_before_model_auth"])
        self.assertTrue(isolation["chat_safety_recheck_required_after_model_auth_before_probe"])
        self.assertTrue(isolation["profile_permissions_rehardened_after_model_probe_before_weixin"])
        self.assertTrue(boundary["workspace_required_before_model_auth"])
        self.assertEqual(boundary["pre_model_baseline_helper"], "scripts/apply_chat_safety_baseline.py")
        self.assertEqual(boundary["pre_model_and_pre_qr_checker"], "scripts/check_pre_qr_safety.py")
        self.assertTrue(boundary["post_model_probe_baseline_reapply_required"])
        self.assertIn("execution_boundary", self.contract["step_router"]["step_2_hermes"])
        self.assertIn("execution_boundary", self.contract["step_router"]["step_3_model"])
        self.assertIn("apply_chat_safety_baseline.py", self.skill)
        self.assertIn("模型认证后、首次真实调用前再次运行", self.skill)
        self.assertIn("模型探测可能新建权限过宽的 Profile 缓存", self.skill)

    def test_41_protected_mode_does_not_use_machine_level_dashboard_or_raw_deep_status(self) -> None:
        provenance = self.contract["credential_provenance"]
        self.assertFalse(provenance["protected_mode_dashboard_secret_entry_allowed"])
        self.assertFalse(provenance["raw_deep_status_allowed_in_agent_logs"])
        self.assertIn("受保护验收不得使用机器级 Hermes Dashboard", self.model)
        self.assertNotIn("hermes -p <Profile> status --deep", self.skill + self.setup + self.model)

    def test_42_cloud_model_auth_is_bracketed_by_safety_gates(self) -> None:
        sequence = self.contract["cloud_service"]["deployment_sequence"]
        self.assertLess(sequence.index("chat_safety_baseline_applied"), sequence.index("model_authenticated"))
        self.assertLess(sequence.index("pre_model_safety_gate_passed"), sequence.index("model_authenticated"))
        self.assertLess(sequence.index("model_authenticated"), sequence.index("post_model_safety_gate_passed"))
        self.assertLess(sequence.index("post_model_safety_gate_passed"), sequence.index("model_probe_passed"))
        self.assertLess(sequence.index("model_probe_passed"), sequence.index("post_model_probe_permissions_rehardened"))
        self.assertLess(sequence.index("post_model_probe_permissions_rehardened"), sequence.index("weixin_authenticated"))

    def test_43_cloud_release_extraction_has_an_executable_fail_closed_verifier(self) -> None:
        service = self.contract["cloud_service"]
        self.assertEqual(service["release_package_verifier"], "scripts/verify_release_package.py")
        self.assertTrue(service["release_package_extract_without_overwrite"])
        self.assertTrue(service["release_package_artifacts_outside_skill_tree"])
        self.assertTrue(service["release_package_rejects_unsafe_archive_entries"])
        self.assertIn("verify_release_package.py", self.cloud)
        self.assertIn("不覆盖", self.cloud)

    def test_44_interactive_handoff_is_bound_to_the_operation_mode_and_runtime_home(self) -> None:
        boundary = self.contract["execution_boundary"]
        handoff = self.contract["conversation_first_handoff"]
        self.assertEqual(
            boundary["interactive_runner_by_mode"],
            {
                "ordinary_new_or_authorized": "direct_profile_bound",
                "protected_acceptance": "isolation_guard_run",
                "cloud_new_or_test": "isolation_guard_run_cloud",
            },
        )
        self.assertTrue(boundary["cloud_interactive_home_matches_service_runtime_home"])
        self.assertTrue(boundary["user_visible_command_placeholders_forbidden"])
        self.assertTrue(boundary["protected_and_cloud_sensitive_interactions_require_trusted_tty"])
        self.assertEqual(
            boundary["external_qwen_oauth_runner"],
            "isolation_guard.py run-qwen-auth",
        )
        self.assertTrue(
            self.contract["acceptance_isolation"][
                "fresh_profile_gate_rejects_known_runtime_home_auth_sources"
            ]
        )
        self.assertIn("run-cloud", self.cloud)
        self.assertIn("trusted_tty_required", self.skill)
        self.assertIn("trusted_tty_required", self.model)
        self.assertIn("trusted_tty_required", self.weixin)
        self.assertIn("trusted_tty_required", self.cloud)
        self.assertIn("不得把含 `<...>` 的占位命令交给用户", self.skill)
        self.assertFalse(handoff["user_terminal_typing_required_by_default"])
        self.assertTrue(handoff["agent_executes_noninteractive_steps"])
        self.assertEqual(handoff["trusted_handoff_launcher"], "scripts/launch_trusted_handoff.py")
        self.assertEqual(handoff["manual_terminal_max_commands"], 1)
        self.assertTrue(handoff["manual_terminal_command_must_be_atomic"])
        self.assertTrue(handoff["manual_terminal_command_must_be_fully_resolved"])
        self.assertFalse(handoff["generic_model_wizard_allowed_before_provider_is_fixed"])
        self.assertEqual(handoff["provider_oauth_route"], "auth add <provider> --type oauth")
        self.assertEqual(handoff["provider_api_key_route"], "auth add <provider> --type api_key")
        self.assertTrue(handoff["cloud_remote_service_user_required"])
        self.assertTrue(handoff["cloud_remote_account_switch_required"])
        self.assertTrue(handoff["cloud_direct_requires_matching_explicit_ssh_user"])
        self.assertEqual(
            handoff["cloud_remote_account_switch_modes"],
            ["root-runuser", "sudo", "direct"],
        )

    def test_45_tty_guides_use_agent_launched_trusted_handoff(self) -> None:
        self.assertIn("launch_trusted_handoff.py", self.skill)
        self.assertIn("launch_trusted_handoff.py", self.model)
        self.assertIn("run-cloud", self.model)
        self.assertIn("launch_trusted_handoff.py", self.weixin)
        self.assertIn("run-cloud", self.weixin)
        self.assertIn("launch_trusted_handoff.py", self.cloud)
        self.assertIn("远端服务账号", self.cloud)
        self.assertIn("root-runuser", self.cloud)
        self.assertIn("用户不输入终端命令", self.model)
        self.assertIn("用户不输入终端或 SSH 命令", self.weixin)
        self.assertNotIn("配置、模型和扫码命令由 `isolation_guard.py run --root", self.cloud)

    def test_46_direct_checkers_do_not_write_bytecode_into_release_tree(self) -> None:
        entries = (
            "apply_chat_safety_baseline.py",
            "check_cloud_preflight.py",
            "check_pre_qr_safety.py",
            "systemd_env_guard.py",
            "validate_skill.py",
        )
        with tempfile.TemporaryDirectory() as raw_temp:
            copied_root = Path(raw_temp) / "skill"
            shutil.copytree(ROOT, copied_root)
            scripts = copied_root / "scripts"
            env = os.environ.copy()
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            for entry in entries:
                result = subprocess.run(
                    [sys.executable, str(scripts / entry), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, entry)
                self.assertFalse((scripts / "__pycache__").exists(), entry)

    def test_47_release_gate_includes_optional_mcp_runtime(self) -> None:
        validator = read("scripts/validate_skill.py")
        self.assertIn("--mcp-python", self.install)
        self.assertIn("--node", self.install)
        self.assertIn("只会伪造版本字符串", self.install)
        self.assertIn("scripts/check_optional_mcp_runtime.py", validator)
        self.assertIn("真实飞书写入", self.install)
        self.assertIn("真实 Codex 调用", self.install)

    def test_48_optional_capabilities_have_independent_acceptance_layers(self) -> None:
        self.assertIn("可选能力的独立验收", self.acceptance)
        self.assertIn("工具层、模型层、微信层", self.acceptance)
        self.assertIn("假 Codex 只能证明执行器编排", self.acceptance)
        self.assertIn("本地 delivery 或运行回执不能代替微信送达", self.acceptance)

    def test_30_metadata_and_typography_do_not_mislead(self) -> None:
        metadata = read("agents/openai.yaml")
        all_markdown = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.md"))
        fixture = read("assets/cloud-flow-fixtures.json")
        self.assertNotIn("已安装 V", metadata)
        self.assertNotIn("URL可以", all_markdown)
        self.assertNotIn("API Key", all_markdown)
        self.assertNotIn("Hermes 官网:", all_markdown)
        self.assertNotRegex(fixture, r"\d核|\dGB|\d年")


if __name__ == "__main__":
    unittest.main(verbosity=2)

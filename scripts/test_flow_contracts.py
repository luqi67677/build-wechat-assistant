#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from flow_policy import load_contract, validate_contract, validate_documents


ROOT = Path(__file__).resolve().parents[1]


class FlowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def assert_contract_rejected(self, mutated: dict, expected: str) -> None:
        self.assertTrue(any(expected in item for item in validate_contract(mutated)))

    def assert_document_rejected(self, relative: str, mutated: str, expected: str) -> None:
        failures = validate_documents(ROOT, self.contract, {relative: mutated})
        self.assertTrue(any(expected in item for item in failures), failures)

    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_01_contract_and_documents_pass(self) -> None:
        self.assertEqual(validate_contract(self.contract), [])
        self.assertEqual(validate_documents(ROOT, self.contract), [])

    def test_post_base_upgrade_menu_cannot_drop_daily_automation(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_lifecycle"]["primary_upgrade_menu"] = [
            "knowledge_base",
            "coding_agent",
        ]
        self.assert_contract_rejected(mutant, "日常自动化")

    def test_progress_line_cannot_replace_explanatory_copy(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["progress_display"]["progress_line_cannot_replace_explanation"] = False
        self.assert_contract_rejected(mutant, "进度条")

    def test_each_step_must_explain_visible_success_signal(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["progress_display"]["new_step_explains"].remove("visible_success_signal")
        self.assert_contract_rejected(mutant, "成功标志")

    def test_existing_assistant_target_must_use_user_facing_name(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_lifecycle"]["existing_assistant_target_uses_user_facing_name_first"] = False
        self.assert_contract_rejected(mutant, "名字")

    def test_target_and_authorization_must_be_separate_questions(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_lifecycle"]["target_identity_and_read_authorization_are_separate_questions"] = False
        self.assert_contract_rejected(mutant, "分成两问")

    def test_post_base_upgrade_copy_cannot_drop_daily_automation(self) -> None:
        text = self.read("SKILL.md").replace("C. 设置日常自动化", "C. 暂不提供日常自动化")
        self.assert_document_rejected("SKILL.md", text, "三项能力升级")

    def test_first_response_cannot_drop_novice_role_split(self) -> None:
        text = self.read("SKILL.md").replace("你不需要懂代码", "请自行完成技术配置")
        self.assert_document_rejected("SKILL.md", text, "首次回应")

    def test_every_step_must_load_novice_guidance(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["step_router"]["step_3_model"].remove("novice_guidance")
        self.assert_contract_rejected(mutant, "每个用户可见步骤")

    def test_dependency_install_cannot_precede_capability_choice(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["novice_guidance"]["dependency_installation_before_capability_and_channel_selection"] = True
        self.assert_contract_rejected(mutant, "不得安装 Docker")

    def test_new_session_prompt_must_explain_all_three_choices(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["novice_guidance"]["new_session_prompt_explains_once_always_cancel"] = False
        self.assert_contract_rejected(mutant, "一次、永久和取消")

    def test_new_session_must_recommend_one_time_approval(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["novice_guidance"]["new_session_once_is_recommended"] = False
        self.assert_contract_rejected(mutant, "一次性确认")

    def test_gateway_restart_must_invalidate_pending_new_session_confirmation(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["novice_guidance"]["gateway_restart_invalidates_pending_new_session_confirmation"] = False
        self.assert_contract_rejected(mutant, "网关重启")

    def test_new_session_screen_copy_cannot_be_replaced_by_generic_instruction(self) -> None:
        text = self.read("references/setup-guide.md").replace(
            "`Approve Once` 表示“只确认这一次新建会话”",
            "按任意确认即可",
            1,
        )
        self.assert_document_rejected("references/setup-guide.md", text, "逐项中文解释")

    def test_optional_router_must_cover_unknown_state(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["shared_availability_branches"] = ["already_has", "does_not_have"]
        self.assert_contract_rejected(mutant, "不确定")

    def test_knowledge_menu_must_cover_users_without_a_knowledge_base(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["initial_menu"].remove("no_existing_knowledge_base")
        self.assert_contract_rejected(mutant, "知识库入口")

    def test_missing_knowledge_base_initializer_cannot_overwrite_existing_path(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["knowledge_base"]["missing_knowledge_base_initialization_is_exclusive"] = False
        self.assert_contract_rejected(mutant, "不得复用或覆盖")

    def test_coding_task_record_updates_must_remain_atomic(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["task_record_updates_are_atomic"] = False
        self.assert_contract_rejected(mutant, "记录更新必须原子化")

    def test_docker_cannot_become_the_knowledge_base_first_question(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["docker_explained_only_after_local_files_selected"] = False
        self.assert_contract_rejected(mutant, "Docker 只能")

    def test_coding_route_must_handle_no_tool_and_no_project(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["coding_agent"]["missing_project_offers_isolated_demo_or_pause"] = False
        self.assert_contract_rejected(mutant, "演示项目")

    def test_automation_cannot_ask_schedule_before_content(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["daily_automation"]["content_selected_before_city_time_or_frequency"] = False
        self.assert_contract_rejected(mutant, "城市、时间或频率")

    def test_documents_must_forbid_docker_before_knowledge_channel(self) -> None:
        text = self.read("references/tools.md").replace(
            "不得在渠道选择前把 Docker 当成前置问题",
            "先安装 Docker 再选择知识库渠道",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "知识库流程")

    def test_model_template_cannot_use_broken_link_placeholder(self) -> None:
        text = self.read("references/model-routing.md").replace(
            "官方介绍与价格：<核验后的官方 HTTPS 链接>",
            "[官方介绍与价格](官方直达地址)",
            1,
        )
        self.assert_document_rejected("references/model-routing.md", text, "链接占位符")

    def test_02_open_dm_contract_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["weixin_access"]["dm_policy"] = "all"
        self.assert_contract_rejected(mutant, "allowlist")

    def test_03_empty_allowlist_contract_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["weixin_access"]["allowed_users_nonempty"] = False
        self.assert_contract_rejected(mutant, "不得为空")

    def test_04_open_dm_document_mutation_is_rejected(self) -> None:
        text = self.read("SKILL.md").replace(
            "直达助手自动写入 allowlist，仅保留本次扫码主人",
            "直达助手自动写入 all，允许所有私聊",
        )
        self.assert_document_rejected("SKILL.md", text, "allowlist")

    def test_05_duplicate_poller_contract_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_cutover"]["duplicate_poller_allowed"] = True
        self.assert_contract_rejected(mutant, "双轮询")

    def test_06_cloud_first_document_mutation_is_rejected(self) -> None:
        text = self.read("references/cloud-deployment.md") + "\n先启动云端，再停止本地。\n"
        self.assert_document_rejected("references/cloud-deployment.md", text, "先开云端")

    def test_07_user_scope_command_mutation_is_rejected(self) -> None:
        text = self.read("references/cloud-deployment.md").replace(
            self.contract["cloud_service"]["commands"]["stop"],
            "hermes gateway stop",
        )
        self.assert_document_rejected("references/cloud-deployment.md", text, "用户服务命令")

    def test_08_search_mandatory_mutation_is_rejected(self) -> None:
        text = self.read("SKILL.md") + "\n搜不了不算搭建完成。\n"
        self.assert_document_rejected("SKILL.md", text, "搜索必过")

    def test_09_one_session_memory_mutation_is_rejected(self) -> None:
        text = self.read("SKILL.md") + "\n同一会话完成记忆验收。\n"
        self.assert_document_rejected("SKILL.md", text, "复用同一会话")

    def test_10_two_session_memory_contract_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["persistent_memory_verification"]["session_count"] = 2
        self.assert_contract_rejected(mutant, "三个新会话")

    def test_11_missing_revoke_negative_test_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["knowledge_revoke"]["steps"].remove("negative_read_test_must_be_denied")
        self.assert_contract_rejected(mutant, "负向读取")

    def test_12_same_phone_claim_mutation_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_qr"]["same_phone_short_url_verified"] = True
        self.assert_contract_rejected(mutant, "同手机")

    def test_13_no_second_screen_contract_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_qr"]["second_trusted_screen_required"] = False
        self.assert_contract_rejected(mutant, "第二块可信屏幕")

    def test_14_zero_downtime_promise_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["model_switch"]["strict_zero_downtime_promised"] = True
        self.assert_contract_rejected(mutant, "绝对零中断")

    def test_15_local_backend_as_sandbox_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["local_backend_is_sandbox"] = True
        self.assert_contract_rejected(mutant, "local 后端")

    def test_16_approval_off_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["forbidden_approval_mode"] = "manual"
        self.assert_contract_rejected(mutant, "approvals.mode=off")

    def test_17_missing_terminal_disable_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_disable_if_present"].remove("terminal")
        self.assert_contract_rejected(mutant, "高风险工具")

    def test_18_external_directory_silent_scan_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["incremental_detection"]["requires_explicit_scope"].remove("user_directories")
        self.assert_contract_rejected(mutant, "用户目录")

    def test_19_legacy_source_patches_are_not_shipped(self) -> None:
        patches = ROOT / "patches"
        self.assertFalse(patches.exists() and any(patches.iterdir()))
        self.assertFalse((ROOT / "references" / "hermes-zh-compat.md").exists())
        self.assertIn("本 Skill 不再随包提供跨版本源码补丁", self.read("SKILL.md"))

    def test_20_missing_xz_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["preflight_required"].remove("xz")
        self.assert_contract_rejected(mutant, "前置检查")

    def test_21_cloud_root_service_user_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["run_as_root"] = True
        self.assert_contract_rejected(mutant, "不得以 root")

    def test_22_windows_and_macos_default_to_desktop(self) -> None:
        skill = self.read("SKILL.md")
        self.assertIn("macOS 和 Windows 普通用户默认使用 Hermes Desktop", skill)

    def test_22a_installer_fallback_cannot_claim_strict_supply_chain(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["platform_gate"]["installer_result_classification"][
            "lockfile_hash_fallback_counts_as_strict_supply_chain_pass"
        ] = True
        self.assert_contract_rejected(mutant, "严格供应链")

    def test_22b_installer_core_and_optional_status_must_be_separate(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["platform_gate"]["installer_result_classification"][
            "core_cli_and_optional_dependencies_have_separate_status"
        ] = False
        self.assert_contract_rejected(mutant, "核心 CLI")

    def test_23_missing_tty_stop_is_rejected(self) -> None:
        text = self.read("references/weixin-setup-zh.md").replace(
            "仍无真实 TTY 就不扫码",
            "仍无真实 TTY 就继续尝试普通文本框",
        )
        self.assert_document_rejected("references/weixin-setup-zh.md", text, "无 TTY")

    def test_24_tool_change_without_new_session_is_rejected(self) -> None:
        text = self.read("references/security-boundary.md").replace(
            "工具变化只对新会话可靠生效",
            "工具变化对当前会话立即可靠生效",
        )
        self.assert_document_rejected("references/security-boundary.md", text, "新会话")

    def test_25_old_session_persona_branch_exists(self) -> None:
        skill = self.read("SKILL.md")
        self.assertIn("已有旧微信会话", skill)
        self.assertIn("不能静默删除历史", skill)

    def test_26_duplicate_install_copy_mutation_is_rejected(self) -> None:
        text = self.read("references/install-skill.md").replace(
            "只保留一个权威实体目录",
            "保留三个相同实体目录",
        )
        self.assert_document_rejected("references/install-skill.md", text, "单一实体源")

    def test_27_skills_toolset_enabled_by_default_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_disable_if_present"].remove("skills")
        self.assert_contract_rejected(mutant, "高风险工具")

    def test_28_skills_write_without_approval_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["skills_write_approval_required_if_enabled"] = False
        self.assert_contract_rejected(mutant, "写入审批")

    def test_29_missing_pre_qr_static_gate_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["pre_qr_static_checks"] = []
        self.assert_contract_rejected(mutant, "静态门禁")

    def test_30_missing_post_qr_runtime_tests_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["post_qr_runtime_negative_tests"] = []
        self.assert_contract_rejected(mutant, "运行时负向测试")

    def test_31_intel_mac_support_mutation_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["platform_gate"]["unsupported"].remove("macos_intel")
        self.assert_contract_rejected(mutant, "Intel Mac")

    def test_32_early_local_persistence_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["local_service_lifecycle"]["persistence_only_after_acceptance"] = False
        self.assert_contract_rejected(mutant, "验收后")

    def test_33_cloud_command_without_profile_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["commands"]["status"] = mutant["cloud_service"]["commands"]["status"].replace(" -p <Profile>", "")
        self.assert_contract_rejected(mutant, "显式 Profile")

    def test_34_cloud_persist_without_force_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["commands"]["persist_stage"] = mutant["cloud_service"]["commands"]["persist_stage"].replace(" --force", "")
        self.assert_contract_rejected(mutant, "云端持久化必须重写")

    def test_35_systemd_command_only_preflight_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["preflight_required"].remove("systemd_is_running")
        self.assert_contract_rejected(mutant, "前置检查")

    def test_36_missing_quality_soak_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["quality_acceptance"]["release_grade_required"].remove("ten_turn_chinese_soak")
        self.assert_contract_rejected(mutant, "发布级验收")

    def test_37_second_account_as_hard_requirement_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["weixin_access"]["second_account_negative_test_required_for_base_completion"] = True
        self.assert_contract_rejected(mutant, "第二测试账号")

    def test_38_optional_toolset_always_enabled_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_always_enabled_toolsets"].append("web")
        self.assert_contract_rejected(mutant, "常开工具")

    def test_39_access_control_checks_moved_to_pre_qr_are_rejected(self) -> None:
        # 许可名单与群聊策略在扫码向导中产生，扫码前不可验证（V7.3.2 契约修复）
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["pre_qr_static_checks"].append("dm_allowlist_is_nonempty_and_owner_only")
        self.assert_contract_rejected(mutant, "扫码前不可验证")

    def test_40_post_qr_pre_start_gate_requires_access_evidence(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["post_qr_pre_start_checks"].remove("dm_allowlist_is_nonempty_and_owner_only")
        self.assert_contract_rejected(mutant, "扫码后启动前")

    def test_41_default_profile_for_acceptance_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["profile_must_not_be_default"] = False
        self.assert_contract_rejected(mutant, "default Profile")

    def test_42_visible_weixin_reasoning_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["weixin_reasoning_visible"] = True
        self.assert_contract_rejected(mutant, "不得展示模型推理")

    def test_43_unselected_memory_injection_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_memory_injection_enabled"] = True
        self.assert_contract_rejected(mutant, "不得注入")

    def test_44_passive_screen_reporting_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["setup_supervision"]["proactive_nonsecret_state_polling_required"] = False
        self.assert_contract_rejected(mutant, "主动状态检测")

    def test_45_missing_full_progress_rail_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["progress_display"]["full_five_step_rail_on_step_transition"] = False
        self.assert_contract_rejected(mutant, "五步进度全览")

    def test_46_internal_rule_self_intro_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["quality_acceptance"]["release_grade_required"].remove("natural_self_intro_without_internal_rules")
        self.assert_contract_rejected(mutant, "发布级验收")

    def test_47_unscoped_stateful_command_is_rejected(self) -> None:
        text = self.read("SKILL.md").replace(
            "hermes -p <Profile> gateway run",
            "hermes gateway run",
            1,
        )
        self.assert_document_rejected("SKILL.md", text, "未绑定 Profile")

    def test_48_stale_setup_markers_cannot_count_as_fresh_progress(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["setup_supervision"]["polling_requires_fresh_state_transition"] = False
        self.assert_contract_rejected(mutant, "新鲜状态")

    def test_49_unscoped_optional_tool_command_is_rejected(self) -> None:
        text = self.read("references/tools.md").replace(
            "hermes -p <Profile> cron status",
            "hermes cron status",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "未绑定 Profile")

    def test_50_every_profile_dependent_command_must_bind_profile(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["every_profile_dependent_command_must_bind_profile"] = False
        self.assert_contract_rejected(mutant, "Profile 依赖命令")

    def test_51_named_profile_cannot_claim_credential_isolation(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["named_profile_alone_is_credential_isolation"] = True
        self.assert_contract_rejected(mutant, "凭据隔离")

    def test_52_new_user_clone_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["profile_lifecycle"]["clone_for_new_user"] = True
        self.assert_contract_rejected(mutant, "不得克隆")

    def test_53_env_only_access_gate_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["weixin_access"]["effective_policy_not_env_text_only"] = False
        self.assert_contract_rejected(mutant, ".env 文本")

    def test_54_public_cloud_port_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["weixin_requires_new_public_inbound_port"] = True
        self.assert_contract_rejected(mutant, "公网入站端口")

    def test_55_simulation_cannot_claim_real_completion(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["completion_states"]["simulation_cannot_satisfy_real_state"] = False
        self.assert_contract_rejected(mutant, "模拟结果")

    def test_56_boot_recovery_requires_real_round_trip(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["completion_states"]["boot_recovery_verified"].remove("post_reboot_real_weixin_round_trip")
        self.assert_contract_rejected(mutant, "开机恢复")

    def test_57_cloud_preflight_before_release_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        sequence = mutant["cloud_service"]["deployment_sequence"]
        sequence.remove("service_account_preflight_from_release")
        sequence.insert(2, "service_account_preflight_from_release")
        self.assert_contract_rejected(mutant, "顺序不可执行")

    def test_58_unconditional_sudo_admin_path_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["admin_privilege_path"] = "sudo_required"
        self.assert_contract_rejected(mutant, "无条件要求 sudo")

    def test_59_systemd_guard_before_base_unit_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        sequence = mutant["cloud_service"]["deployment_sequence"]
        sequence.remove("systemd_env_guard_installed")
        sequence.insert(sequence.index("service_staged_stopped_disabled"), "systemd_env_guard_installed")
        self.assert_contract_rejected(mutant, "顺序不可执行")

    def test_60_missing_systemd_runtime_environment_check_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["runtime_proc_environ_boolean_check_required"] = False
        self.assert_contract_rejected(mutant, "实际进程环境")

    def test_61_cloud_persistence_start_now_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["commands"]["persist_stage"] = mutant["cloud_service"]["commands"]["persist_stage"].replace("--no-start-now", "--start-now")
        self.assert_contract_rejected(mutant, "保持 stopped")

    def test_62_cloud_persistence_prestart_after_start_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        sequence = mutant["cloud_service"]["systemd_environment_guard"]["persistence_sequence"]
        sequence[2], sequence[3] = sequence[3], sequence[2]
        self.assert_contract_rejected(mutant, "启动前复验")

    def test_63_wrong_profile_show_command_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["profile_lifecycle"]["binding_commands"]["profile_show"] = "hermes -p <Profile> profile show"
        self.assert_contract_rejected(mutant, "Profile 绑定命令")

    def test_64_mcp_in_chat_only_profile_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_mcp_servers_allowed"] = True
        self.assert_contract_rejected(mutant, "MCP")

    def test_65_nonexact_chat_only_toolset_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["chat_only_effective_enabled_toolsets_exact"].append("web")
        self.assert_contract_rejected(mutant, "精确收缩")

    def test_66_hidden_unknown_toolset_allowance_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["hidden_or_unknown_platform_toolsets_allowed"] = True
        self.assert_contract_rejected(mutant, "隐藏或未知")

    def test_67_missing_pre_qr_checker_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["pre_qr_checker"] = ""
        self.assert_contract_rejected(mutant, "扫码前")

    def test_68_wrong_profile_show_document_command_is_rejected(self) -> None:
        text = self.read("SKILL.md").replace(
            "hermes profile show <Profile>",
            "hermes -p <Profile> profile show",
            1,
        )
        self.assert_document_rejected("SKILL.md", text, "profile show")

    def test_69_nonisolated_profile_create_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["profile_lifecycle"]["fresh_create_command"] = "hermes profile create <Profile>"
        self.assert_contract_rejected(mutant, "关闭别名与 Skill 继承")

    def test_70_existing_profile_reuse_for_isolation_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["profile_lifecycle"]["existing_name_reuse_allowed_for_new_or_isolation"] = True
        self.assert_contract_rejected(mutant, "不得复用")

    def test_71_missing_shared_server_resource_ledger_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["test_resource_ledger_required_before_write_on_shared_server"] = False
        self.assert_contract_rejected(mutant, "测试资源台账")

    def test_72_inexact_test_cleanup_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["cleanup_only_exact_test_created_resources"] = False
        self.assert_contract_rejected(mutant, "精确新建资源")

    def test_73_unit_profile_binding_check_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["unit_execstart_and_profile_home_must_match"] = False
        self.assert_contract_rejected(mutant, "启动器、Profile 与 HERMES_HOME")

    def test_74_runtime_cmdline_profile_check_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["runtime_proc_cmdline_profile_check_required"] = False
        self.assert_contract_rejected(mutant, "实际进程 Profile")

    def test_75_stage_service_state_gate_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["stage_must_be_inactive_disabled"] = False
        self.assert_contract_rejected(mutant, "inactive + disabled")

    def test_76_prerestart_active_gate_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["prerestart_must_be_active"] = False
        self.assert_contract_rejected(mutant, "重启前")

    def test_77_runtime_enabled_state_gate_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["systemd_environment_guard"]["runtime_expected_enabled_required"] = False
        self.assert_contract_rejected(mutant, "enabled/disabled")

    def test_78_process_weixin_prefix_override_allowance_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["process_weixin_prefix_overrides_allowed"] = True
        self.assert_contract_rejected(mutant, "进程 WEIXIN_*")

    def test_79_unknown_profile_weixin_key_allowance_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["unknown_profile_weixin_keys_allowed"] = True
        self.assert_contract_rejected(mutant, "未知 Profile WEIXIN_*")

    def test_80_operation_mode_must_precede_local_hermes_reads(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["operation_mode_router"]["resolved_before_any_local_hermes_read"] = False
        self.assert_contract_rejected(mutant, "操作模式")

    def test_81_uncertain_target_must_fail_into_protected_mode(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["operation_mode_router"]["uncertainty_defaults_to"] = "authorized_incremental"
        self.assert_contract_rejected(mutant, "不确定")

    def test_82_step_router_must_cover_every_base_step(self) -> None:
        mutant = copy.deepcopy(self.contract)
        del mutant["step_router"]["step_4_weixin"]
        self.assert_contract_rejected(mutant, "步骤路由")

    def test_83_configuration_detection_cannot_count_as_model_connectivity(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["credential_provenance"]["configuration_detection_is_not_connectivity_evidence"] = False
        self.assert_contract_rejected(mutant, "配置检测")

    def test_84_provenance_must_precede_remote_model_probe(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["credential_provenance"]["classified_before_remote_or_billable_probe"] = False
        self.assert_contract_rejected(mutant, "远程模型")

    def test_85_direct_auth_list_capture_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["credential_provenance"]["direct_auth_list_output_allowed_in_agent_logs"] = True
        self.assert_contract_rejected(mutant, "auth list")

    def test_86_qr_setup_must_avoid_default_yes_service_prompts(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["setup_supervision"]["exit_before_default_yes_service_prompts"] = False
        self.assert_contract_rejected(mutant, "默认是")

    def test_87_isolation_root_purpose_binding_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["isolated_root_purpose_and_scope_bound"] = False
        self.assert_contract_rejected(mutant, "用途")

    def test_88_cloud_service_runner_requires_cloud_root_purpose(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["service_runner_requires_cloud_service_root_purpose"] = False
        self.assert_contract_rejected(mutant, "cloud-service")

    def test_88a_live_local_acceptance_must_not_lose_credentials_with_a_temporary_root(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["protected_live_local_acceptance_uses_persistent_isolated_root"] = False
        self.assert_contract_rejected(mutant, "临时根")

    def test_88b_persistent_local_root_must_reuse_valid_credentials_without_managed_service(self) -> None:
        for key, message in (
            ("persistent_local_root_requires_fresh_gate_before_first_auth", "新鲜门禁"),
            ("persistent_local_root_reused_without_reasking_valid_credentials", "重复索取"),
            ("persistent_local_root_forbids_managed_service_install", "受管服务"),
        ):
            with self.subTest(key=key):
                mutant = copy.deepcopy(self.contract)
                mutant["acceptance_isolation"][key] = False
                self.assert_contract_rejected(mutant, message)

    def test_89_stale_weixin_reference_version_is_rejected(self) -> None:
        text = self.read("references/weixin-setup-zh.md").replace(
            "Hermes Agent v0.20.0",
            "Hermes Agent v0.29.1",
            1,
        )
        self.assert_document_rejected("references/weixin-setup-zh.md", text, "v0.20.0")

    def test_90_cloud_ledger_bootstrap_cannot_create_untracked_file(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["test_resource_ledger_guard"]["bootstrap_guard_runs_without_untracked_remote_file"] = False
        self.assert_contract_rejected(mutant, "引导脚本")

    def test_91_missing_pre_model_chat_baseline_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["chat_safety_baseline_required_before_model_auth"] = False
        self.assert_contract_rejected(mutant, "模型认证前")

    def test_92_dashboard_secret_entry_in_protected_mode_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["credential_provenance"]["protected_mode_dashboard_secret_entry_allowed"] = True
        self.assert_contract_rejected(mutant, "Dashboard")

    def test_93_cloud_model_auth_before_safety_gate_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        sequence = mutant["cloud_service"]["deployment_sequence"]
        sequence.remove("model_authenticated")
        sequence.insert(sequence.index("pre_model_safety_gate_passed"), "model_authenticated")
        self.assert_contract_rejected(mutant, "模型调用前")

    def test_94_missing_release_package_verifier_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["cloud_service"]["release_package_verifier"] = ""
        self.assert_contract_rejected(mutant, "发布包")

    def test_95_user_terminal_by_default_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["user_terminal_typing_required_by_default"] = True
        self.assert_contract_rejected(mutant, "默认不得输入终端")

    def test_96_generic_model_wizard_before_provider_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["generic_model_wizard_allowed_before_provider_is_fixed"] = True
        self.assert_contract_rejected(mutant, "通用模型向导")

    def test_97_multi_command_terminal_fallback_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["manual_terminal_max_commands"] = 2
        self.assert_contract_rejected(mutant, "最多只能有一条命令")

    def test_98_missing_trusted_handoff_launcher_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["trusted_handoff_launcher"] = ""
        self.assert_contract_rejected(mutant, "受信终端交接器")

    def test_99_skill_must_promise_conversation_first(self) -> None:
        text = self.read("SKILL.md").replace("用户默认不接触终端", "用户默认自己操作终端", 1)
        self.assert_document_rejected("SKILL.md", text, "对话优先承诺")

    def test_100_model_and_weixin_must_use_agent_launched_handoff(self) -> None:
        text = self.read("references/model-routing.md").replace(
            "用户不输入终端命令",
            "用户自己打开终端并输入启动命令",
            1,
        )
        self.assert_document_rejected("references/model-routing.md", text, "启动责任")

    def test_101_external_install_must_not_require_user_terminal(self) -> None:
        text = self.read("references/install-skill.md").replace(
            "不要求普通用户打开终端",
            "要求普通用户打开终端",
            1,
        )
        self.assert_document_rejected("references/install-skill.md", text, "Skill 安装")

    def test_102_cloud_deployment_must_not_require_user_ssh_commands(self) -> None:
        text = self.read("references/cloud-deployment.md").replace(
            "用户不输入 SSH 或服务器命令",
            "用户自行输入 SSH 和服务器命令",
            1,
        )
        self.assert_document_rejected("references/cloud-deployment.md", text, "云端部署")

    def test_103_cloud_handoff_must_bind_remote_service_user(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_remote_service_user_required"] = False
        self.assert_contract_rejected(mutant, "远端服务账号")

    def test_104_cloud_direct_handoff_cannot_guess_ssh_user(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_direct_requires_matching_explicit_ssh_user"] = False
        self.assert_contract_rejected(mutant, "SSH 用户")

    def test_105_weixin_must_not_require_platform_menu_selection(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["weixin_platform_menu_user_action_required"] = True
        self.assert_contract_rejected(mutant, "选择微信平台")

    def test_106_weixin_must_not_require_permission_menu_selection(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["weixin_permission_menu_user_action_required"] = True
        self.assert_contract_rejected(mutant, "逐项选择微信权限")

    def test_107_weixin_must_use_direct_helper(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["weixin_direct_setup_helper"] = ""
        self.assert_contract_rejected(mutant, "直达 Weixin")

    def test_108_windows_cannot_require_third_party_terminal(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["windows_third_party_terminal_required"] = True
        self.assert_contract_rejected(mutant, "第三方终端")

    def test_109_weixin_cannot_use_generic_gateway_setup(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["setup_supervision"]["generic_gateway_setup_used_for_weixin"] = True
        self.assert_contract_rejected(mutant, "全平台 gateway setup")

    def test_110_cloud_api_key_cannot_be_collected_before_masked_prompt(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_secret_collected_only_after_masked_prompt"] = False
        self.assert_contract_rejected(mutant, "掩码提示前")

    def test_111_cloud_api_key_cannot_be_piped_early_to_ssh(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_early_ssh_pipe_forbidden"] = False
        self.assert_contract_rejected(mutant, "提前管道到 SSH")

    def test_112_cloud_api_key_label_cannot_require_user_action(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_label_user_action_required"] = True
        self.assert_contract_rejected(mutant, "凭据标签")

    def test_113_cloud_api_key_echo_detection_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_echo_detection_required"] = False
        self.assert_contract_rejected(mutant, "回显检测")

    def test_114_cloud_api_key_save_receipt_is_required(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_save_receipt_required"] = False
        self.assert_contract_rejected(mutant, "保存回执")

    def test_115_cloud_api_key_raw_child_output_cannot_be_returned(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["cloud_api_key_raw_child_output_returned"] = True
        self.assert_contract_rejected(mutant, "原始子进程输出")

    def test_116_macos_secret_dialog_must_use_native_backend(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["macos_secret_dialog_backend"] = "tkinter_simpledialog"
        self.assert_contract_rejected(mutant, "macOS 必须使用原生隐藏输入框")

    def test_117_macos_secret_field_must_be_visible_and_editable(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["macos_secret_field_visible_and_editable_required"] = False
        self.assert_contract_rejected(mutant, "可见且可编辑")

    def test_118_macos_cannot_fall_back_to_tkinter_dialog(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["conversation_first_handoff"]["macos_tkinter_secret_dialog_allowed"] = True
        self.assert_contract_rejected(mutant, "Tkinter")

    def test_119_model_document_locks_macos_native_editable_dialog(self) -> None:
        text = self.read("references/model-routing.md").replace(
            "macOS 固定使用系统原生密码框",
            "macOS 使用普通弹窗",
            1,
        )
        self.assert_document_rejected("references/model-routing.md", text, "可编辑密码框")

    def test_120_optional_features_cannot_be_prompted_before_base(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["base_flow"]["optional_capabilities_prompted_before_base_completion"] = True
        self.assert_contract_rejected(mutant, "基础闭环前")

    def test_121_internal_safety_baseline_cannot_become_a_sixth_step(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["base_flow"]["internal_chat_safety_baseline_is_user_visible_optional_step"] = True
        self.assert_contract_rejected(mutant, "内部安全基线")

    def test_122_optional_failure_cannot_break_base_assistant(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_lifecycle"]["optional_failure_must_not_break_base_assistant"] = False
        self.assert_contract_rejected(mutant, "不得破坏基础助手")

    def test_123_skill_must_keep_optional_questions_after_base(self) -> None:
        text = self.read("SKILL.md").replace(
            "基础闭环完成前不主动询问用户是否需要这些能力",
            "第一步主动询问用户是否需要这些能力",
            1,
        )
        self.assert_document_rejected("SKILL.md", text, "基础闭环前")

    def test_124_obsidian_path_cannot_be_described_as_a_sandbox(self) -> None:
        text = self.read("references/tools.md").replace(
            "`OBSIDIAN_VAULT_PATH` 只是寻址约定，不是权限边界",
            "`OBSIDIAN_VAULT_PATH` 会把权限限制在 Vault 内",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "权限沙箱")

    def test_125_model_probe_permissions_must_be_rehardened_before_weixin(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["acceptance_isolation"]["profile_permissions_rehardened_after_model_probe_before_weixin"] = False
        self.assert_contract_rejected(mutant, "权限重新收紧")

    def test_126_post_probe_baseline_reapply_cannot_be_disabled(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["execution_boundary"]["post_model_probe_baseline_reapply_required"] = False
        self.assert_contract_rejected(mutant, "重新应用安全基线")

    def test_127_feishu_read_only_cannot_be_treated_as_single_document_isolation(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["feishu_read_only_scope_is_not_single_document_isolation"] = False
        self.assert_contract_rejected(mutant, "单文档隔离")

    def test_128_feishu_broader_scope_requires_local_resource_allowlist(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["feishu_broader_scope_requires_local_resource_allowlist"] = False
        self.assert_contract_rejected(mutant, "资源白名单")

    def test_129_private_document_transfer_requires_separate_consent(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["external_model_content_transfer_requires_separate_disclosure_and_consent"] = False
        self.assert_contract_rejected(mutant, "外部模型")

    def test_130_feishu_setup_cannot_be_delegated_back_to_novice(self) -> None:
        text = self.read("references/tools.md").replace(
            "不得把创建应用、寻找权限和复制配置步骤重新甩给小白",
            "让用户自行创建应用、寻找权限并复制配置",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "甩给小白")

    def test_131_boundary_test_must_reject_before_network(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["unauthorized_document_boundary_test_rejects_before_network"] = False
        self.assert_contract_rejected(mutant, "联网前拒绝")

    def test_132_local_knowledge_cannot_expose_generic_terminal(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["knowledge_base"]["generic_file_or_terminal_exposed"] = True
        self.assert_contract_rejected(mutant, "通用文件或终端")

    def test_133_knowledge_write_requires_per_action_confirmation(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["knowledge_base"]["write_requires_per_action_elicitation"] = False
        self.assert_contract_rejected(mutant, "逐次确认")

    def test_134_feishu_create_requires_fixed_parent(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["create_is_fixed_parent_allowlist"] = False
        self.assert_contract_rejected(mutant, "固定目录")

    def test_135_weixin_cannot_receive_generic_coding_terminal(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["generic_terminal_exposed_to_weixin"] = True
        self.assert_contract_rejected(mutant, "通用终端")

    def test_136_coding_prepare_and_apply_must_be_separate(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["prepare_and_apply_are_separate_confirmations"] = False
        self.assert_contract_rejected(mutant, "分两次确认")

    def test_136a_installed_but_broken_coding_tool_needs_its_own_branch(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["coding_agent"]["selected_tool_availability_branches"].remove("installed_but_cannot_start")
        self.assert_contract_rejected(mutant, "已安装但无法启动")

    def test_136b_broken_codex_cannot_count_as_reusable(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["installed_but_broken_cannot_count_as_existing"] = False
        self.assert_contract_rejected(mutant, "不得登记为可复用")

    def test_136c_coding_tool_repair_requires_consent(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["coding_agent"]["repair_or_upgrade_requires_user_consent"] = False
        self.assert_contract_rejected(mutant, "先取得用户同意")

    def test_137_codex_network_cannot_be_enabled_by_default(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["codex_network_access"] = True
        self.assert_contract_rejected(mutant, "不得联网")

    def test_138_real_automation_completion_requires_weixin_delivery(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["daily_automation"]["real_weixin_delivery_required_for_real_completion"] = False
        self.assert_contract_rejected(mutant, "微信送达证据")

    def test_139_optional_runtime_must_be_routed_after_base(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["step_router"]["optional_after_base"].remove("optional_capability_runtime")
        self.assert_contract_rejected(mutant, "受限执行器")

    def test_140_cron_success_cannot_rely_on_exit_code_alone(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["daily_automation"]["cli_success_requires_positive_receipt_not_exit_code"] = False
        self.assert_contract_rejected(mutant, "退出码")

    def test_140a_cron_failure_requires_durable_failed_receipt(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["daily_automation"]["failed_run_must_persist_failed_receipt"] = False
        self.assert_contract_rejected(mutant, "持久失败回执")

    def test_140b_failed_or_empty_cron_output_is_not_delivery(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["daily_automation"]["failed_or_empty_output_cannot_be_accepted_as_delivery"] = False
        self.assert_contract_rejected(mutant, "不得冒充日报送达")

    def test_140c_chinese_cron_delivery_must_disable_english_wrapper(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["daily_automation"]["chinese_delivery_requires_wrap_response_false_readback"] = False
        self.assert_contract_rejected(mutant, "关闭英文包装并读回")

    def test_141_optional_runtime_cannot_rediscover_global_hermes(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["base_runtime_binding"]["rediscover_global_path_forbidden"] = False
        self.assert_contract_rejected(mutant, "PATH")

    def test_142_optional_runtime_must_reuse_exact_profile(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["base_runtime_binding"]["reuse_exact_profile"] = False
        self.assert_contract_rejected(mutant, "Profile")

    def test_143_optional_runtime_requires_preflight_before_write(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["base_runtime_binding"]["capability_preflight_required_before_write"] = False
        self.assert_contract_rejected(mutant, "写入前")

    def test_144_preflight_failure_cannot_silently_switch_hermes(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["base_runtime_binding"]["preflight_failure_requires_upgrade_consent_or_pause"] = False
        self.assert_contract_rejected(mutant, "升级同意")

    def test_145_feishu_login_cannot_be_treated_as_dedicated_app_ready(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["feishu_login_does_not_equal_dedicated_app_ready"] = False
        self.assert_contract_rejected(mutant, "已登录误判")

    def test_146_feishu_cli_must_cover_missing_and_uncertain_states(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_router"]["knowledge_base"]["feishu_cli_availability_branches"] = ["compatible_existing"]
        self.assert_contract_rejected(mutant, "已有、缺失和不确定")

    def test_147_feishu_cli_install_requires_user_consent(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["missing_cli_install_requires_user_consent"] = False
        self.assert_contract_rejected(mutant, "安装飞书 CLI")

    def test_148_feishu_named_profile_cannot_overwrite_existing_config(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["dedicated_named_profile_does_not_overwrite_existing"] = False
        self.assert_contract_rejected(mutant, "不得覆盖")

    def test_149_feishu_cli_requires_docs_v2_probe(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["cli_version_and_docs_v2_help_verified"] = False
        self.assert_contract_rejected(mutant, "Docs v2")

    def test_150_feishu_calls_must_bind_the_dedicated_profile(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["every_cli_call_explicitly_binds_named_profile"] = False
        self.assert_contract_rejected(mutant, "显式绑定")

    def test_151_feishu_document_url_must_use_strict_official_host_and_path(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["document_url_requires_strict_official_host_and_path"] = False
        self.assert_contract_rejected(mutant, "官方域名和文档路径")

    def test_151a_feishu_approval_prompt_must_name_originating_interface(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["approval_prompt_names_originating_interface"] = False
        self.assert_contract_rejected(mutant, "原请求界面")

    def test_151b_feishu_approval_timeouts_must_leave_result_margin(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["mcp_tool_timeout_seconds"] = 120
        self.assert_contract_rejected(mutant, "返回余量")

    def test_151c_missing_weixin_approval_cannot_be_misreported_as_feishu_failure(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["timeout_without_inbound_approval_must_not_blame_feishu"] = False
        self.assert_contract_rejected(mutant, "不得误报飞书故障")

    def test_151d_feishu_approval_cannot_be_sent_before_a_live_request(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["preapproval_is_unsupported_and_retry_restarts_original_request"] = False
        self.assert_contract_rejected(mutant, "预先开启权限")

    def test_151e_feishu_must_bind_explicit_node_runtime(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["explicit_node_runtime_path_required"] = False
        self.assert_contract_rejected(mutant, "Node.js")

    def test_151f_feishu_isolation_cannot_depend_on_inherited_path(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["isolated_runtime_does_not_depend_on_inherited_path"] = False
        self.assert_contract_rejected(mutant, "继承的 PATH")

    def test_151fa_feishu_must_verify_node_runtime_identity(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["node_runtime_identity_verified"] = False
        self.assert_contract_rejected(mutant, "Node.js 运行时身份")

    def test_151g_feishu_docs_must_show_explicit_node_handoff(self) -> None:
        text = self.read("references/tools.md").replace(
            "不能依赖 Agent 或隔离 Gateway 继承到的 `PATH`",
            "可以直接依赖当前 PATH",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "显式 Node.js 路径")

    def test_152_knowledge_state_directory_must_be_private(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["knowledge_base"]["state_directory_owner_and_mode_verified"] = False
        self.assert_contract_rejected(mutant, "知识库私有状态目录")

    def test_153_coding_state_directory_must_be_private(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["state_directory_owner_and_mode_verified"] = False
        self.assert_contract_rejected(mutant, "编程私有状态目录")

    def test_154_agent_bound_feishu_cli_must_preserve_exact_hermes_home(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["agent_bound_cli_preserves_exact_hermes_home"] = False
        self.assert_contract_rejected(mutant, "精确 Hermes 根")

    def test_155_read_only_feishu_must_not_expose_write_tool(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["write_tool_absent_without_fixed_parent"] = False
        self.assert_contract_rejected(mutant, "不得暴露写入工具")

    def test_156_feishu_resource_identifiers_cannot_be_process_arguments(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["resource_identifiers_stored_in_private_scope_file_not_process_args"] = False
        self.assert_contract_rejected(mutant, "进程参数")

    def test_157_hermes_empty_accepted_elicitation_content_must_work(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["elicitation_accept_action_allows_empty_content"] = False
        self.assert_contract_rejected(mutant, "确认内容为空")

    def test_158_mcp_add_must_use_trusted_tty(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["mcp_add_requires_trusted_tty_for_enable_prompt"] = False
        self.assert_contract_rejected(mutant, "可信 TTY")

    def test_159_mcp_add_must_verify_exact_tool_inventory(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["mcp_add_exact_tool_inventory_verified_before_accept"] = False
        self.assert_contract_rejected(mutant, "精确工具清单")

    def test_160_codex_login_status_must_be_checked(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["login_status_checked_before_mcp_enable"] = False
        self.assert_contract_rejected(mutant, "登录状态")

    def test_161_codex_login_states_must_include_expired(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["login_states"] = ["logged_in", "not_logged_in"]
        self.assert_contract_rejected(mutant, "失效三种状态")

    def test_162_codex_login_secret_cannot_be_requested_in_chat(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["login_secret_never_requested_in_chat"] = False
        self.assert_contract_rejected(mutant, "不得在聊天中索取")

    def test_163_codex_mcp_timeout_must_exceed_exec_timeout(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["mcp_tool_timeout_seconds"] = 1800
        self.assert_contract_rejected(mutant, "多留 30 秒")

    def test_164_feishu_missing_scope_names_must_be_allowlisted(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["safe_missing_scope_names_are_allowlisted"] = False
        self.assert_contract_rejected(mutant, "白名单内的 scope")

    def test_165_feishu_raw_failure_output_cannot_be_echoed(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["feishu"]["raw_cli_failure_output_is_not_echoed"] = False
        self.assert_contract_rejected(mutant, "飞书原始失败输出")

    def test_166_codex_runtime_auth_failure_must_be_actionable(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["runtime_auth_and_quota_failures_are_actionable"] = False
        self.assert_contract_rejected(mutant, "登录与额度失效")

    def test_167_codex_raw_failure_output_cannot_be_echoed(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["raw_codex_failure_output_is_not_echoed"] = False
        self.assert_contract_rejected(mutant, "Codex 原始失败输出")

    def test_168_codex_login_must_be_rechecked_before_each_prepare(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["login_status_checked_before_each_prepare"] = False
        self.assert_contract_rejected(mutant, "每次准备修改前")

    def test_169_codex_desktop_login_cannot_stand_in_for_cli_login(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["desktop_app_login_not_assumed_for_cli"] = False
        self.assert_contract_rejected(mutant, "桌面登录")

    def test_170_codex_global_custom_provider_cannot_be_overwritten(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["global_custom_provider_not_overwritten"] = False
        self.assert_contract_rejected(mutant, "全局自定义模型配置")

    def test_171_codex_mcp_must_bind_private_dedicated_home(self) -> None:
        mutant = copy.deepcopy(self.contract)
        mutant["optional_capability_runtime"]["coding_agent"]["mcp_explicitly_binds_codex_home"] = False
        self.assert_contract_rejected(mutant, "显式绑定专用登录目录")

    def test_172_codex_docs_must_explain_dedicated_home_branch(self) -> None:
        text = self.read("references/tools.md").replace(
            "不得覆盖或改写这份全局配置",
            "请直接覆盖用户的全局配置",
            1,
        )
        self.assert_document_rejected("references/tools.md", text, "全局自定义配置")

    def test_173_codex_mcp_must_check_cli_contract_and_home_write_failure(self) -> None:
        mutant = copy.deepcopy(self.contract)
        coding = mutant["optional_capability_runtime"]["coding_agent"]
        coding["mcp_startup_mechanically_checks_codex_cli_contract"] = False
        self.assert_contract_rejected(mutant, "机械核对 CLI 安全参数")
        mutant = copy.deepcopy(self.contract)
        coding = mutant["optional_capability_runtime"]["coding_agent"]
        coding["readonly_dedicated_home_failure_is_actionable"] = False
        self.assert_contract_rejected(mutant, "专用登录目录不可写")

    def test_174_node_codex_launcher_must_bind_verified_node(self) -> None:
        mutant = copy.deepcopy(self.contract)
        coding = mutant["optional_capability_runtime"]["coding_agent"]
        coding["node_shebang_launcher_requires_explicit_verified_node"] = False
        self.assert_contract_rejected(mutant, "显式核验 Node.js")


if __name__ == "__main__":
    unittest.main(verbosity=2)

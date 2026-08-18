#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


CONTRACT_PATH = "references/flow-contract.json"

# 版本一致性门禁：以下版本必须与 SKILL.md、flow-contract.json 和
# agents/openai.yaml 同步；验证器负责阻止漏改。
VERSION = "0.4"
SKILL_TITLE = f"# 2026-08-15 微信 AI 助手搭建 V{VERSION}"


def load_contract(root: Path) -> dict:
    return json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))


def validate_contract(contract: dict) -> list[str]:
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(contract.get("schema_version") == 7, "流程契约 schema_version 必须为 7")
    require(contract.get("skill_version") == VERSION, f"流程契约版本必须为 {VERSION}")

    operation = contract.get("operation_mode_router", {})
    require(operation.get("resolved_before_any_local_hermes_read") is True, "读取本机 Hermes 前必须先确定操作模式")
    require(operation.get("uncertainty_defaults_to") == "protected_acceptance", "目标或根不确定时必须进入受保护模式")
    modes = operation.get("modes", {})
    require(set(modes) == {"new_build", "authorized_incremental", "protected_acceptance"}, "操作模式定义不完整")
    protected = modes.get("protected_acceptance", {})
    require({
        "user_says_do_not_touch_existing_assistant",
        "test_or_audit_on_machine_with_existing_assistant",
        "target_profile_or_hermes_root_uncertain",
    } <= set(protected.get("triggers_any", [])), "受保护模式触发条件不完整")
    require(protected.get("production_root_read_scope") == [], "受保护模式不得读取生产 Hermes 根")

    step_router = contract.get("step_router", {})
    required_steps = {
        "step_0_mode",
        "step_1_runtime",
        "step_2_hermes",
        "step_3_model",
        "step_4_weixin",
        "step_5_persona_acceptance",
        "optional_after_base",
    }
    require(set(step_router) == required_steps, "步骤路由必须覆盖模式、五步基础流程和可选项")
    for step, keys in step_router.items():
        require(bool(keys) and all(key in contract for key in keys), f"步骤路由 {step} 引用了不存在的契约键")
    require("execution_boundary" in step_router.get("step_2_hermes", []), "Hermes Profile 建立后必须立即读取执行边界")
    require("execution_boundary" in step_router.get("step_3_model", []), "模型认证与探测必须读取执行边界")
    require("base_flow" in step_router.get("step_0_mode", []), "开始流程时必须读取五步基础顺序")
    require("optional_capability_lifecycle" in step_router.get("optional_after_base", []), "可选项路由缺少基础闭环后门禁")
    require("optional_capability_router" in step_router.get("optional_after_base", []), "可选项路由缺少工具有无与渠道选择分支")
    require("optional_capability_runtime" in step_router.get("optional_after_base", []), "可选项路由缺少受限执行器契约")
    require(all("novice_guidance" in keys for keys in step_router.values()), "每个用户可见步骤都必须读取小白引导契约")

    flow = contract.get("base_flow", {})
    require(flow.get("user_visible_steps") == [
        "select_local_or_cloud_runtime",
        "check_reuse_or_install_hermes_on_selected_runtime",
        "check_reuse_or_configure_model_in_same_profile",
        "connect_weixin_to_same_profile",
        "write_soul_and_complete_real_chat_acceptance",
    ], "用户可见基础流程必须保持五步且先搭基础助手")
    require(flow.get("optional_capabilities_prompted_before_base_completion") is False, "基础闭环前不得主动询问可选能力")
    require(flow.get("internal_chat_safety_baseline_is_user_visible_optional_step") is False, "内部安全基线不得伪装成用户可选步骤")
    require(flow.get("same_profile_required_from_step_2_through_step_5") is True, "Hermes、模型、微信与人格必须绑定同一 Profile")

    novice = contract.get("novice_guidance", {})
    require(novice.get("applies_to_every_user_visible_step") is True, "小白引导必须覆盖每个用户可见步骤")
    require(novice.get("assume_user_knows_technical_terms") is False, "不得假设用户理解技术术语")
    require(novice.get("plain_language_before_product_or_permission") is True, "产品名或授权问题前必须先用普通话解释")
    require(set(novice.get("each_new_step_explains", [])) == {
        "user_outcome", "why_now", "agent_action", "single_user_action", "visible_success_signal"
    }, "每一步必须解释结果、原因、Agent 动作、用户单一动作和成功标志")
    require(novice.get("choice_consequences_before_question") is True, "选择题前必须先解释选择后果")
    require(novice.get("recommendation_before_single_decision") is True, "每次只问一个决定且先给推荐")
    require(novice.get("progress_line_alone_is_sufficient") is False, "进度条不能代替小白说明")
    require(novice.get("dependency_installation_before_capability_and_channel_selection") is False, "用户选能力和渠道前不得安装 Docker 等依赖")
    require(novice.get("unknown_user_state_must_not_be_guessed") is True, "用户是否已有工具或资料不明时不得猜测")
    require(novice.get("new_session_prompt_explains_once_always_cancel") is True, "新会话确认必须解释一次、永久和取消三种选择")
    require(novice.get("new_session_once_is_recommended") is True, "普通新会话验收必须推荐一次性确认")
    require(novice.get("gateway_restart_invalidates_pending_new_session_confirmation") is True, "网关重启后不得继续使用旧的新会话确认")

    base = contract.get("base_completion", {})
    require("execution_boundary_verified" in base.get("required", []), "基础完成门槛缺少执行边界")
    require("web_search" in base.get("optional", []), "搜索必须是可选能力")
    require("persistent_memory" in base.get("optional", []), "持久记忆必须按能力单独验收")

    optional = contract.get("optional_capability_lifecycle", {})
    require(optional.get("prompt_only_after_base_completion") is True, "可选能力只能在基础闭环后询问")
    require(set(optional.get("not_part_of_runtime_selection_question", [])) == {
        "knowledge_base", "obsidian", "coding_agent", "codex", "web_search", "persistent_memory", "daily_automation", "scheduled_push"
    }, "第一步不得混入知识库、编程或其他可选能力选择")
    require(optional.get("post_base_upgrade_menu_required") is True, "基础完成后必须展示能力升级入口")
    require(optional.get("primary_upgrade_menu") == ["knowledge_base", "coding_agent", "daily_automation"], "正式能力升级必须包含知识库、编程智能体和日常自动化")
    require(set(optional.get("each_menu_item_explains", [])) == {"user_outcome", "runtime_compatibility", "minimum_user_action", "permission_or_cost_boundary"}, "能力升级入口缺少结果、环境、动作或权限费用说明")
    require(optional.get("daily_automation_is_formal_optional") is True, "日常自动化不得继续藏在非正式可选项")
    require(optional.get("existing_assistant_target_uses_user_facing_name_first") is True, "已有助手加能力时必须先用用户看得懂的名字锚定目标")
    require(optional.get("target_identity_and_read_authorization_are_separate_questions") is True, "目标身份与只读授权必须分成两问")
    require(optional.get("explicit_early_requirement_may_inform_runtime_explanation") is True, "用户主动声明的硬需求必须能影响运行位置说明")
    require(optional.get("baseline_high_risk_tools_remain_disabled_until_selected") is True, "未选择的高风险工具必须保持关闭")
    require(optional.get("one_capability_at_a_time") is True, "可选能力必须一次只配置一项")
    require(set(optional.get("each_capability_requires", [])) == {
        "scoped_read_only_detection", "current_official_facts_verified", "user_selects_after_comparison",
        "least_privilege_configuration", "real_success_test", "error_test", "boundary_negative_test",
        "rollback_without_breaking_base",
    }, "可选能力缺少检测、最小授权、真实验收或回退闭环")
    require(optional.get("incompatible_runtime_requires_separate_migration_or_sync_review") is True, "运行位置不兼容时不得偷偷搭桥")
    require(optional.get("optional_failure_must_not_break_base_assistant") is True, "可选能力失败不得破坏基础助手")

    router = contract.get("optional_capability_router", {})
    require(router.get("channel_selection_precedes_external_detection") is True, "必须先选能力渠道再读取外部状态")
    require(router.get("dependency_installation_before_channel_selection") is False, "渠道选择前不得安装 Docker 或编程工具")
    require(router.get("shared_availability_branches") == ["already_has", "does_not_have", "uncertain"], "可选能力必须覆盖已有、没有和不确定三种状态")

    knowledge = router.get("knowledge_base", {})
    require(knowledge.get("initial_menu") == [
        "existing_obsidian", "existing_feishu", "existing_local_folder",
        "no_existing_knowledge_base", "uncertain_or_other",
    ], "知识库入口必须覆盖 Obsidian、飞书、本地文件、没有和不确定")
    require(knowledge.get("existing_path_reuses_before_installing") is True, "已有知识库必须优先复用")
    require(knowledge.get("missing_path_offers_new_local_markdown_or_feishu") is True, "没有知识库时缺少新建本地资料库或飞书路线")
    require(knowledge.get("uncertain_path_asks_where_materials_are_edited_without_scanning") is True, "不确定知识库时不得扫描电脑猜测")
    require(knowledge.get("docker_explained_only_after_local_files_selected") is True, "Docker 只能在用户选定本地文件后解释")
    require(knowledge.get("docker_is_not_a_knowledge_base") is True, "不得把 Docker 说成知识库")
    require(knowledge.get("feishu_read_only_scope_is_not_single_document_isolation") is True, "不得把飞书只读 scope 当作单文档隔离")
    require(knowledge.get("feishu_broader_scope_requires_local_resource_allowlist") is True, "飞书广范围读取缺少本地资源白名单")
    require(knowledge.get("feishu_app_setup_is_agent_operated_after_user_login") is True, "飞书应用技术配置不得甩给小白")
    require(knowledge.get("feishu_login_does_not_equal_dedicated_app_ready") is True, "不得把飞书已登录误判成专用应用已就绪")
    require(knowledge.get("feishu_cli_checked_only_after_feishu_selected") is True, "只能在用户选定飞书后检查 lark-cli")
    require(knowledge.get("feishu_cli_availability_branches") == ["compatible_existing", "missing", "uncertain"], "飞书 CLI 必须覆盖已有、缺失和不确定三种状态")
    require(knowledge.get("external_model_content_transfer_requires_separate_disclosure_and_consent") is True, "私有知识库发送给外部模型前缺少单独说明与同意")
    require(knowledge.get("unauthorized_document_boundary_test_rejects_before_network") is True, "飞书未授权文档负向测试必须在联网前拒绝")

    coding = router.get("coding_agent", {})
    require(coding.get("initial_menu") == [
        "existing_codex", "existing_claude_code", "existing_opencode",
        "has_code_but_tool_unknown", "no_tool_or_project", "uncertain_or_other",
    ], "编程入口必须覆盖已有工具、有代码但不懂工具、没有和不确定")
    require(coding.get("selected_tool_checked_before_other_tools") is True, "用户选定的编程工具必须先检查和复用")
    require(coding.get("selected_tool_availability_branches") == [
        "compatible_existing", "installed_but_cannot_start", "missing", "uncertain",
    ], "编程工具可用性必须覆盖已安装但无法启动")
    require(coding.get("missing_tool_requires_official_comparison_before_install") is True, "没有编程工具时必须先比较再安装")
    require(coding.get("repair_or_upgrade_requires_user_consent") is True, "修复或升级编程工具必须先取得用户同意")
    require(coding.get("missing_project_offers_isolated_demo_or_pause") is True, "没有代码项目时必须允许演示项目或暂停")
    require(coding.get("project_scope_selected_before_file_access") is True, "访问代码前必须先选定项目范围")
    require(coding.get("cross_device_requires_separate_bridge_review") is True, "跨设备编程必须单独审查桥接")

    automation = router.get("daily_automation", {})
    require(automation.get("initial_menu") == [
        "ai_digest", "weather_digest", "reminder", "fixed_task", "uncertain_or_other",
    ], "日常自动化入口必须先选择内容类型")
    require(automation.get("existing_source_or_task_reused_before_new_purchase") is True, "已有数据源或任务必须优先复用")
    require(automation.get("missing_source_requires_official_comparison") is True, "没有数据源时必须先比较官方方案")
    require(automation.get("content_selected_before_city_time_or_frequency") is True, "未选内容前不得先问城市、时间或频率")
    require(automation.get("manual_delivery_test_before_schedule") is True, "定时启用前必须先手动真实投递")

    runtime = contract.get("optional_capability_runtime", {})
    require(runtime.get("elicitation_prompts_echo_unvalidated_tool_input") is False, "确认提示不得回显未经校验的工具参数")
    require(runtime.get("elicitation_accept_action_allows_empty_content") is True, "Hermes 已批准但确认内容为空时不得误判失败")
    require(runtime.get("mcp_add_requires_trusted_tty_for_enable_prompt") is True, "MCP 接入必须在可信 TTY 处理工具启用提示")
    require(runtime.get("mcp_add_exact_tool_inventory_verified_before_accept") is True, "MCP 启用前必须核对精确工具清单")
    binding = runtime.get("base_runtime_binding", {})
    require(binding.get("reuse_exact_hermes_executable") is True, "可选能力必须复用基础闭环已验收的 Hermes 启动器")
    require(binding.get("reuse_exact_hermes_root") is True, "可选能力必须复用基础闭环已验收的 Hermes 根")
    require(binding.get("reuse_exact_profile") is True, "可选能力必须复用基础闭环已验收的 Profile")
    require(binding.get("rediscover_global_path_forbidden") is True, "可选能力不得重新从 PATH 发现另一份 Hermes")
    require(binding.get("capability_preflight_required_before_write") is True, "可选能力写入前必须检查当前运行时能力")
    require(binding.get("preflight_failure_requires_upgrade_consent_or_pause") is True, "可选能力预检失败时只能获得升级同意或暂停")

    local_knowledge = runtime.get("knowledge_base", {})
    require(local_knowledge.get("local_executor") == "scripts/scoped_knowledge_mcp.py", "本地知识库缺少受限执行器")
    require(local_knowledge.get("missing_knowledge_base_initializer") == "scripts/scoped_knowledge_mcp.py --initialize-only", "没有知识库的用户缺少受控初始化动作")
    require(local_knowledge.get("missing_knowledge_base_initialization_is_exclusive") is True, "新知识库初始化不得复用或覆盖已有目录")
    require(local_knowledge.get("default_mode") == "read_only", "知识库必须默认只读")
    require(local_knowledge.get("generic_file_or_terminal_exposed") is False, "知识库不得暴露通用文件或终端")
    require(local_knowledge.get("read_requires_per_action_model_transfer_elicitation") is True, "本地知识库内容交给模型前必须逐次确认")
    require(local_knowledge.get("write_requires_per_action_elicitation") is True, "知识库写入必须逐次确认")
    require(set(local_knowledge.get("allowed_suffixes", [])) == {".md", ".txt"}, "知识库文件类型白名单不正确")
    require(local_knowledge.get("path_traversal_and_symlinks_rejected") is True, "知识库必须拒绝路径穿越和符号链接")
    require(local_knowledge.get("optimistic_concurrency_sha256_required") is True, "知识库更新缺少并发覆盖保护")
    require(local_knowledge.get("state_directory_owner_and_mode_verified") is True, "知识库私有状态目录缺少所有者与权限复核")
    require(local_knowledge.get("writes_are_recoverable") is True, "知识库写入缺少可回滚记录")
    require(local_knowledge.get("delete_tool_exposed") is False, "知识库不得暴露通用删除工具")

    feishu_runtime = runtime.get("feishu", {})
    require(feishu_runtime.get("executor") == "scripts/scoped_feishu_mcp.py", "飞书缺少资源白名单执行器")
    require(feishu_runtime.get("read_is_fixed_document_allowlist") is True, "飞书读取必须锁定固定文档")
    require(feishu_runtime.get("document_url_requires_strict_official_host_and_path") is True, "飞书文档链接必须严格限制官方域名和文档路径")
    require(feishu_runtime.get("other_document_rejected_before_network") is True, "飞书越界读取必须在联网前拒绝")
    require(feishu_runtime.get("read_requires_per_action_model_transfer_elicitation") is True, "飞书正文交给模型前必须逐次确认")
    require(feishu_runtime.get("approval_prompt_names_originating_interface") is True, "飞书确认提示必须说明只在原请求界面确认")
    require(feishu_runtime.get("gateway_approval_timeout_seconds") == 120, "微信审批等待必须限制为 120 秒")
    require(feishu_runtime.get("mcp_elicitation_timeout_seconds") == 120, "MCP 确认等待必须与微信审批等待一致")
    require(feishu_runtime.get("mcp_tool_timeout_seconds") == 150, "MCP 工具总超时必须给确认结果保留返回余量")
    require(feishu_runtime.get("preapproval_is_unsupported_and_retry_restarts_original_request") is True, "不得把 /approve 写成预先开启权限的开关")
    require(feishu_runtime.get("timeout_without_inbound_approval_must_not_blame_feishu") is True, "没有收到微信审批时不得误报飞书故障")
    require(feishu_runtime.get("create_is_fixed_parent_allowlist") is True, "飞书创建必须锁定固定目录")
    require(feishu_runtime.get("write_disabled_without_fixed_parent") is True, "飞书未锁定创建位置时必须只读")
    require(feishu_runtime.get("write_tool_absent_without_fixed_parent") is True, "飞书未锁定创建位置时不得暴露写入工具")
    require(feishu_runtime.get("write_requires_per_action_elicitation") is True, "飞书创建必须逐次确认")
    require(feishu_runtime.get("content_passed_on_stdin_not_command_line") is True, "飞书正文不得放在命令参数")
    require(feishu_runtime.get("resource_identifiers_stored_in_private_scope_file_not_process_args") is True, "飞书私人资源标识不得出现在进程参数")
    require(feishu_runtime.get("official_cli_package") == "@larksuite/cli", "飞书必须使用官方 CLI 包")
    require(feishu_runtime.get("explicit_node_runtime_path_required") is True, "飞书必须显式绑定已核验的 Node.js 运行时")
    require(feishu_runtime.get("node_runtime_identity_verified") is True, "飞书必须验证 Node.js 运行时身份")
    require(feishu_runtime.get("isolated_runtime_does_not_depend_on_inherited_path") is True, "飞书隔离运行时不得依赖继承的 PATH")
    require(feishu_runtime.get("compatible_existing_cli_reused") is True, "兼容的现有 lark-cli 必须复用")
    require(feishu_runtime.get("missing_cli_install_requires_user_consent") is True, "安装飞书 CLI 前必须取得用户同意")
    require(feishu_runtime.get("dedicated_named_profile_does_not_overwrite_existing") is True, "飞书专用 Profile 不得覆盖现有应用配置")
    require(feishu_runtime.get("every_cli_call_explicitly_binds_named_profile") is True, "飞书每次调用都必须显式绑定专用 Profile")
    require(feishu_runtime.get("agent_bound_cli_preserves_exact_hermes_home") is True, "Agent 绑定的飞书 CLI 必须保留精确 Hermes 根")
    require(feishu_runtime.get("cli_version_and_docs_v2_help_verified") is True, "飞书 CLI 缺少版本与 Docs v2 能力验证")
    require(feishu_runtime.get("docs_v2_create_uses_current_content_and_parent_flags") is True, "飞书创建参数未锁定当前 Docs v2 契约")
    require(feishu_runtime.get("user_oauth_handoff_hides_url_and_device_code") is True, "飞书用户授权缺少不回传凭据的安全交接")
    require(feishu_runtime.get("user_only_completes_official_account_confirmation") is True, "飞书技术配置不得再次甩给用户")
    require(feishu_runtime.get("safe_missing_scope_names_are_allowlisted") is True, "飞书权限错误只能回传白名单内的 scope 名称")
    require(feishu_runtime.get("raw_cli_failure_output_is_not_echoed") is True, "飞书原始失败输出不得回传给模型或用户")

    coding_runtime = runtime.get("coding_agent", {})
    require(coding_runtime.get("executor") == "scripts/scoped_coding_mcp.py", "编程智能体缺少受控执行器")
    require(coding_runtime.get("generic_terminal_exposed_to_weixin") is False, "微信不得获得通用终端")
    require(coding_runtime.get("requires_clean_git_root") is True, "编程任务必须从干净 Git 根开始")
    require(coding_runtime.get("codex_runs_in_disposable_detached_worktree") is True, "Codex 必须在一次性 worktree 准备修改")
    require(coding_runtime.get("prepare_and_apply_are_separate_confirmations") is True, "编程准备与应用必须分两次确认")
    require(coding_runtime.get("original_project_unchanged_during_prepare") is True, "准备补丁时不得修改原项目")
    require(coding_runtime.get("cli_must_start_and_required_flags_be_present") is True, "Codex 必须能启动且具备受控运行参数")
    require(coding_runtime.get("mcp_startup_mechanically_checks_codex_cli_contract") is True, "Codex MCP 启动时必须机械核对 CLI 安全参数")
    require(coding_runtime.get("node_shebang_launcher_requires_explicit_verified_node") is True, "npm Codex 必须显式核验 Node.js")
    require(coding_runtime.get("installed_but_broken_cannot_count_as_existing") is True, "已安装但无法启动的 Codex 不得登记为可复用")
    require(coding_runtime.get("login_status_checked_before_mcp_enable") is True, "Codex 接入 MCP 前必须检查当前登录状态")
    require(coding_runtime.get("login_status_checked_before_each_prepare") is True, "Codex 每次准备修改前必须重新检查登录状态")
    require(coding_runtime.get("login_states") == ["logged_in", "not_logged_in", "expired_or_invalid"], "Codex 登录必须覆盖可用、未登录和失效三种状态")
    require(coding_runtime.get("login_repair_uses_official_or_device_flow_in_trusted_window") is True, "Codex 登录恢复必须使用可信窗口中的官方或设备流程")
    require(coding_runtime.get("login_secret_never_requested_in_chat") is True, "Codex 登录秘密不得在聊天中索取")
    require(coding_runtime.get("desktop_app_login_not_assumed_for_cli") is True, "Codex 桌面登录不得冒充 CLI 登录")
    require(coding_runtime.get("global_custom_provider_not_overwritten") is True, "Codex 不得覆盖用户的全局自定义模型配置")
    require(coding_runtime.get("dedicated_codex_home_supported") is True, "Codex 必须支持专用登录目录")
    require(coding_runtime.get("dedicated_codex_home_private") is True, "Codex 专用登录目录必须为私有目录")
    require(coding_runtime.get("mcp_explicitly_binds_codex_home") is True, "Codex MCP 必须显式绑定专用登录目录")
    require(coding_runtime.get("codex_exec_timeout_seconds") == 1800, "Codex 受控执行超时必须为 1800 秒")
    require(coding_runtime.get("mcp_elicitation_timeout_seconds") == 120, "Codex MCP 确认等待必须为 120 秒")
    require(coding_runtime.get("mcp_tool_timeout_seconds") == 1830, "Codex MCP 工具超时必须比 Codex 执行多留 30 秒")
    require(coding_runtime.get("runtime_auth_and_quota_failures_are_actionable") is True, "Codex 运行中登录与额度失效必须给出可操作恢复")
    require(coding_runtime.get("readonly_dedicated_home_failure_is_actionable") is True, "Codex 专用登录目录不可写时必须给出准确恢复")
    require(coding_runtime.get("raw_codex_failure_output_is_not_echoed") is True, "Codex 原始失败输出不得回传给模型或用户")
    require(coding_runtime.get("codex_sandbox") == "workspace-write", "Codex 必须使用 workspace-write 沙箱")
    require(coding_runtime.get("codex_network_access") is False, "受控 Codex 默认不得联网")
    require(coding_runtime.get("state_directory_owner_and_mode_verified") is True, "编程私有状态目录缺少所有者与权限复核")
    require(coding_runtime.get("task_record_updates_are_atomic") is True, "编程任务记录更新必须原子化")
    require(coding_runtime.get("apply_record_failure_restores_original_project") is True, "应用记录失败时必须恢复原项目")
    require(coding_runtime.get("rollback_record_failure_restores_applied_project") is True, "回滚记录失败时必须恢复回滚前项目")
    require(coding_runtime.get("commit_push_publish_deploy_allowed") is False, "受控编程不得提交、推送、发布或部署")
    require(coding_runtime.get("rollback_refuses_after_later_changes") is True, "编程回滚必须保护后续修改")
    require(coding_runtime.get("cross_device_bridge_included") is False, "本 Skill 不得悄悄建立跨设备代码桥")

    automation_runtime = runtime.get("daily_automation", {})
    require(automation_runtime.get("script_must_be_inside_profile_scripts_dir") is True, "定时脚本必须限制在 Profile scripts 目录")
    require(automation_runtime.get("deterministic_task_prefers_no_agent") is True, "确定性定时任务应优先 no-agent")
    require(automation_runtime.get("manual_run_required_before_schedule_acceptance") is True, "定时任务启用前必须手动运行")
    require(automation_runtime.get("run_receipt_required") is True, "定时任务验收缺少运行回执")
    require(automation_runtime.get("chinese_delivery_requires_wrap_response_false_readback") is True, "中文定时消息必须关闭英文包装并读回")
    require(automation_runtime.get("pause_resume_remove_documented") is True, "定时任务缺少暂停、恢复与移除说明")
    require(automation_runtime.get("path_traversal_negative_test_required") is True, "定时脚本缺少路径穿越负向测试")
    require(automation_runtime.get("cli_success_requires_positive_receipt_not_exit_code") is True, "定时任务不得只按退出码判断成功")
    require(automation_runtime.get("failed_run_must_persist_failed_receipt") is True, "定时任务失败必须留下持久失败回执")
    require(automation_runtime.get("failed_or_empty_output_cannot_be_accepted_as_delivery") is True, "失败或空输出不得冒充日报送达")
    require(automation_runtime.get("real_weixin_delivery_required_for_real_completion") is True, "定时任务真实完成必须有微信送达证据")

    isolation = contract.get("acceptance_isolation", {})
    require(isolation.get("dedicated_profile_required") is True, "Skill 验收必须使用专用 Profile")
    require(isolation.get("profile_must_not_be_default") is True, "Skill 验收不得使用 default Profile")
    require(isolation.get("profile_gateway_must_be_stopped_before_test") is True, "测试 Profile 的 gateway 必须先停止")
    require(isolation.get("profile_must_have_no_weixin_credentials_before_qr") is True, "测试 Profile 扫码前不得已有微信凭据")
    require(isolation.get("production_soul_memory_sessions_and_weixin_untouched") is True, "隔离验收不得修改生产人格、记忆、会话或微信")
    require(isolation.get("every_stateful_command_must_bind_profile") is True, "隔离验收的状态命令必须显式绑定 Profile")
    require(isolation.get("every_profile_dependent_command_must_bind_profile") is True, "隔离验收的 Profile 依赖命令必须显式绑定 Profile")
    require(isolation.get("named_profile_alone_is_credential_isolation") is False, "不得把命名 Profile 当作凭据隔离")
    require(isolation.get("strict_test_requires_clean_secret_sources") is True, "严格测试必须使用清洁秘密来源")
    require(isolation.get("protected_existing_assistant_must_not_be_read") is True, "严格验收不得读取受保护的现有助手")
    require(isolation.get("strict_test_requires_exclusive_isolated_root_marker") is True, "严格验收缺少独占隔离根标记")
    require(isolation.get("isolated_root_purpose_and_scope_bound") is True, "隔离根必须绑定用途与允许范围")
    require(isolation.get("protected_live_local_acceptance_uses_persistent_isolated_root") is True, "真实账号或跨轮本地验收不得使用临时根")
    require(isolation.get("persistent_local_root_requires_fresh_gate_before_first_auth") is True, "持久本地根首次认证前缺少新鲜门禁")
    require(isolation.get("persistent_local_root_reused_without_reasking_valid_credentials") is True, "持久本地根不得重复索取仍有效凭据")
    require(isolation.get("persistent_local_root_forbids_managed_service_install") is True, "受保护持久本地根不得安装受管服务")
    require(isolation.get("fresh_profile_gate_required_before_model_auth") is True, "模型认证前缺少新鲜 Profile 门禁")
    require(isolation.get("fresh_profile_gate_rejects_known_runtime_home_auth_sources") is True, "fresh 门禁未拒绝运行 HOME 的旧模型认证来源")
    require(isolation.get("chat_safety_baseline_required_before_model_auth") is True, "模型认证前缺少最小聊天安全基线")
    require(isolation.get("chat_safety_recheck_required_after_model_auth_before_probe") is True, "模型认证后首次调用前缺少安全复验")
    require(isolation.get("profile_permissions_rehardened_after_model_probe_before_weixin") is True, "模型探测后扫码前缺少 Profile 权限重新收紧")
    require(isolation.get("strict_test_hermes_commands_use_sanitized_environment_runner") is True, "严格验收命令未清洗继承环境")
    require(isolation.get("approved_hermes_root_required_by_safety_checkers") is True, "安全检查器未绑定已批准 Hermes 根")

    profile_lifecycle = contract.get("profile_lifecycle", {})
    require(profile_lifecycle.get("required_before_stateful_setup") is True, "状态写入前必须建立 Profile")
    require(profile_lifecycle.get("new_user_profile") == "fresh_nondefault", "新用户必须使用全新非 default Profile")
    require(profile_lifecycle.get("clone_for_new_user") is False, "新用户不得克隆旧 Profile")
    require(profile_lifecycle.get("fresh_create_command") == "hermes profile create <Profile> --no-alias --no-skills", "新 Profile 创建未关闭别名与 Skill 继承")
    require(profile_lifecycle.get("existing_name_reuse_allowed_for_new_or_isolation") is False, "新建或隔离测试不得复用同名 Profile")
    require(profile_lifecycle.get("resolved_path_must_end_with_profiles_profile") is True, "Profile 路径未精确绑定请求名称")
    require(profile_lifecycle.get("every_stateful_command_must_bind_same_profile") is True, "所有状态命令必须绑定同一 Profile")
    require(set(profile_lifecycle.get("binding_checks", [])) == {"profile_show", "config_path", "config_env_path"}, "Profile 绑定检查不完整")
    require(profile_lifecycle.get("binding_commands") == {
        "profile_show": "hermes profile show <Profile>",
        "config_path": "hermes -p <Profile> config path",
        "config_env_path": "hermes -p <Profile> config env-path",
    }, "Profile 绑定命令与当前 CLI 不一致")

    provenance = contract.get("credential_provenance", {})
    require(provenance.get("must_be_classified_without_secret_values") is True, "凭据来源必须无秘密分类")
    require(provenance.get("shared_or_unknown_requires_user_decision") is True, "共享或未知凭据来源必须由用户决定")
    require(provenance.get("strict_test_forbids_uncontrolled_shared_or_process_fallback") is True, "严格测试不得借用测试边界外的共享凭据")
    require(provenance.get("strict_test_runner_clears_inherited_model_and_weixin_secrets") is True, "严格测试未清除继承的模型或微信秘密")
    require(provenance.get("strict_test_shared_auth_must_stay_inside_isolated_root") is True, "严格测试共享认证目录未绑定隔离根")
    require(provenance.get("cloud_manager_unit_and_initial_process_forbid_uncontrolled_model_secrets") is True, "云端未阻止模型凭据从服务环境回退")
    require(provenance.get("configuration_detection_is_not_connectivity_evidence") is True, "配置检测不能冒充模型连通证据")
    require(provenance.get("classified_before_remote_or_billable_probe") is True, "凭据来源必须在远程模型或计费探测前分类")
    require(provenance.get("direct_auth_list_output_allowed_in_agent_logs") is False, "不得把 auth list 直接捕获到 Agent 日志")
    require(provenance.get("raw_deep_status_allowed_in_agent_logs") is False, "不得把原始 status --deep 捕获到 Agent 日志")
    require(provenance.get("protected_mode_dashboard_secret_entry_allowed") is False, "受保护验收不得使用机器级 Dashboard 录入秘密")
    require(
        provenance.get("protected_mode_secret_entry_channels") == [
            "prompt_synchronized_native_secret_dialog_via_isolated_runner",
            "isolated_runner_trusted_tty",
            "provider_official_oauth_inside_isolated_root",
        ],
        "受保护验收的秘密录入通道必须绑定隔离根",
    )

    handoff = contract.get("conversation_first_handoff", {})
    require(handoff.get("user_terminal_typing_required_by_default") is False, "小白默认不得输入终端命令")
    require(handoff.get("agent_executes_noninteractive_steps") is True, "非交互步骤必须由 Agent 执行")
    require(
        handoff.get("interactive_priority") == [
            "host_native_secret_or_official_ui",
            "agent_launched_trusted_terminal",
            "manual_single_command_last_resort",
        ],
        "交互步骤没有按官方界面、Agent 代开窗口、单命令回退排序",
    )
    require(handoff.get("trusted_handoff_launcher") == "scripts/launch_trusted_handoff.py", "缺少受信终端交接器")
    require(handoff.get("cloud_api_key_uses_prompt_synchronized_native_dialog") is True, "云端 API key 缺少同步原生隐藏框")
    require(handoff.get("macos_secret_dialog_backend") == "osascript_display_dialog_hidden_answer", "macOS 必须使用原生隐藏输入框")
    require(handoff.get("macos_secret_field_visible_and_editable_required") is True, "macOS 密钥输入栏必须可见且可编辑")
    require(handoff.get("macos_tkinter_secret_dialog_allowed") is False, "macOS 不得回退到无输入栏的 Tkinter 弹窗")
    require(handoff.get("cloud_api_key_secret_collected_only_after_masked_prompt") is True, "不得在掩码提示前收集 API key")
    require(handoff.get("cloud_api_key_early_ssh_pipe_forbidden") is True, "不得把 API key 提前管道到 SSH")
    require(handoff.get("cloud_api_key_label_user_action_required") is False, "不得让用户第二次确认凭据标签")
    require(handoff.get("cloud_api_key_echo_detection_required") is True, "API key 交接缺少回显检测")
    require(handoff.get("cloud_api_key_save_receipt_required") is True, "API key 交接缺少保存回执门禁")
    require(handoff.get("cloud_api_key_raw_child_output_returned") is False, "API key 交接不得回传原始子进程输出")
    require(handoff.get("weixin_direct_setup_helper") == "scripts/setup_weixin_direct.py", "微信扫码没有直达 Weixin 的固定助手")
    require(handoff.get("weixin_platform_menu_user_action_required") is False, "不得让小白在终端选择微信平台")
    require(handoff.get("weixin_permission_menu_user_action_required") is False, "不得让小白在终端逐项选择微信权限")
    require(handoff.get("weixin_user_action") == "qr_scan_and_phone_confirmation_only", "微信步骤的用户动作必须只剩扫码和手机确认")
    require(handoff.get("windows_third_party_terminal_required") is False, "Windows 不得要求安装第三方终端")
    require(handoff.get("windows_agent_launched_system_console_allowed") is True, "Windows 缺少由 Agent 代开的系统二维码窗口")
    require(handoff.get("qr_or_login_url_requires_ephemeral_trusted_display") is True, "二维码必须留在临时受信显示面")
    require(handoff.get("launcher_returns_only_nonsecret_open_state") is True, "交接器不得回传命令或短时凭据")
    require(handoff.get("manual_terminal_requires_reason") is True, "终端回退前必须解释无法代执行的原因")
    require(handoff.get("manual_terminal_requires_user_opt_in") is True, "终端回退必须由用户明确继续")
    require(handoff.get("manual_terminal_max_commands") == 1, "终端回退最多只能有一条命令")
    require(handoff.get("manual_terminal_command_must_be_atomic") is True, "终端回退命令必须原子化")
    require(handoff.get("manual_terminal_command_must_be_fully_resolved") is True, "终端回退命令不得含占位符")
    require(handoff.get("manual_terminal_command_must_not_contain_secret") is True, "终端回退命令不得含秘密")
    require(handoff.get("generic_model_wizard_allowed_before_provider_is_fixed") is False, "未锁定 provider 时不得运行通用模型向导")
    require(handoff.get("provider_scoped_auth_required") is True, "模型认证必须锁定 provider")
    require(handoff.get("provider_oauth_route") == "auth add <provider> --type oauth", "OAuth 登录没有明确 provider 路由")
    require(handoff.get("provider_api_key_route") == "auth add <provider> --type api_key", "API key 录入没有明确 provider 路由")
    require(handoff.get("device_code_qr_or_login_url_allowed_in_chat") is False, "短时登录凭据不得进入聊天")
    require(handoff.get("cloud_remote_service_user_required") is True, "云端交接器必须绑定远端服务账号")
    require(handoff.get("cloud_remote_account_switch_required") is True, "云端交接器必须显式选择账号切换方式")
    require(handoff.get("cloud_direct_requires_matching_explicit_ssh_user") is True, "云端 direct 模式必须核对 SSH 用户")
    require(
        handoff.get("cloud_remote_account_switch_modes") == ["root-runuser", "sudo", "direct"],
        "云端账号切换方式不完整",
    )
    require(
        set(handoff.get("user_must_not_be_asked_to", []))
        == {"cd", "source", "ssh", "assemble_paths", "run_multiple_commands"},
        "仍可能让小白拼接或分步运行终端命令",
    )
    for step in ("step_2_hermes", "step_3_model", "step_4_weixin"):
        require("conversation_first_handoff" in step_router.get(step, []), f"{step} 缺少对话优先交接规则")

    boundary = contract.get("execution_boundary", {})
    require(boundary.get("default_weixin_profile") == "chat_only", "微信默认必须是仅聊天档")
    require(boundary.get("workspace_required_before_weixin") is True, "扫码前必须选择专用工作区")
    require(boundary.get("workspace_required_before_model_auth") is True, "模型认证前必须建立专用工作区")
    require(boundary.get("workspace_must_not_be_home") is True, "工作区不得默认使用主目录")
    require(
        boundary.get("interactive_runner_by_mode")
        == {
            "ordinary_new_or_authorized": "direct_profile_bound",
            "protected_acceptance": "isolation_guard_run",
            "cloud_new_or_test": "isolation_guard_run_cloud",
        },
        "交互命令没有按普通、受保护与云端模式绑定唯一 runner",
    )
    require(boundary.get("cloud_interactive_home_matches_service_runtime_home") is True, "云端认证 HOME 与 gateway 运行 HOME 不一致")
    require(boundary.get("user_visible_command_placeholders_forbidden") is True, "不得把占位命令交给小白")
    require(boundary.get("protected_and_cloud_sensitive_interactions_require_trusted_tty") is True, "受保护/云端敏感交互命令未机械拒绝非 TTY 环境")
    require(
        boundary.get("external_qwen_oauth_runner") == "isolation_guard.py run-qwen-auth",
        "Qwen 外部 OAuth 命令未绑定固定用途隔离 runner",
    )
    require(boundary.get("terminal_cwd_is_starting_directory_not_sandbox") is True, "不得把 terminal.cwd 当沙箱")
    require(boundary.get("local_backend_is_sandbox") is False, "不得把 local 后端当沙箱")
    require(boundary.get("high_risk_backend") == "docker", "高风险工具默认必须使用 Docker")
    require(boundary.get("forbidden_approval_mode") == "off", "必须禁止 approvals.mode=off")
    require(boundary.get("chat_only_always_enabled_toolsets") == ["clarify"], "仅聊天档常开工具必须最小化")
    optional_toolsets = set(boundary.get("chat_only_enable_only_if_selected_and_verified", []))
    require({"web", "vision", "memory", "session_search"} <= optional_toolsets, "可选工具未与默认常开集合分开")
    required_disabled = {"skills", "terminal", "file", "code_execution", "browser", "computer_use", "delegation", "cronjob"}
    require(required_disabled <= set(boundary.get("chat_only_disable_if_present", [])), "仅聊天档未覆盖全部高风险工具")
    require(boundary.get("skills_write_approval_required_if_enabled") is True, "启用 skills 时必须强制写入审批")
    require(boundary.get("weixin_reasoning_visible") is False, "Weixin 不得展示模型推理")
    require(boundary.get("cli_reasoning_visible_before_model_probe") is False, "首次模型探测不得展示推理")
    require(boundary.get("chat_only_memory_injection_enabled") is False, "未选择记忆时不得注入内置记忆或用户画像")
    require(boundary.get("chat_only_effective_enabled_toolsets_exact") == ["clarify"], "仅聊天档有效工具集必须精确收缩")
    require(boundary.get("chat_only_mcp_servers_allowed") is False, "仅聊天档不得继承 MCP 服务器")
    require(boundary.get("hidden_or_unknown_platform_toolsets_allowed") is False, "隐藏或未知平台工具集必须失败关闭")
    require(boundary.get("process_weixin_prefix_overrides_allowed") is False, "当前或未来进程 WEIXIN_* 覆盖必须失败关闭")
    require(boundary.get("unknown_profile_weixin_keys_allowed") is False, "未知 Profile WEIXIN_* 键必须失败关闭")
    require(boundary.get("pre_qr_checker") == "scripts/check_pre_qr_safety.py", "扫码前缺少自动失败关闭检查器")
    require(boundary.get("pre_model_baseline_helper") == "scripts/apply_chat_safety_baseline.py", "模型认证前缺少可执行安全基线")
    require(boundary.get("pre_model_and_pre_qr_checker") == "scripts/check_pre_qr_safety.py", "模型调用前与扫码前未共用自动门禁")
    require(boundary.get("post_model_probe_baseline_reapply_required") is True, "模型探测后未重新应用安全基线")
    require(boundary.get("isolation_checker") == "scripts/isolation_guard.py", "严格验收缺少隔离根检查器")
    require(boundary.get("pre_qr_expected_hermes_root_required") is True, "扫码前检查未绑定已批准 Hermes 根")
    pre_qr = boundary.get("pre_qr_static_checks", [])
    require(len(pre_qr) >= 4, "扫码前静态门禁不完整")
    require("profile_path_matches_requested_name" in pre_qr, "扫码前未精确绑定 Profile 路径")
    require("weixin_state_absent_before_qr" in pre_qr, "扫码前未拒绝旧 Weixin 状态")
    require("model_and_profile_secret_stores_are_private" in pre_qr, "模型认证存储权限未进入调用前门禁")
    require(
        "dm_allowlist_is_nonempty_and_owner_only" not in pre_qr and "group_policy_is_disabled" not in pre_qr,
        "扫码前不可验证许可名单与群聊策略：它们在扫码向导中产生",
    )
    post_start = set(boundary.get("post_qr_pre_start_checks", []))
    require(
        {
            "dm_allowlist_is_nonempty_and_owner_only",
            "group_policy_is_disabled",
            "allow_all_flags_are_false",
            "home_channel_set",
            "home_channel_matches_owner",
            "config_access_overrides_absent",
            "process_weixin_overrides_absent",
            "weixin_endpoints_are_official_or_builtin",
            "profile_path_matches_requested_name",
            "secret_files_and_directory_private",
            "weixin_and_auth_stores_private",
            "profile_store_path_scope_resolved",
            "service_absent_and_gateway_not_running_until_persona_ready",
        } <= post_start,
        "扫码后启动前门禁缺少访问控制配置证据",
    )
    require(set(boundary.get("post_qr_runtime_negative_tests", [])) == {
        "write_inside_workspace_succeeds_when_file_tools_are_authorized",
        "read_or_write_outside_workspace_is_denied",
        "prompt_injection_cannot_enable_disabled_tools",
        "destructive_command_requires_approval_or_is_denied",
        "skill_manage_is_unavailable_or_requires_explicit_approval",
    }, "扫码后运行时负向测试不完整或重复")

    supervision = contract.get("setup_supervision", {})
    require(supervision.get("proactive_nonsecret_state_polling_required") is True, "微信向导缺少主动状态检测")
    require(supervision.get("polling_requires_baseline_snapshot") is True, "主动轮询缺少开始前状态基线")
    require(supervision.get("polling_requires_fresh_state_transition") is True, "主动轮询不得把旧标记冒充本轮新鲜状态")
    require(supervision.get("polling_must_not_expose_secret_values") is True, "主动轮询不得输出秘密值")
    require(supervision.get("screen_report_protocol_is_fallback") is True, "报屏协议只能作为备用")
    require(supervision.get("screen_report_fallback_when_state_not_observable") is True, "无法观察本轮状态时必须回退报屏协议")
    require(supervision.get("qr_success_is_not_configuration_complete") is True, "不得把扫码成功当作配置完成")
    require(supervision.get("generic_gateway_setup_used_for_weixin") is False, "微信不得再进入全平台 gateway setup 菜单")
    require(supervision.get("direct_weixin_helper_fixes_owner_allowlist_and_groups_disabled") is True, "微信直达助手没有固定主人名单与关闭群聊")
    require(supervision.get("direct_weixin_helper_never_starts_or_installs_gateway") is True, "微信直达助手不得启动或安装 gateway")
    require(supervision.get("exit_before_default_yes_service_prompts") is True, "必须在默认是的服务启动提示前安全退出向导")
    require(supervision.get("final_exit_notice_required") is True, "微信配置完成后必须主动提示退出向导")

    progress = contract.get("progress_display", {})
    require(progress.get("full_five_step_rail_at_start") is True, "首次回应缺少五步进度全览")
    require(progress.get("full_five_step_rail_on_step_transition") is True, "进入新步骤时缺少五步进度全览")
    require(progress.get("compact_status_within_step") is True, "同一步内应使用紧凑状态")
    require(progress.get("progress_line_cannot_replace_explanation") is True, "进度条不得代替说明性引导")
    require(progress.get("new_step_explains") == ["user_outcome", "why_now", "agent_action", "single_user_action", "visible_success_signal"], "新步骤缺少结果、原因、Agent 动作、用户单一动作或成功标志")
    require(progress.get("maximum_user_actions_per_prompt") == 1, "每次提示不得要求小白执行多个动作")

    access = contract.get("weixin_access", {})
    require(access.get("dm_policy") == "allowlist", "个人助手私聊必须使用 allowlist")
    require(access.get("allowed_users_nonempty") is True, "私聊许可名单不得为空")
    require(access.get("allowed_users_exact_owner_only") is True, "私聊许可名单必须只有主人")
    require(access.get("group_policy") == "disabled", "群聊必须默认关闭")
    require(access.get("configuration_evidence_required") is True, "访问控制缺少必需配置证据")
    require(access.get("second_account_negative_test_required_for_base_completion") is False, "不得强制用户拥有第二测试账号")
    require(access.get("second_account_negative_test_status_when_unavailable") == "not_verified", "缺少第二账号时必须标记未验证")
    require(access.get("effective_policy_not_env_text_only") is True, "访问控制不得只检查 .env 文本")
    require(access.get("prestart_checker") == "scripts/check_profile_safety.py", "访问控制缺少安全检查器")

    platform = contract.get("platform_gate", {})
    require("macos_intel" in platform.get("unsupported", []), "必须在安装前阻止 Intel Mac")
    require({"macos_apple_silicon", "windows_10_11_x86_64_aarch64", "linux_x86_64_aarch64"} <= set(platform.get("supported", [])), "支持平台矩阵不完整")
    require({"uname_m_is_arm64", "macos_version_at_least_12", "git_version_succeeds", "desktop_native_build_dependency_available"} <= set(platform.get("macos_required_checks", [])), "macOS 安装前置检查不完整")
    require("get_command_hermes_after_new_terminal" in platform.get("windows_required_checks", []), "Windows 缺少新终端 PATH 检查")
    require("profile_secret_acl_private" in platform.get("windows_required_checks", []), "Windows 缺少秘密 ACL 检查")
    require({"git_version_succeeds", "curl_version_succeeds", "xz_version_succeeds"} <= set(platform.get("linux_required_checks", [])), "Linux 本地前置检查不完整")
    install_result = platform.get("installer_result_classification", {})
    require(install_result.get("network_clone_timeout_counts_as_install_success") is False, "网络克隆超时不得算安装成功")
    require(install_result.get("lockfile_hash_fallback_must_be_disclosed") is True, "锁文件哈希降级必须向用户说明")
    require(install_result.get("lockfile_hash_fallback_counts_as_strict_supply_chain_pass") is False, "重新解析依赖不得冒充严格供应链通过")
    require(install_result.get("core_cli_and_optional_dependencies_have_separate_status") is True, "核心 CLI 与可选依赖必须分别记录状态")
    require(install_result.get("installer_completion_banner_is_sufficient_evidence") is False, "安装完成横幅不得单独作为通过证据")
    require(install_result.get("blind_retry_after_no_progress") is False, "安装无进展后不得盲目重试")
    require({"profile_selector", "gateway_run_foreground", "platform_tool_policy", "provider_scoped_auth", "gateway_install_start_flags"} <= set(platform.get("required_hermes_capabilities", [])), "Hermes 能力闸不完整")

    local_service = contract.get("local_service_lifecycle", {})
    require(local_service.get("before_persona") == "service_not_installed_and_gateway_not_running", "本地人格前不得安装或启动服务")
    require(local_service.get("acceptance_command") == "hermes -p <Profile> gateway run", "本地临时验收必须使用前台 gateway run")
    require(local_service.get("protected_acceptance_command") == "isolation_guard.py run ... -- -p <Profile> gateway run", "受保护验收启动必须绑定隔离 runner 与 Profile")
    require(local_service.get("protected_foreground_stop_command") == "isolation_guard.py run ... -- -p <Profile> gateway stop", "受保护前台停止必须使用 Hermes 的 Profile 定向停止")
    require(local_service.get("protected_foreground_stop_is_profile_scoped") is True, "受保护前台停止不得跨 Profile")
    require(local_service.get("protected_foreground_restart_counts_as_persistence") is False, "受保护前台重启不得冒充持久服务")
    require("--force --start-now --start-on-login" in local_service.get("persistence_command", ""), "本地持久服务缺少明确启用命令")
    require(local_service.get("persistence_only_after_acceptance") is True, "本地持久服务只能在验收后启用")

    service = contract.get("cloud_service", {})
    require(service.get("scope") == "service_account_user_with_linger", "云端服务必须统一为服务账号用户级 unit + linger")
    require(service.get("run_as_root") is False, "云端 Hermes 服务不得以 root 运行")
    require(service.get("interactive_runner") == "isolation_guard.py run-cloud", "云端配置、模型与扫码必须使用 run-cloud")
    require(service.get("interactive_home_matches_gateway_runtime_home") is True, "云端交互认证与 gateway 必须使用同一 HOME")
    require(service.get("service_runner_requires_cloud_service_root_purpose") is True, "云端 run-service 必须要求 cloud-service 用途根")
    commands = service.get("commands", {})
    for action in ("stage", "start", "stop", "restart", "status", "persist_stage"):
        command = commands.get(action, "")
        require("isolation_guard.py run-service" in command, f"云端 {action} 未使用真实 HOME 服务隔离通道")
        require("--root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway " in command, f"云端 {action} 必须绑定专用根、已核验启动器和显式 Profile")
        require("--system" not in command and "sudo" not in command, f"云端 {action} 不得跨 sudo/root 解析 Profile")
    persist_stage = commands.get("persist_stage", "")
    require("--force --no-start-now --start-on-login" in persist_stage and "--start-now" not in persist_stage, "云端持久化必须重写为 enabled 但保持 stopped")
    require({"systemd_is_running", "user_scope_unit_manageable", "linger_enabled", "systemd_manager_has_no_uncontrolled_secret_environment", "root_or_sudo_admin_path", "curl", "git", "xz", "service_user_login_path", "absolute_hermes_launcher", "playwright_dependency_decision", "timezone_checked"} <= set(service.get("preflight_required", [])), "云端安装前置检查不完整")
    admin_commands = service.get("admin_commands", {})
    require(admin_commands.get("enable_linger_as_root") == "loginctl enable-linger <非root账号>", "root 管理员 linger 动作错误")
    require(admin_commands.get("enable_linger_with_sudo") == "sudo loginctl enable-linger <非root账号>", "非 root 管理员 linger 动作错误")
    require(service.get("admin_privilege_path") == "root_without_sudo_or_nonroot_with_sudo", "云端不得无条件要求 sudo")
    deployment_sequence = service.get("deployment_sequence", [])
    model_boundary_steps = (
        "chat_safety_baseline_applied",
        "pre_model_safety_gate_passed",
        "model_authenticated",
        "post_model_safety_gate_passed",
        "model_probe_passed",
        "post_model_probe_permissions_rehardened",
        "weixin_authenticated",
    )
    require(
        all(step in deployment_sequence for step in model_boundary_steps)
        and [deployment_sequence.index(step) for step in model_boundary_steps]
        == sorted(deployment_sequence.index(step) for step in model_boundary_steps),
        "云端模型调用前安全边界顺序错误",
    )
    require(
        deployment_sequence == [
            "admin_readonly_preflight",
            "user_confirms_deployment",
            "test_resource_ledger_frozen",
            "service_account_created",
            "linger_enabled",
            "service_account_resource_ledger_initialized",
            "resource_ledger_prewrite_passed",
            "release_directory_created_and_recorded",
            "release_uploaded_and_hash_verified",
            "model_host_selected_without_secret",
            "service_account_preflight_from_release",
            "hermes_installed",
            "isolated_root_created",
            "isolated_root_recorded",
            "workspace_created_and_recorded",
            "profile_created",
            "profile_directory_recorded",
            "fresh_profile_gate_passed",
            "chat_safety_baseline_applied",
            "pre_model_safety_gate_passed",
            "model_authenticated",
            "post_model_safety_gate_passed",
            "model_probe_passed",
            "post_model_probe_permissions_rehardened",
            "weixin_authenticated",
            "service_staged_stopped_disabled",
            "systemd_env_guard_installed",
            "resource_ledger_sealed",
            "systemd_prestart_env_verified",
            "persona_written",
            "service_started",
            "systemd_runtime_env_verified",
        ],
        "云端发布包、服务账号、模型主机与预检顺序不可执行",
    )
    require(service.get("all_hermes_commands_run_in_service_account_login") is True, "云端 Hermes 命令必须固定服务账号登录身份")
    require(service.get("service_management_commands_use_real_home_isolation_runner") is True, "云端用户服务命令未使用真实 HOME 隔离通道")
    require(service.get("profile_binding_required_for_every_command") is True, "云端每条命令必须绑定 Profile")
    require(service.get("unit_must_pin_profile_home") is True, "云端 unit 必须固定 Profile home")
    require(service.get("unit_environment_must_reject_unapproved_weixin_overrides") is True, "云端 unit 缺少有效环境覆盖检查")
    require(service.get("unit_environment_must_reject_uncontrolled_model_secrets") is True, "云端 unit 未拒绝继承模型秘密")
    require(service.get("test_resource_ledger_required_before_write_on_shared_server") is True, "共享服务器写入前缺少测试资源台账")
    require(service.get("release_package_verifier") == "scripts/verify_release_package.py", "云端发布包缺少可执行验证器")
    require(service.get("release_package_extract_without_overwrite") is True, "云端发布包不得覆盖解压")
    require(service.get("release_package_artifacts_outside_skill_tree") is True, "发布制品不得留在最终 Skill 文件树")
    require(service.get("release_package_rejects_unsafe_archive_entries") is True, "云端发布包必须拒绝危险归档项")
    ledger_guard = service.get("test_resource_ledger_guard", {})
    require(ledger_guard.get("script") == "scripts/resource_ledger_guard.py", "共享服务器缺少机器可校验资源台账")
    require(ledger_guard.get("new_service_account_required") is True, "共享服务器小号测试未强制全新服务账号")
    require(ledger_guard.get("bootstrap_guard_runs_without_untracked_remote_file") is True, "资源台账引导脚本不得先创建未跟踪远端文件")
    require(ledger_guard.get("host_and_instance_label_binding_required") is True, "资源台账未绑定目标实例")
    require(ledger_guard.get("planned_paths_and_unit_must_be_absent") is True, "资源台账未拒绝既有资源冲突")
    require(ledger_guard.get("each_created_path_inode_recorded_immediately") is True, "资源台账未即时记录新建目录身份")
    require(ledger_guard.get("partial_cleanup_requires_recorded_or_absent_paths") is True, "部分部署清理未限制已记录或仍缺席路径")
    require(ledger_guard.get("deployed_inode_seal_required") is True, "资源台账未检测路径替换漂移")
    require(ledger_guard.get("seal_requires_current_host_uid_home_binding") is True, "资源封存前未重验主机账号绑定")
    require(ledger_guard.get("cleanup_preview_never_deletes") is True, "清理预览不得执行删除")
    require(ledger_guard.get("cleanup_requires_no_same_uid_gateway_process") is True, "清理前未排除服务账号残留 gateway")
    require(ledger_guard.get("unknown_unit_state_fails_cleanup") is True, "清理流程把未知 unit 状态误当停止")
    require(ledger_guard.get("cleanup_verification_required") is True, "精确清理缺少事后核验")
    require(ledger_guard.get("authorization_creation_intent_recorded_before_external_flow") is True, "外部授权前未记录创建意图")
    require(service.get("cleanup_only_exact_test_created_resources") is True, "测试清理未限制精确新建资源")
    systemd_guard = service.get("systemd_environment_guard", {})
    require(systemd_guard.get("script") == "scripts/systemd_env_guard.py", "云端缺少 systemd 环境隔离脚本")
    require(systemd_guard.get("manager_check_before_install") is True, "云端预检缺少 manager 环境覆盖门禁")
    require(systemd_guard.get("dropin_unsets_current_weixin_keys") is True, "云端 unit 缺少 UnsetEnvironment 防线")
    require(systemd_guard.get("unit_environment_files_for_weixin_allowed") is False, "云端不得从 unit EnvironmentFile 注入 Weixin")
    require(systemd_guard.get("unit_execstart_and_profile_home_must_match") is True, "云端 unit 未绑定启动器、Profile 与 HERMES_HOME")
    require(systemd_guard.get("expected_hermes_root_required") is True, "云端 systemd 门禁未绑定本轮专用 Hermes 根")
    require(systemd_guard.get("stage_must_be_inactive_disabled") is True, "云端暂存服务未锁定 inactive + disabled")
    require(systemd_guard.get("prestart_must_be_inactive") is True, "云端启动前未锁定 inactive")
    require(systemd_guard.get("prerestart_must_be_active") is True, "云端重启前未锁定 active")
    require(systemd_guard.get("runtime_expected_enabled_required") is True, "云端运行态未显式核对 enabled/disabled")
    require(systemd_guard.get("prestart_check_required") is True, "云端每次启动前缺少环境复验")
    require(systemd_guard.get("runtime_proc_environ_boolean_check_required") is True, "云端启动后缺少实际进程环境布尔复验")
    require(systemd_guard.get("runtime_proc_cmdline_profile_check_required") is True, "云端启动后缺少实际进程 Profile 复验")
    require(systemd_guard.get("manager_unit_and_initial_process_model_secret_check_required") is True, "云端缺少模型秘密继承门禁")
    require(systemd_guard.get("manager_and_process_home_must_match_os_account") is True, "云端服务 HOME 未绑定操作系统账号")
    require(systemd_guard.get("unit_home_override_must_be_absent_or_match_os_account") is True, "云端 unit HOME 覆盖未绑定操作系统账号")
    require(systemd_guard.get("ambient_credential_home_overrides_forbidden") is True, "云端未阻断外部凭据目录覆盖")
    require(systemd_guard.get("same_service_user_gateway_process_scan_required") is True, "云端缺少同服务账号 gateway 全量扫描")
    require(systemd_guard.get("target_must_be_only_gateway_for_service_user") is True, "云端未强制目标为服务账号唯一 gateway")
    require(systemd_guard.get("after_persist_and_reboot_recheck_required") is True, "云端持久化或重启后缺少环境复验")
    require(
        systemd_guard.get("persistence_sequence") == [
            "stop_verified",
            "force_rewrite_enabled_but_stopped",
            "prestart_check_on_rewritten_unit",
            "explicit_gateway_start",
            "runtime_environment_check",
        ],
        "云端持久化没有在重写后、启动前复验新 unit",
    )
    require(service.get("remote_checker_release_hash_must_match") is True, "远端安全检查器缺少发布哈希一致性门禁")
    require(service.get("weixin_requires_new_public_inbound_port") is False, "Weixin 云端不得要求公网入站端口")
    require(service.get("dashboard_public_internet_exposure_allowed") is False, "Dashboard 不得直接暴露公网")
    require(service.get("secret_backup_scope_review_required") is True, "云端秘密必须经过备份范围审查")

    qr = contract.get("cloud_qr", {})
    require(qr.get("same_phone_short_url_verified") is False, "不得把同手机短时 URL 标记为已验证")
    require(qr.get("second_trusted_screen_required") is True, "云端扫码必须预先要求第二块可信屏幕")
    require(qr.get("stop_before_purchase_if_unavailable") is True, "没有第二屏幕必须在购买前停止")

    cutover = contract.get("cloud_cutover", {})
    expected_cutover = [
        "prepare_cloud_without_starting_gateway",
        "verify_cloud_config_and_rollback",
        "stop_local_gateway",
        "verify_local_gateway_stopped",
        "start_cloud_gateway",
        "verify_cloud_weixin_round_trip",
        "on_failure_stop_cloud_before_restore_local",
    ]
    require(cutover.get("duplicate_poller_allowed") is False, "迁移不得允许双轮询")
    require(cutover.get("steps") == expected_cutover, "迁移切换顺序不安全")

    memory = contract.get("persistent_memory_verification", {})
    require(memory.get("session_count") == 3, "记忆验收必须使用三个新会话")
    require(memory.get("steps") == [
        "session_a_add_tagged_nonsensitive_fact",
        "session_b_verify_fact_then_remove_memory_entry",
        "session_c_verify_memory_entry_absent",
    ], "记忆验收会话顺序不正确")
    require(set(memory.get("removal_does_not_erase", [])) == {"session_history", "logs", "provider_records"}, "记忆移除保留范围披露不完整")

    revoke = contract.get("knowledge_revoke", {})
    require(set(revoke.get("mechanisms", [])) == {"obsidian_path", "obsidian_sync", "syncthing_share", "feishu_oauth"}, "知识库撤权机制不完整")
    require("negative_read_test_must_be_denied" in revoke.get("steps", []), "知识库撤权缺少负向读取测试")

    switch = contract.get("model_switch", {})
    require(switch.get("strict_zero_downtime_promised") is False, "不得承诺模型切换绝对零中断")
    require(switch.get("steps", [])[-1:] == ["on_failure_restore_old_route"], "模型切换缺少失败恢复旧路由")

    detection = contract.get("incremental_detection", {})
    require("user_directories" in detection.get("requires_explicit_scope", []), "用户目录读取必须显式授权")
    require("other_apps" in detection.get("requires_explicit_scope", []), "其他应用检测必须显式授权")

    quality = contract.get("quality_acceptance", {})
    required_quality = {"ten_turn_chinese_soak", "long_message_round_trip", "rapid_two_message_no_duplicate_or_loss", "gateway_restart_recovery", "real_non_welcome_task", "error_message_states_problem_and_next_action", "natural_self_intro_without_internal_rules", "reasoning_hidden_from_weixin", "unselected_memory_not_injected"}
    require(required_quality <= set(quality.get("release_grade_required", [])), "发布级验收缺少稳定性或恢复门槛")
    require(quality.get("unperformed_fault_injection_status") == "not_verified", "未执行的故障注入必须标记未验证")

    completion = contract.get("completion_states", {})
    require(set(completion.get("chat_usable", [])) == {"base_completion_required_all_passed", "current_same_profile_gateway_running", "post_handoff_real_weixin_round_trip"}, "聊天可用状态定义不正确")
    require("gateway_restart_real_weixin_round_trip" in completion.get("persistent_runtime_verified", []), "持久运行缺少真实重启往返")
    require({"real_machine_reboot", "boot_id_changed", "post_reboot_real_weixin_round_trip"} <= set(completion.get("boot_recovery_verified", [])), "开机恢复缺少真实机器重启往返")
    require(completion.get("simulation_cannot_satisfy_real_state") is True, "模拟结果不得满足真实完成状态")
    require(completion.get("ten_turn_and_fault_injection_are_release_grade_not_first_use_blockers") is True, "发布级压力测试不得阻断第一次聊天可用")

    secret = contract.get("secret_lifecycle", {})
    require(secret.get("at_rest_permissions_required") is True, "秘密落盘缺少权限门禁")
    require(secret.get("backup_snapshot_scope_review_required") is True, "秘密缺少备份快照范围审查")
    require(secret.get("incident_response_steps", [])[-1:] == ["rerun_prestart_and_real_round_trip_checks"], "秘密泄露响应缺少重新验收")

    return failures


def _read_documents(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*.md")
    }


def validate_documents(
    root: Path,
    contract: dict,
    overrides: Mapping[str, str] | None = None,
) -> list[str]:
    documents = _read_documents(root)
    if overrides:
        documents.update(overrides)
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    skill = documents["SKILL.md"]
    cloud = documents["references/cloud-deployment.md"]
    setup = documents["references/setup-guide.md"]
    weixin = documents["references/weixin-setup-zh.md"]
    tools = documents["references/tools.md"]
    faq = documents["references/operation-faq.md"]
    chinese_ux = documents["references/chinese-ux.md"]
    model = documents["references/model-routing.md"]
    install = documents["references/install-skill.md"]
    security = documents["references/security-boundary.md"]
    acceptance = documents["references/acceptance.md"]
    direct_setup = (root / "scripts" / "setup_weixin_direct.py").read_text(encoding="utf-8")
    all_text = "\n".join(documents.values())

    require(SKILL_TITLE in skill, "SKILL.md 版本标题不正确")
    require("references/flow-contract.json" in skill, "SKILL.md 未加载机器流程契约")
    require("references/security-boundary.md" in skill, "SKILL.md 未加载执行边界")
    require("搜索是可选能力" in skill, "SKILL.md 未明确搜索是可选能力")
    require("搜不了不算搭建完成" not in all_text, "存在搜索必过的矛盾表述")
    require("```mermaid" in skill and "基础微信助手完成" in skill, "SKILL.md 缺少五步总流程图")
    require("检查、复用或安装 Hermes" in skill and "检查、复用或配置模型" in skill, "基础流程仍把复用误写成必定重装或重配")
    require("基础闭环完成前不主动询问用户是否需要这些能力" in skill, "主流程仍在基础闭环前询问知识库或编程")
    require("三项正式能力升级" in skill and "A. 连接知识库" in skill and "B. 连接编程智能体" in skill and "C. 设置日常自动化" in skill, "基础完成后的三项能力升级入口不完整")
    require("天气与 AI 日报不作为正式可选项" not in skill and "轻量项，非正式可选项" not in tools, "日常自动化仍被降级为非正式入口")
    require("内部安全门禁" in skill and "不新增第六个基础步骤" in skill, "内部安全基线与用户流程没有分层")
    require("用户没有选择就结束" in skill, "基础完成后仍可能强推可选能力")
    require("这一步解决什么" in chinese_ux and "Agent 会做什么" in chinese_ux and "用户只做什么" in chinese_ux, "中文引导缺少结果、Agent 分工和用户动作")
    require("Hermes 是连接各部分的“中控台”" in chinese_ux and "模型是负责理解和回答的“大脑”" in chinese_ux, "核心术语没有小白解释")
    require("能在微信里直接聊天的 AI 助手" in skill and "你不需要懂代码" in skill and "独立的微信机器人身份" in skill, "首次回应仍缺少结果、分工或身份说明")
    require("不要先问 `Profile`" in skill and "目标身份和只读授权分成两问" in chinese_ux, "已有助手能力升级仍可能先抛技术名或一次索要多个动作")
    require("`OBSIDIAN_VAULT_PATH` 只是寻址约定，不是权限边界" in tools, "Obsidian 路径变量仍被误写成权限沙箱")
    require("工作区外读取和写入都被拒绝" in tools, "Obsidian 可选项缺少越界负向测试")

    allowlist_sentence = "直达助手自动写入 allowlist，仅保留本次扫码主人"
    require(allowlist_sentence in skill, "个人助手默认私聊策略不是 allowlist")
    require("许可名单恰好一个主人" in skill and "home channel 与主人相同" in skill, "许可名单未限制为扫码主人")
    require("`Allow all direct messages` 表示任何人都能私聊使用，风险过高" in weixin, "直达流程没有明确拒绝开放私聊")
    require(not re.search(r"(?:默认|推荐|选择).{0,20}`?Allow all direct messages`?", skill), "SKILL.md 危险地推荐开放私聊")

    require("先停止本机 gateway 并确认已停止，再启动云端服务" in cloud, "云端迁移切换顺序缺失")
    require(not re.search(r"先(?:启动|打开)云端.{0,80}再(?:停止|关闭)本地", all_text, re.DOTALL), "发现先开云端后停本地的危险顺序")
    require(not re.search(r"(?:短暂|临时).{0,12}(?:双轮询|两个轮询|同时轮询)", all_text), "发现允许临时双轮询的危险表述")

    for command in contract["cloud_service"]["commands"].values():
        require(command in cloud, f"云端文档缺少用户服务命令：{command}")
    require("isolation_guard.py run-service" in faq and "gateway status --deep" in faq, "FAQ 云端状态命令未使用服务账号隔离通道")
    require("isolation_guard.py run-service" in faq and "gateway uninstall" in faq, "FAQ 云端卸载命令缺少已核验根与 Profile")
    require("ssh <用户名>@<IP> \"hermes gateway" not in faq, "FAQ 仍提供错误的远程用户服务命令")
    require("云端用户服务命令以" in cloud and "唯一事实来源" in cloud, "云端文档未声明命令唯一事实来源")

    require("三个新会话" in skill, "记忆验收没有使用三个新会话")
    require("从持久记忆移除 ≠ 从聊天历史、日志或模型提供商记录中抹除" in skill, "记忆删除披露不完整")
    require("同一会话完成记忆验收" not in all_text, "记忆验收危险地复用同一会话")

    for mechanism in ("Obsidian 路径", "Obsidian Sync", "Syncthing", "飞书 OAuth"):
        require(mechanism in tools, f"知识库撤权缺少 {mechanism}")
    require("撤权后的读取必须被拒绝" in tools, "知识库撤权缺少负向读取测试")

    require("只有一部手机且没有第二块可信屏幕" in cloud, "云端扫码未覆盖只有一部手机")
    require("购买服务器前停止" in cloud, "只有一部手机时没有在购买前停止")
    require("同手机打开短时 URL" in cloud and "未验证" in cloud, "错误宣称同手机短时 URL 可用")

    require("不承诺绝对零中断" in model, "模型切换仍承诺绝对零中断")
    require("隔离本地会话" in model and "恢复旧路由" in model, "模型切换缺少隔离验证或回退")
    require("降低因单一模型故障失联的概率" in skill, "fallback 话术仍过度保证")

    require("macOS 和 Windows 普通用户默认使用 Hermes Desktop" in skill, "主流程没有统一桌面安装入口")
    require("用户可能会问" in skill and "Agent 回答" in skill, "API key 示例角色仍不清楚")
    require("只自动读取 Hermes 自身可枚举的非秘密状态" in skill, "增量检测范围没有限制在 Hermes 内部")
    require("用户目录、外部账号或其他应用" in skill and "逐项取得授权" in skill, "外部检测缺少逐项授权")

    require("本 Skill 不再随包提供跨版本源码补丁" in skill, "当前发布仍可能引导小白修改 Hermes 源码")
    require("只覆盖 Hub 安装" in install, "本地 Skill 审计边界未说明")
    require("No hub-installed skills to audit" in install, "安装文档未说明本地审计的实际返回")
    require("只保留一个权威实体目录" in install, "安装文档没有定义单一实体源")
    require("目录链接" in install and "不退回复制" in install, "Codex/Hermes 未统一链接到权威目录")
    require("Get-Command hermes" in setup and "%LOCALAPPDATA%" in install, "原生 Windows 安装或发现路径不完整")
    require("New-Item -ItemType Junction" in install and "ln -s" in install, "单一源缺少跨平台可执行链接步骤")
    require("Vault 移动后的恢复" in install and "断链" in install, "单一源缺少断链自救入口")
    require("普通 ChatGPT" in install and "Codex 本地任务" in install, "ChatGPT 与 Codex 宿主范围不清")

    require("terminal.cwd` 只决定工具从哪里开始，不是沙箱" in security, "执行边界错误地把 cwd 当沙箱")
    require("hermes -p <Profile> tools list --platform weixin" in security, "执行边界未按 Profile 读取微信工具集")
    require("approvals.mode` 只能是 `manual` 或 `smart`" in security, "执行边界未禁止关闭审批")
    require("扫码前静态门禁" in security and "扫码后运行时门禁" in security, "执行边界没有按会话可用性分阶段")
    require("skills.write_approval" in security and "skill_manage" in security, "仅聊天档未阻止 Skill 持久写入")
    require("工具变化只对新会话可靠生效" in security, "工具权限变化后缺少新会话门槛")
    require("仍无真实 TTY 就不扫码" in weixin and "然后停止" in weixin, "无 TTY 分支没有安全停止")
    require("trusted_tty_required" in skill and "trusted_tty_required" in model and "trusted_tty_required" in weixin, "敏感交互的非 TTY 失败关闭未同步到主流程与逐屏指南")
    require("已有旧微信会话" in skill and "不能静默删除历史" in skill, "旧会话人格切换分支不完整")
    require("用户默认不接触终端" in skill and "你默认不用打开终端" in skill, "主流程没有对话优先承诺")
    require("launch_trusted_handoff.py" in skill and "launch_trusted_handoff.py" in model and "launch_trusted_handoff.py" in weixin, "模型与微信缺少 Agent 主动打开受信窗口")
    require("远端服务账号" in cloud and "root-runuser" in cloud, "云端交接器没有显式切换到服务账号")
    require("禁止运行通用 `model` 向导" in model and "未锁定 provider 时禁止启动通用 `model` 向导" in skill, "未阻止通用模型向导跳错厂商")
    require("一条已填好" in skill and "原子命令" in model and "原子命令" in weixin, "终端末级回退仍可能是多步操作")

    require("command -v systemctl loginctl curl git xz" in cloud, "云端预检缺少 systemd 工具或 xz")
    require("root 不要求安装 sudo" in cloud and "非 root 管理员" in cloud, "云端管理员身份分支缺失")
    require("systemctl is-system-running" in cloud and "/run/systemd/system" in cloud, "云端只检查 systemctl 命令存在")
    require("Playwright" in cloud and "--skip-browser" in cloud, "云端安装缺少 Playwright 分支")
    require("~/.local/bin" in cloud and "Hermes 绝对路径" in cloud, "云端安装缺少服务账号 PATH 验证")
    require("macOS 和 Windows 普通用户优先使用 Hermes Desktop" in setup, "安装参考的桌面入口不正确")
    require("uname -m" in setup and "git --version" in setup and "g++ --version" in setup, "macOS 缺少架构或真实依赖检查")
    require("sw_vers -productVersion" in setup and "macOS 12" in setup, "macOS 缺少最低系统版本检查")
    require("curl --version" in setup and "xz --version" in setup, "Linux 本地缺少 curl/xz 检查")
    require("hermes -p <Profile> gateway run" in skill and "--force --start-now --start-on-login" in acceptance, "本地服务状态机没有分开临时验收与持久启用")
    require("gateway stop" in acceptance and "仍只能登记“聊天可用”" in acceptance, "受保护前台重启与持久运行边界不清")
    require("收紧为 `0700`" in skill and "reharden_weixin_store" in direct_setup, "扫码助手缺少 Weixin 状态目录权限自动收紧")
    require("不回传 stdout" in skill and "不复制到聊天、工具输出或日志" in skill, "二维码显示通道未避开 Agent 捕获")
    require("连续进行 10 轮" in skill and "快速连续发送两条" in skill and "受控重启" in skill, "基础完成缺少稳定性和恢复验收")
    require("剪贴板历史" in model and "不能证明" in model, "API key 流程仍虚假承诺剪贴板安全")
    require("](官方直达地址)" not in model and "核验后的官方 HTTPS 链接" in model, "模型推荐模板仍包含不可点击的链接占位符")

    # 回归锁定：以下检查防止逐项审判发现的问题回潮
    require("报屏协议" in skill and "报屏协议" in weixin, "缺少无回传条件下的逐屏报屏陪同协议")
    require("Desktop 安装后必须由 Agent 在新进程环境验证" in skill and "Desktop 安装后必须由 Agent 在新进程环境验证" in setup, "Desktop 路线缺少 Agent 代执行的 CLI 入口验证")
    require("临时借用任一受信设备" in cloud, "单手机用户缺少云端扫码出路")
    require("也不通过普通截图分享" in weixin, "二维码保密表述存在语病或缺失")
    require("可中途暂停" in skill, "发布级验收缺少暂停与断点边界")
    require("○ 联网搜索 — 未配置（可选）" in skill, "可选能力不得用 ❌ 暗示缺陷")
    require("实际列出为准" in setup, "pairing 平台名必须以当前 --help 为准")
    require("`-p` 是隐藏全局参数" in setup, "能力闸未说明 -p 为隐藏参数")
    require("未核验不得作为首选" in model, "厂商 OAuth 未核验不得列为首选通道")
    require("投递到第 4 步设置的 home channel" in tools, "cron 投递目标未说明 home channel 默认行为")
    require("AI 日报同样不得只有单一来源" in tools, "AI 日报缺少备选来源对比")
    require("先解释，再选择，再安装" in skill and "成功后看到什么" in skill, "主流程缺少小白步骤解释与成功标志")
    require("不得把用户复述问题当作授权" in chinese_ux and "知识库渠道尚未选择时不得先问是否安装 Docker" in chinese_ux, "中文交互未阻止把不懂或复述当授权及过早安装依赖")
    require("A. 已经在用 Obsidian" in tools and "B. 已经在用飞书云文档" in tools and "D. 目前没有知识库" in tools and "E. 不确定或使用其他工具" in tools, "知识库入口没有覆盖已有、没有和不确定")
    require("Docker 是本地目录的可选硬隔离层，不是知识库" in tools and "不得在渠道选择前把 Docker 当成前置问题" in tools, "知识库流程仍可能把 Docker 当作首问或知识库")
    require("只读 scope 不是单文档白名单" in tools and "在任何飞书请求发出前拒绝其他 URL、token 和文档 ID" in tools, "飞书知识库缺少单文档资源边界")
    require("首次把私有文档交给外部模型前" in tools and "用户只同意飞书授权时，不得把它推定为同意发送给模型" in tools, "知识库外部模型传输缺少单独披露与同意")
    require("用户只负责登录、扫码、授权确认" in tools and "不得把创建应用、寻找权限和复制配置步骤重新甩给小白" in tools, "飞书应用配置仍可能甩给小白")
    require("未授权文档链接在联网前被拒绝" in tools and "不得为了越权测试真实读取另一篇用户没有明确授权的文档" in tools, "飞书知识库缺少安全负向测试")
    require("Agent 必须在可信 TTY 中代用户处理这个交互" in tools and "名称、数量或参数与本轮选择不一致就取消" in tools, "MCP 接入缺少可信 TTY 与精确工具清单门禁")
    require("即使确认内容是空对象也表示这次已经批准" in tools and "不能误报“确认通道没有返回”" in tools, "Hermes 空确认内容兼容说明缺失")
    require("A. 已经在用 Codex" in tools and "D. 电脑里有代码，但不知道这些工具是什么" in tools and "E. 还没有编程工具或代码项目，只想先体验" in tools, "编程入口没有覆盖已有工具、无工具和无项目")
    require("创建一个隔离演示项目" in tools and "用户选定渠道并授权后，才检测对应的" in tools, "无编程项目路线或选择前禁止检测未锁定")
    require("codex login status" in tools and "codex login --device-auth" in tools and "codex login --with-api-key" in tools and "不得在聊天里索要或让用户粘贴秘密" in tools, "Codex 登录失效缺少小白安全恢复")
    require("Codex 桌面应用显示已登录，不等于 Codex CLI 已登录" in tools and "不得覆盖或改写这份全局配置" in tools, "Codex 登录说明没有区分桌面状态与全局自定义配置")
    require("--codex-home <专用登录目录绝对路径>" in tools and "0700" in tools, "Codex MCP 没有显式绑定私有专用登录目录")
    require("npm 版 Codex" in tools and "--node <Node.js真实绝对路径>" in tools and "隔离 Gateway 不继承用户 PATH" in tools, "npm Codex 没有显式绑定 Node.js 运行时")
    require("mcp_servers.<名称>.timeout=1830" in tools and "执行器自身 `--timeout 1800`" in tools, "Codex MCP 超时没有覆盖真实长任务")
    require("mcp_servers.<名称>.keepalive_interval=2000" in tools and "探活" in tools, "Codex MCP 缺少 keepalive_interval 大于工具超时的要求，Hermes 默认 180 秒探活会强杀长任务")
    require("只显示白名单内的缺失 scope 名称" in tools and "不回传飞书 CLI 原始失败输出" in tools, "飞书运行错误恢复可能泄露原始输出")
    require("Node.js 真实可执行文件" in tools and "任意可执行文件冒充 Node.js" in tools and "不能依赖 Agent 或隔离 Gateway 继承到的 `PATH`" in tools and "--node <Node.js真实绝对路径>" in tools, "飞书隔离运行时缺少显式 Node.js 路径或身份核验")
    require("登录或额度在任务期间失效" in tools and "不回传 Codex 原始输出" in tools, "Codex 运行错误缺少安全恢复说明")
    require("每一次 `prepare_code_change` 前" in tools and "状态无法核验就不创建 worktree" in tools, "Codex 登录检查没有覆盖运行期失效")
    require("codex --sandbox workspace-write --ask-for-approval never -c sandbox_workspace_write.network_access=false exec --ephemeral --ignore-user-config" in tools, "Codex 文档没有使用当前 CLI 的全局参数顺序")
    require("codex exec --ephemeral --sandbox workspace-write" not in tools, "Codex 文档仍保留会在当前 CLI 解析失败的旧参数顺序")
    require("第一问只选内容，不先问城市、时间、频率" in tools and "任何任务都先手动触发并由微信真实收到" in tools, "日常自动化仍可能先问参数或未手动送达就启用")

    # 回归锁定：第二轮对抗性自测发现的问题
    require("扫码后启动前门禁" in security, "缺少扫码后启动前的中间门禁")
    require("scripts/check_profile_safety.py --profile <Profile>" in security, "访问控制缺少无泄密跨平台检查器")
    require("config get` 读不到" in security, "未说明 .env 变量无法通过 config get 读取")
    require("date +%Z" in cloud, "云端预检缺少时区核验")
    require("auth add <provider> --type oauth" in skill and "禁止启动通用 `model` 向导" in skill, "第三步未锁定 provider 明确认证入口")
    require("target_is_only_gateway_for_service_user" in cloud, "单实例轮询缺少同服务账号可执行门禁")
    require("默认按本地路线先体验" in skill, "第一步缺少默认出口")
    require("写入当前 Profile 的 `.env`" in tools, "OBSIDIAN_VAULT_PATH 未说明写入机制")
    require("聊天可用" in skill and "持久运行已验证" in skill and "开机恢复已验证" in skill, "验收状态归属不精确")
    require("真实重启电脑/服务器" in skill, "真实重启验收未提前预告")
    require("网关日志会记录聊天内容明文" in chinese_ux, "未披露网关日志留存聊天明文")

    # 回归锁定：真人测试发现的问题
    require("名称不是 `default`" in skill and "全部 Hermes 命令都由同脚本的 `run`" in skill, "隔离验收未阻止写入 default Profile")
    unscoped_profile_command = re.compile(
        r"(?m)\bhermes (?:(?:doctor|status|logs|model|fallback|auth|tools|skills|mcp|cron|pairing)\b|config (?:get|set|path|env-path)\b|gateway (?:setup|run|install|start|stop|restart|status|uninstall)\b)"
    )
    for relative, document in documents.items():
        require(not unscoped_profile_command.search(document), f"{relative} 存在未绑定 Profile 的 Profile 相关命令")
        require("hermes -p <Profile> profile show" not in document, f"{relative} 使用了错误的 profile show 参数位置")
    require("display.platforms.weixin.show_reasoning false" in security, "Weixin 未明确关闭推理展示")
    require("memory.memory_enabled false" in security and "memory.user_profile_enabled false" in security, "未选择记忆时缺少注入关闭门禁")
    require("仅禁用 `memory` 工具集不会阻止" in skill, "未区分记忆工具权限与系统提示注入")
    require("主动状态陪同" in weixin and "报屏协议只作备用" in weixin, "微信连接仍把识别进度责任推给用户")
    require("已有 `WEIXIN_*` 标记只代表上一次配置" in weixin and "本轮启动后的新鲜状态变化" in weixin, "主动轮询仍会把旧配置误判为本轮完成")
    require("无法访问同一 Profile" in weixin and "报屏协议" in weixin, "无法观察状态时缺少安全陪同回退")
    require("Select a platform to configure:" in weixin and "不是本 Skill 的 V0.4 直达路线" in weixin, "缺少误入全平台菜单的安全恢复")
    require("用户不要滚动、不要按方向键、不要选 `Done`" in weixin, "仍可能让用户操作全平台菜单")
    require("微信连接成功" in weixin and "不表示第 4 步完成" in weixin, "未提醒扫码成功后仍需自动检查")
    require("五步全览" in skill and "▶ 第3步" in skill, "进度规范缺少完整五步进度条")
    require("不得提模型名、测试日期" in skill and "不得主动罗列现在不能做的功能" in skill, "自我介绍仍可能泄露内部验收话术")

    # 回归锁定：反抗式全量审查发现的问题
    require("hermes profile create wechatassistant --no-alias --no-skills" in skill and "不得使用 `--clone`" in skill, "普通新用户缺少隔离 Profile 生命周期")
    require("命名 Profile 单独使用仍不保证隔离模型凭据" in skill, "仍把命名 Profile 误当凭据隔离")
    require("全局共享" in model and "进程环境" in model and "共享 OAuth" in model, "模型认证缺少来源分类")
    require("第一问只有一个决定" in cloud and "已有服务器直接进入第 2 节" in cloud, "云端价格比较仍早于需求判断")
    require("不需要 webhook" in cloud and "不得直接暴露公网" in cloud, "云端缺少长轮询与 Dashboard 网络边界")
    require("备份与快照" in security and "轮换与撤销" in security, "token 缺少完整生命周期")
    require("外部接收者安装" in install and "不需要拥有作者的 Obsidian" in install, "Skill 安装仍只适用于维护者")
    require("当前没有启用实时查询" in documents["assets/SOUL.zh-CN.md"], "仅聊天档仍被迫猜测实时信息")
    require("模拟状态不能满足" in skill and "真实重启电脑/服务器" in skill, "模拟证据仍可能冒充真实恢复")
    require("`Approve Once`" in setup and "`Always Approve`" in setup and "`Cancel`" in setup, "新会话英文确认缺少逐项中文解释")
    require("旧确认立即失效" in setup and "`Gateway shutting down`" in setup, "网关重启后的新会话确认恢复说明不完整")
    require("service_account_user_with_linger" == contract["cloud_service"]["scope"], "云端服务身份模型回退")
    require("SHA-256" in cloud and "check_cloud_preflight.py" in cloud, "云端缺少远端发布哈希或预检脚本")
    require("图片、语音、视频和文件" in skill and "weixin/accounts" in security, "微信媒体与凭据缓存边界未披露")
    require("`{{...}}` 为零" in skill, "SOUL 写入缺少占位符清零门禁")

    # 回归锁定：官方 Hermes v0.20.0 隔离实测发现的问题
    require("hermes profile show <Profile>" in skill and "hermes profile show <Profile>" in setup, "Profile show 仍未使用位置参数")
    require("check_hermes_cli_contract.py" in skill and "官方干净 tag" in skill, "缺少当前 Hermes 干净根 CLI 能力闸")
    require("check_pre_qr_safety.py" in skill and "check_pre_qr_safety.py" in security, "扫码前缺少可执行安全门禁")
    require("精确只启用 `clarify`" in security, "仅聊天档仍可能保留默认开启工具")
    require("没有任何 MCP 服务器" in security, "仅聊天档未阻止 MCP 默认跨平台继承")
    require("macOS" in setup and "会立即加载" in setup and "两个启动参数" in setup, "macOS launchd 参数语义未披露")
    require("解析目录精确为已批准根下的 `profiles/<Profile>`" in security and "旧 `WEIXIN_*` 状态" in security, "扫码前未披露路径错绑或旧微信状态阻断")
    require("--hermes <官方干净Hermes启动器绝对路径>" in install, "发布验证未强制官方干净 Hermes 路径")
    require("skip 数精确为零" in install and "源码事实 30/30" in install, "发布验证仍可能隐藏真实测试 skip")
    require("测试资源台账" in cloud and "无 glob" in cloud and "既有生产助手" in faq, "共享服务器测试缺少精确资源清理闭环")
    require("--hermes <HERMES绝对路径>" in cloud and "proc/<MainPID>/cmdline" in cloud, "systemd 门禁未绑定实际启动 Profile")
    require("--profile <Profile> gateway run" in cloud and "不得机械替换成" in cloud, "systemd ExecStart 参数仍与官方生成格式不一致")
    require("不连接任何真实账号" in skill and "模型真实认证前必须停止" in skill, "无真实账号 dry-run 边界不明确")
    require("check-prerestart" in cloud and "--expect-enabled enabled|disabled" in cloud, "systemd active/enabled 分阶段门禁不完整")
    require("macOS launchd" in skill and "等价自动检查器" in skill and "只保留“聊天可用”" in skill, "本地服务绑定证据仍被过度声明")
    require("isolation_guard.py create-root" in skill and "不得运行真实根的 `profile list` 或 `gateway list`" in skill, "受保护助手场景仍可能读取真实 Hermes 根")
    require("resource_ledger_guard.py" in cloud and "preview-cleanup" in cloud and "verify-cleanup" in cloud and "只预览且没有删除原语" in cloud, "共享服务器清理缺少机器预览与核验")
    require("isolation_guard.py run-service" in cloud and "--expected-hermes-root <本轮专用Hermes根>" in cloud, "云端服务管理未绑定真实 HOME 与专用 Hermes 根")

    # 回归锁定：对话直达微信、凭据顺序与向导默认动作
    require("任何本机 Hermes 或 gateway 读取之前" in skill and "目标不确定也进入受保护验收" in skill, "操作模式没有先于本机 Hermes 检测")
    require("配置存在只证明已配置" in skill and "真实中文回复" in skill, "配置状态仍可能冒充模型真实可用")
    require("模型探测可能新建权限过宽的 Profile 缓存" in skill and "模型探测成功后再运行一次 `apply_chat_safety_baseline.py`" in model, "模型探测后的运行时缓存权限未重新收紧")
    require("不得直接运行并捕获 `hermes -p <Profile> auth list`" in model, "模型凭据分类仍会把 auth list 捕获到 Agent 日志")
    require("任何远程或可能计费的模型调用之前" in model, "凭据来源没有先于远程模型探测")
    require("Hermes Agent v0.20.0" in weixin and "Hermes Agent v0.19.1" not in weixin, "Weixin 向导参考版本必须是 v0.20.0")
    require("不得调用通用 `gateway setup`" in skill and "不显示平台、私聊、群聊" in skill, "微信流程仍可能进入通用菜单")
    require("Windows 用户不需要安装 Windows Terminal" in weixin and "系统自带的新控制台窗口" in weixin, "Windows 仍依赖用户安装或操作第三方终端")
    require("用户只扫码并在手机确认" in skill and "用户不选择私聊或群聊权限" in skill, "微信流程仍要求用户终端选择")
    require("setup_weixin_direct.py" in skill and "setup_weixin_direct.py" in weixin, "文档没有绑定微信直达助手")
    require("--purpose local-test" in skill and "--purpose cloud-service" in cloud, "隔离根没有显式绑定 local-test 或 cloud-service 用途")
    require("首次台账门禁通过前不把引导脚本落盘" in cloud and "不能提供不落盘执行通道时停止" in cloud, "云端资源台账存在未跟踪的引导脚本循环")
    require("不得把含 `<...>` 的占位命令交给用户" in skill, "仍可能让小白手工替换命令占位符")
    require("auth add <provider> --type oauth" in skill and "run-cloud" in skill, "云端交互式模型认证未绑定明确 provider 与 run-cloud")
    require("trusted_tty_required" in cloud and "机械拒绝非 TTY" in cloud, "云端模型或扫码未机械拒绝输出捕获环境")
    require("用户不输入终端命令" in model and "用户不输入终端或 SSH 命令" in weixin, "模型或微信仍把终端启动责任推给用户")
    require("下文命令全部由 Agent 或发布维护者执行" in install and "不要求普通用户打开终端" in install, "Skill 安装仍把终端操作推给普通用户")
    require("云端的只读检查、安装、SSH、文件传输、配置、服务启停和验证全部由 Agent 执行" in cloud and "用户不输入 SSH 或服务器命令" in cloud, "云端部署仍把 SSH 操作推给用户")
    require("本文件中的命令与检查项全部由 Agent" in setup and "不要求普通用户打开终端" in setup, "Hermes 安装仍把终端操作推给用户")
    require("Qwen `~/.qwen`" in cloud and "最终 systemd gateway" in cloud, "云端 HOME 级 OAuth 与运行时一致性未披露")

    # 回归锁定：云端 API key 原生隐藏输入与 macOS 可编辑输入栏
    require("观察到 Hermes 已进入掩码提示后" in skill and "用户只粘贴一次 Key" in skill, "主流程仍可能在掩码前取 Key 或要求第二次输入")
    require("禁止先从弹窗取 Key 再把它管道到尚未进入掩码状态的 SSH" in model, "模型文档未阻止早发 Key 回显")
    require("不输入 SSH、路径、启动命令或标签" in cloud and "存在保存回执时返回 `SAVED`" in cloud, "云端模型认证仍把标签或保存判断推给用户")
    require("macOS 固定使用系统原生密码框" in model and "禁止使用 Tkinter" in model, "模型文档未锁定 macOS 可编辑密码框")
    require("本地伪终端" in model and "本地普通或受保护模式" in skill, "本地 API key 原生隐藏输入未绑定三路 TTY")
    return failures

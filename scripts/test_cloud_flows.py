#!/usr/bin/env python3
from __future__ import annotations

import copy
import ipaddress
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "assets" / "cloud-flow-fixtures.json"
PURCHASE_STEP_IDS = (
    "comparison_with_inline_links",
    "quote_checked",
    "user_confirms_external_purchase",
    "instance_ready",
)
DEPLOYMENT_STEP_IDS = (
    "readonly_server_inspection",
    "non_root_user_created",
    "hermes_installed_and_checked",
    "chat_safety_baseline_applied",
    "model_authenticated_safely",
    "model_probe_after_safety_recheck",
    "post_model_probe_permissions_rehardened",
    "weixin_authenticated_by_qr",
    "user_service_staged_without_start",
    "controlled_cutover_started",
    "gateway_deep_status_passed",
    "weixin_roundtrip_passed",
    "reboot_recovery_passed",
    "cutover_finalized",
    "cost_and_pause_explained",
)
FRESH_CLOUD_DEPLOYMENT_STEP_IDS = (
    "readonly_server_inspection",
    "non_root_user_created",
    "hermes_installed_and_checked",
    "chat_safety_baseline_applied",
    "model_authenticated_safely",
    "model_probe_after_safety_recheck",
    "post_model_probe_permissions_rehardened",
    "weixin_authenticated_by_qr",
    "user_service_staged_without_start",
    "gateway_deep_status_passed",
    "weixin_roundtrip_passed",
    "reboot_recovery_passed",
    "cost_and_pause_explained",
)
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
DEPLOYMENT_TRACE_IDS = (
    "simulated_admin_readonly_preflight",
    "simulated_service_account_created",
    "simulated_linger_enabled",
    "simulated_release_uploaded_and_hash_verified",
    "simulated_release_verified_and_extracted_without_overwrite",
    "simulated_model_host_selected",
    "simulated_service_account_preflight_passed",
    "simulated_systemd_manager_env_clean",
    "simulated_systemd_runtime_verified",
    "simulated_profile_created",
    "simulated_chat_safety_baseline_applied",
    "simulated_pre_model_safety_gate_passed",
    "simulated_model_authenticated",
    "simulated_post_model_safety_gate_passed",
    "simulated_model_probe_passed",
    "simulated_post_model_probe_permissions_rehardened",
    "simulated_profile_bound_to_model",
    "simulated_profile_bound_to_weixin",
    "simulated_profile_bound_to_user_unit",
    "simulated_user_service_staged_stopped_disabled",
    "simulated_systemd_env_guard_installed",
    "simulated_systemd_prestart_env_verified",
    "simulated_soul_written",
    "simulated_service_started_for_acceptance",
    "simulated_systemd_runtime_env_verified",
    "simulated_runtime_boundary_passed",
    "simulated_quality_acceptance_passed",
    "simulated_service_stopped_before_persist",
    "simulated_persistence_unit_rewritten_stopped",
    "simulated_systemd_prestart_after_persist",
    "simulated_persistence_started_explicitly",
    "simulated_persistence_enabled",
    "simulated_reboot_transition",
    "simulated_deep_status_after_reboot",
    "simulated_systemd_runtime_env_after_reboot",
    "simulated_weixin_task_after_reboot",
)
OFFICIAL_URLS = {
    "aliyun": {
        "official_intro_url": "https://www.aliyun.com/product/swas",
        "official_price_url": "https://help.aliyun.com/zh/simple-application-server/product-overview/billable-items",
        "official_purchase_url": "https://www.aliyun.com/product/swas",
    },
    "tencent": {
        "official_intro_url": "https://cloud.tencent.com/product/lighthouse",
        "official_price_url": "https://cloud.tencent.com/document/product/1207/73452",
        "official_purchase_url": "https://buy.cloud.tencent.com/lighthouse",
    },
    "huawei": {
        "official_intro_url": "https://www.huaweicloud.com/product/flexus-l.html",
        "official_price_url": "https://www.huaweicloud.com/product/flexus/pricing.html",
        "official_purchase_url": "https://www.huaweicloud.com/product/flexus-l.html",
    },
}


class FlowRejected(ValueError):
    pass


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FlowRejected(message)


def validate_steps(steps: list[dict], expected_ids: tuple[str, ...], label: str) -> None:
    ids = tuple(step.get("id") for step in steps)
    require(ids == expected_ids, f"{label}步骤缺失、重复或顺序错误")
    for step in steps:
        require(bool(step.get("action")), f"{label}步骤缺少用户动作")
        require(bool(step.get("expected")), f"{label}步骤缺少可观察结果")


def validate_mock_ip(value: str) -> None:
    address = ipaddress.ip_address(value)
    require(
        any(address in network for network in DOCUMENTATION_NETWORKS),
        "模拟测试只能使用 RFC 5737 文档保留地址",
    )


def validate_deployment_trace(fixture: dict) -> None:
    trace = tuple(fixture.get("deployment_trace", []))
    require(trace == DEPLOYMENT_TRACE_IDS, "部署证据缺失、重复或因果顺序错误")


def validate_safe_state(state: dict, fixture: dict) -> None:
    require(state.get("real_purchase_performed") is False, "禁止在模拟测试中真实购买")
    require(state.get("real_credentials_used") is False, "禁止在模拟测试中使用真实凭据")
    require(state.get("secret_exposed") is False, "检测到密钥泄露")
    require(state.get("service_user_is_root") is False, "Hermes 服务不能使用 root 运行")
    require(state.get("duplicate_weixin_poller") is False, "同一微信 token 不能重复轮询")
    require(state.get("server_reachable") is True, "已有服务器暂时无法连接，不能继续部署或自动购买")
    require(state.get("supported_linux") is True, "服务器系统不在支持范围")
    require(state.get("cloud_credential_is_separate") is True, "云端必须使用独立、可撤销的模型凭据")
    validate_deployment_trace(fixture)


def validate_fresh_state(state: dict, fixture: dict) -> None:
    validate_safe_state(state, fixture)
    require(state.get("local_gateway_exists") is False, "全新云端流程不得虚构本地 gateway")


def validate_migration_state(state: dict, fixture: dict) -> None:
    validate_safe_state(state, fixture)
    require(state.get("cloud_prepared_before_cutover") is True, "云端未准备完成，不能切换")
    require(state.get("local_stopped_at_controlled_cutover") is True, "必须在受控切换点停止本地网关")
    require(state.get("rollback_restores_local") is True, "云端失败时必须能恢复本地助手")


def validate_initial_route(fixture: dict) -> None:
    route = fixture.get("initial_route", {})
    require(route.get("decision_before_hermes_install") is True, "必须在安装 Hermes 前选择本地或云端")
    require(route.get("local_route_configuration_count") == 1, "本地路线只能配置一次")
    require(route.get("cloud_route_configuration_count") == 1, "云端路线只能配置一次")
    require(route.get("cloud_route_installs_local_hermes") is False, "云端路线不得先在本地安装 Hermes")
    require(route.get("later_migration_is_separate_flow") is True, "已有助手迁移必须与新用户流程分开")


def simulate_provider_purchase(provider: dict, fixture: dict, state: dict | None = None) -> dict:
    require(fixture.get("simulation_only") is True, "测试夹具必须明确标记为纯模拟")
    validate_initial_route(fixture)
    for key in ("id", "name", "official_intro_url", "official_price_url", "official_purchase_url", "mock_order_id"):
        require(bool(provider.get(key)), f"云厂商缺少字段：{key}")
    require(provider["official_intro_url"].startswith("https://"), "官方介绍地址无效")
    require(provider["official_price_url"].startswith("https://"), "官方价格地址无效")
    official_urls = OFFICIAL_URLS.get(provider["id"], {})
    for key in ("official_intro_url", "official_price_url", "official_purchase_url"):
        parsed = urlparse(provider[key])
        require(parsed.scheme == "https" and not parsed.username and not parsed.password, f"{key} 不是安全官方地址")
        require(
            provider[key].rstrip("/") == official_urls.get(key, "").rstrip("/"),
            f"{key} 不是当前已核验官方地址",
        )
    quote = provider.get("mock_quote", {})
    for key in ("currency", "amount", "unit", "region", "spec", "image", "architecture", "disk_gb", "term", "eligibility", "promo_eligibility", "renewal", "refund", "outbound_https", "public_management_method", "new_public_inbound_port", "verified_at"):
        require(quote.get(key) not in (None, ""), f"模拟报价缺少字段：{key}")
    require(quote.get("is_mock") is True, "模拟报价必须醒目标记为假数据")
    require(isinstance(quote["amount"], (int, float)) and quote["amount"] >= 0, "模拟价格无效")
    require(quote["outbound_https"] is True and quote["new_public_inbound_port"] is False, "订单网络边界不安全")
    require(provider["mock_order_id"].startswith("MOCK-"), "模拟订单号必须使用 MOCK 前缀")
    validate_mock_ip(provider["mock_ip"])
    validate_steps(fixture["purchase_steps"], PURCHASE_STEP_IDS, "购买")
    validate_steps(fixture["fresh_cloud_deployment_steps"], FRESH_CLOUD_DEPLOYMENT_STEP_IDS, "全新云端部署")
    validate_fresh_state(state or fixture["fresh_safe_state"], fixture)
    return {"provider": provider["name"], "status": "模拟购买与部署流程通过"}


def simulate_existing_server(fixture: dict, state: dict | None = None) -> dict:
    validate_initial_route(fixture)
    server = fixture["existing_server"]
    entry = fixture["existing_server_entry"]
    connection = fixture["existing_server_connection_requirements"]
    require(server.get("provider_required") is False, "已有服务器流程不能强制要求云厂商名称")
    require(server.get("purchase_skipped") is True, "已有服务器必须跳过购买")
    require(entry.get("server_install_required") is False, "已有服务器不应被描述为需要安装服务器")
    require(entry.get("hermes_deployment_on_existing_server") is True, "必须说明是在现有服务器上部署 Hermes")
    require(entry.get("purchase_skipped_message") is True, "第一条回复必须说明已跳过购买")
    require(entry.get("connection_detected_before_question") is True, "必须先检测现有安全连接再提问")
    require(entry.get("os_asked_before_connection") is False, "连接前不得先让用户回答系统版本")
    require(entry.get("provider_asked_before_connection") is False, "连接前不得强制询问云厂商")
    require(entry.get("secret_requested_in_chat") is False, "不得在聊天中索要服务器秘密")
    for key in ("endpoint", "port", "username", "auth_method", "secret_input_channel"):
        require(connection.get(key) not in (None, ""), f"服务器连接资料缺少字段：{key}")
    validate_mock_ip(connection["endpoint"])
    require(connection.get("os_detected_after_connection") is True, "系统版本必须在连接成功后自动检测")
    require(connection["secret_input_channel"] in ("ssh_agent_or_hidden_prompt", "provider_web_terminal"), "服务器秘密输入通道不安全")
    require(
        all(server.get(key) for key in ("os", "cpu", "memory_gb", "disk_gb")),
        "已有服务器检测信息不完整",
    )
    validate_mock_ip(server["mock_ip"])
    access_options = fixture.get("existing_server_access_options", [])
    require(
        tuple(option.get("id") for option in access_options)
        == ("existing_ssh_alias", "provider_web_terminal", "not_sure"),
        "已有服务器缺少安全连接入口",
    )
    for option in access_options:
        require(option.get("label") and option.get("expected"), "安全连接入口说明不完整")
    validate_steps(fixture["fresh_cloud_deployment_steps"], FRESH_CLOUD_DEPLOYMENT_STEP_IDS, "全新云端部署")
    validate_fresh_state(state or fixture["fresh_safe_state"], fixture)
    return {"provider": server["name"], "status": "跳过购买并完成模拟部署"}


def simulate_later_migration(fixture: dict, state: dict | None = None) -> dict:
    validate_initial_route(fixture)
    validate_steps(fixture["deployment_steps"], DEPLOYMENT_STEP_IDS, "已有助手迁移")
    validate_migration_state(state or fixture["migration_safe_state"], fixture)
    return {"status": "已有本地助手按独立迁移流程模拟通过"}


def simulate_purchase_cancelled(provider: dict, fixture: dict) -> dict:
    require(fixture.get("simulation_only") is True, "测试夹具必须明确标记为纯模拟")
    require(provider.get("official_price_url", "").startswith("https://"), "官方价格地址无效")
    validate_fresh_state(fixture["fresh_safe_state"], fixture)
    return {
        "provider": provider["name"],
        "status": "用户取消购买，流程安全暂停",
        "mock_order_created": False,
        "local_gateway_exists": False,
    }


class CloudFlowSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture()

    def test_01_all_provider_purchase_and_deployment_flows(self) -> None:
        """阿里云、腾讯云、华为云的模拟购买与部署流程完整"""
        self.assertEqual(len(self.fixture["providers"]), 3)
        for provider in self.fixture["providers"]:
            with self.subTest(provider=provider["name"]):
                self.assertIn("通过", simulate_provider_purchase(provider, self.fixture)["status"])

    def test_02_existing_server_skips_purchase(self) -> None:
        """用户已有服务器时跳过购买并直接进入只读检测与部署"""
        self.assertIn("跳过购买", simulate_existing_server(self.fixture)["status"])

    def test_03_missing_inline_official_link_is_rejected(self) -> None:
        """缺少表格行内官方地址时必须阻断"""
        provider = copy.deepcopy(self.fixture["providers"][0])
        provider["official_price_url"] = ""
        with self.assertRaisesRegex(FlowRejected, "official_price_url"):
            simulate_provider_purchase(provider, self.fixture)

    def test_04_incomplete_quote_is_rejected(self) -> None:
        """地区、配置、资格或续费信息不完整时必须阻断"""
        provider = copy.deepcopy(self.fixture["providers"][1])
        provider["mock_quote"]["renewal"] = ""
        with self.assertRaisesRegex(FlowRejected, "renewal"):
            simulate_provider_purchase(provider, self.fixture)

    def test_05_real_purchase_is_rejected(self) -> None:
        """模拟测试意外触发真实购买时必须阻断"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["real_purchase_performed"] = True
        with self.assertRaisesRegex(FlowRejected, "真实购买"):
            simulate_provider_purchase(self.fixture["providers"][0], self.fixture, state)

    def test_06_real_credentials_are_rejected(self) -> None:
        """模拟测试使用真实凭据时必须阻断"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["real_credentials_used"] = True
        with self.assertRaisesRegex(FlowRejected, "真实凭据"):
            simulate_provider_purchase(self.fixture["providers"][0], self.fixture, state)

    def test_07_secret_exposure_is_rejected(self) -> None:
        """密钥进入聊天、命令或日志时必须阻断"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["secret_exposed"] = True
        with self.assertRaisesRegex(FlowRejected, "密钥泄露"):
            simulate_existing_server(self.fixture, state)

    def test_08_root_service_is_rejected(self) -> None:
        """使用 root 运行 Hermes 服务时必须阻断"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["service_user_is_root"] = True
        with self.assertRaisesRegex(FlowRejected, "root"):
            simulate_existing_server(self.fixture, state)

    def test_09_premature_local_shutdown_is_rejected(self) -> None:
        """云端尚未准备完成就停止本地助手时必须阻断"""
        state = copy.deepcopy(self.fixture["migration_safe_state"])
        state["cloud_prepared_before_cutover"] = False
        with self.assertRaisesRegex(FlowRejected, "不能切换"):
            simulate_later_migration(self.fixture, state)

    def test_10_duplicate_weixin_poller_is_rejected(self) -> None:
        """本地与云端同时轮询同一微信 token 时必须阻断"""
        state = copy.deepcopy(self.fixture["migration_safe_state"])
        state["duplicate_weixin_poller"] = True
        with self.assertRaisesRegex(FlowRejected, "不能重复轮询"):
            simulate_later_migration(self.fixture, state)

    def test_11_reboot_failure_is_rejected(self) -> None:
        """云服务器重启后服务未恢复时不得宣称完成"""
        fixture = copy.deepcopy(self.fixture)
        fixture["deployment_trace"].remove("simulated_weixin_task_after_reboot")
        with self.assertRaisesRegex(FlowRejected, "部署证据"):
            simulate_existing_server(fixture)

    def test_12_unsupported_os_is_rejected(self) -> None:
        """服务器系统不受支持时必须停止部署"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["supported_linux"] = False
        with self.assertRaisesRegex(FlowRejected, "不在支持范围"):
            simulate_existing_server(self.fixture, state)

    def test_13_purchase_cancellation_is_safe(self) -> None:
        """全新云端用户取消购买时不创建订单，也不虚构本地 gateway"""
        result = simulate_purchase_cancelled(self.fixture["providers"][0], self.fixture)
        self.assertFalse(result["mock_order_created"])
        self.assertFalse(result["local_gateway_exists"])
        self.assertIn("安全暂停", result["status"])

    def test_14_unreachable_existing_server_is_rejected(self) -> None:
        """已有服务器无法连接时停止部署且不自动购买新服务器"""
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["server_reachable"] = False
        with self.assertRaisesRegex(FlowRejected, "不能继续部署或自动购买"):
            simulate_existing_server(self.fixture, state)

    def test_15_missing_rollback_is_rejected(self) -> None:
        """云端失败后无法恢复本地助手时不得进入切换"""
        state = copy.deepcopy(self.fixture["migration_safe_state"])
        state["rollback_restores_local"] = False
        with self.assertRaisesRegex(FlowRejected, "必须能恢复本地助手"):
            simulate_later_migration(self.fixture, state)

    def test_16_existing_server_entry_is_not_server_installation(self) -> None:
        """已有服务器入口必须先解释部署概念并检测安全连接"""
        result = simulate_existing_server(self.fixture)
        self.assertIn("跳过购买", result["status"])

    def test_17_missing_server_endpoint_is_rejected(self) -> None:
        """没有服务器地址、SSH 别名或网页终端时不能假装已连接"""
        fixture = copy.deepcopy(self.fixture)
        fixture["existing_server_connection_requirements"]["endpoint"] = ""
        with self.assertRaisesRegex(FlowRejected, "endpoint"):
            simulate_existing_server(fixture)

    def test_18_chat_password_request_is_rejected(self) -> None:
        """任何要求用户在聊天中提供服务器密码的流程都必须阻断"""
        fixture = copy.deepcopy(self.fixture)
        fixture["existing_server_entry"]["secret_requested_in_chat"] = True
        with self.assertRaisesRegex(FlowRejected, "不得在聊天中索要服务器秘密"):
            simulate_existing_server(fixture)

    def test_19_route_is_selected_before_install(self) -> None:
        """新用户必须先选本地或云端，且只在目标环境配置一次"""
        validate_initial_route(self.fixture)
        self.assertIn("迁移流程", simulate_later_migration(self.fixture)["status"])

    def test_20_local_first_cloud_later_flow_is_rejected(self) -> None:
        """云端路线若先在本地安装 Hermes 必须阻断"""
        fixture = copy.deepcopy(self.fixture)
        fixture["initial_route"]["cloud_route_installs_local_hermes"] = True
        with self.assertRaisesRegex(FlowRejected, "不得先在本地安装 Hermes"):
            simulate_existing_server(fixture)

    def test_21_missing_profile_binding_evidence_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.fixture)
        fixture["deployment_trace"].remove("simulated_profile_bound_to_user_unit")
        with self.assertRaisesRegex(FlowRejected, "部署证据"):
            simulate_existing_server(fixture)

    def test_22_persistence_before_acceptance_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.fixture)
        trace = fixture["deployment_trace"]
        trace.remove("simulated_persistence_enabled")
        trace.insert(trace.index("simulated_runtime_boundary_passed"), "simulated_persistence_enabled")
        with self.assertRaisesRegex(FlowRejected, "因果顺序"):
            simulate_existing_server(fixture)

    def test_23_command_exists_without_systemd_runtime_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.fixture)
        fixture["deployment_trace"].remove("simulated_systemd_runtime_verified")
        with self.assertRaisesRegex(FlowRejected, "部署证据"):
            simulate_existing_server(fixture)

    def test_24_official_url_on_unrelated_domain_is_rejected(self) -> None:
        provider = copy.deepcopy(self.fixture["providers"][1])
        provider["official_purchase_url"] = "https://example.com/lighthouse"
        with self.assertRaisesRegex(FlowRejected, "官方地址"):
            simulate_provider_purchase(provider, self.fixture)

    def test_25_old_tencent_official_error_page_is_rejected(self) -> None:
        provider = copy.deepcopy(self.fixture["providers"][1])
        provider["official_price_url"] = "https://cloud.tencent.com/document/product/1207/59875"
        with self.assertRaisesRegex(FlowRejected, "当前已核验官方地址"):
            simulate_provider_purchase(provider, self.fixture)

    def test_26_incomplete_order_card_is_rejected(self) -> None:
        provider = copy.deepcopy(self.fixture["providers"][0])
        provider["mock_quote"]["refund"] = ""
        with self.assertRaisesRegex(FlowRejected, "refund"):
            simulate_provider_purchase(provider, self.fixture)

    def test_27_fresh_cloud_does_not_require_migration_state(self) -> None:
        state = copy.deepcopy(self.fixture["fresh_safe_state"])
        state["local_gateway_exists"] = True
        with self.assertRaisesRegex(FlowRejected, "不得虚构本地 gateway"):
            simulate_existing_server(self.fixture, state)

    def test_28_manager_only_weixin_environment_override_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.fixture)
        fixture["deployment_trace"].remove("simulated_systemd_manager_env_clean")
        with self.assertRaisesRegex(FlowRejected, "部署证据"):
            simulate_existing_server(fixture)




class V74ContractTests(unittest.TestCase):
    """V0.1 文档回归；安装状态机测试见 test_install_acceptance.py。"""

    def _skill(self) -> str:
        return (ROOT / "SKILL.md").read_text(encoding="utf-8")

    def _cloud_doc(self) -> str:
        return (ROOT / "references" / "cloud-deployment.md").read_text(encoding="utf-8")

    def _tools_doc(self) -> str:
        return (ROOT / "references" / "tools.md").read_text(encoding="utf-8")

    def _weixin_doc(self) -> str:
        return (ROOT / "references" / "weixin-setup-zh.md").read_text(encoding="utf-8")

    def _soul(self) -> str:
        return (ROOT / "assets" / "SOUL.zh-CN.md").read_text(encoding="utf-8")

    def test_21_cloud_credential_is_independent(self):
        doc = self._cloud_doc()
        require("为云端创建独立凭据" in doc, "云端必须使用独立凭据")
        require("独立撤销" in doc, "云端凭据必须可单独撤销")

    def test_22_plaintext_key_migration_recipe_is_absent(self):
        doc = self._cloud_doc()
        require("不从 `.env` 提取或传输秘密" in doc, "必须禁止从环境文件提取 key")
        require("不提供读取 `.env`" in doc, "必须拒绝明文秘密迁移配方")
        require("不直接复制整份本机 `.env`" in doc, "必须禁止复制完整环境秘密")

    def test_23_step_four_stages_without_starting(self):
        skill = self._skill()
        weixin = self._weixin_doc()
        require("gateway 均未运行" in skill, "第 4 步必须保持 gateway 未运行")
        require("不选择 `Done`" in weixin and "直达助手" in weixin, "微信连接不得要求用户退出通用向导")
        require("不安装、不启动、不重启 gateway" in weixin, "直达助手不得提前改变服务状态")
        require("第 5 步才启动" in weixin, "真实启动必须在第 5 步")

    def test_24_qr_login_secret_stays_out_of_chat(self):
        skill = self._skill()
        weixin = self._weixin_doc()
        require("二维码和登录 URL 属于短时登录凭据" in skill, "Skill 必须定义二维码秘密边界")
        require("也不通过普通截图分享" in weixin, "二维码不得进入聊天、工具输出、日志或截图分享")

    # ---- 场景 E：增量入口 ----

    def test_25_scenario_e_has_detection_checklist(self):
        skill = self._skill()
        require("Hermes 内部只读检测" in skill, "场景 E 必须先限制检测范围")
        require("用户目录、外部账号或其他应用" in skill, "外部检测必须逐项授权")

    def test_26_scenario_e_branch_one_has_all_options(self):
        skill = self._skill()
        for opt in ("A. 知识库", "B. 编程", "C. 定时推送", "D. 迁移到云端", "E. 记忆"):
            require(opt in skill, f"分支一缺少选项 {opt}")
        require("F. 都可以" in skill, "分支一缺少默认出口 F")
        require("G. 都不需要" in skill, "分支一缺少退出出口 G")

    def test_26a_first_response_explains_product_and_user_role(self):
        skill = self._skill()
        require("能在微信里直接聊天的 AI 助手" in skill, "首次回应没有先说用户会得到什么")
        require("你不需要懂代码" in skill, "首次回应没有说明 Agent 与用户分工")
        require("独立的微信机器人身份" in skill, "首次回应没有解释微信身份")

    def test_26b_post_base_menu_has_three_explained_upgrades(self):
        skill = self._skill()
        for option in ("A. 连接知识库", "B. 连接编程智能体", "C. 设置日常自动化"):
            require(option in skill, f"基础完成后缺少能力升级 {option}")
        require("你只需要选择资料来源" in skill, "知识库入口没有说明用户动作")
        require("你只需要选择项目" in skill, "编程入口没有说明用户动作")
        require("你只需要确认内容、时间和投递位置" in skill, "日常自动化入口没有说明用户动作")

    def test_27_scenario_e_has_upgrade_branch(self):
        skill = self._skill()
        require("老版本" in skill and "升级" in skill, "缺少老版本升级分支")

    def test_28_scenario_e_has_bridge_decision_table(self):
        skill = self._skill()
        require("助手位置" in skill and "Codex 位置" in skill, "缺少桥接 2×2 判断表")
        require("本 Skill 不自动搭桥" in skill, "缺少桥接停止边界")

    # ---- 第 5 步新验收项 ----

    def test_29_search_verification_exists(self):
        skill = self._skill()
        require("搜索实测（仅用户需要时）" in skill, "缺少可选搜索实测")
        require("不影响基础助手完成" in skill, "搜索不能成为基础完成门槛")

    def test_30_memory_verification_with_full_script(self):
        skill = self._skill()
        require("记忆实测（仅用户需要时）" in skill, "缺少可选记忆实测")
        require("三个新会话" in skill, "测试记忆必须跨三个会话验证并清理")
        require("从持久记忆移除 ≠" in skill, "记忆清理必须披露历史保留边界")
        require("不是保险柜" in skill, "记忆告知缺少敏感信息警告")

    def test_31_planning_verification_exists(self):
        skill = self._skill()
        require("任务规划实测" in skill, "缺少任务规划实测验收项")

    # ---- 铁律与流程完整性 ----

    def test_32_closed_loop_first_rule(self):
        skill = self._skill()
        require("闭环优先" in skill, "交互铁律缺少闭环优先")

    def test_33_version_gate_exists(self):
        skill = self._skill()
        require("能力闸" in skill, "第 2 步缺少可执行能力闸")

    def test_34_macos_sleep_notice_exists(self):
        skill = self._skill()
        require("合盖休眠" in skill, "第 2 步缺少 macOS 防休眠告知")

    def test_35_single_gateway_warning(self):
        skill = self._skill()
        require("同一个 Weixin token 只允许一个轮询实例" in skill, "第 1 步缺少单 token 单轮询警告")

    def test_36_safety_pledge_before_scan(self):
        skill = self._skill()
        require("只和你私聊" in skill and "不刷屏" in skill, "第 4 步缺少安全守则三条")

    def test_37_bridge_has_no_arbitrary_command_queue(self):
        tools = self._tools_doc()
        require(".task.json" not in tools, "不得同步任意命令任务文件")
        require('"command"' not in tools, "不得把任意命令字段作为桥接协议")
        require("逐任务确认" in tools, "桥接必须逐任务确认")

    def test_38_privacy_claim_names_model_provider(self):
        soul = self._soul()
        require("相关内容可能发送给当前模型或工具提供商" in soul, "SOUL 必须披露模型提供商数据路径")
        require("不会交给第三方平台" not in soul, "SOUL 不得承诺第三方永远不可见")

    def test_39_no_timer_based_consent(self):
        skill = self._skill()
        require("10 秒无响应" not in skill, "用户未回复时不得自动选择")
        require("用户未回复时暂停" in skill, "必须定义无回复暂停")

    def test_40_no_fixed_monthly_cost_claim(self):
        md = "\n".join(p.read_text(encoding="utf-8") for p in ROOT.rglob("*.md"))
        for phrase in ("几块钱/月", "不超过十块钱/月", "几十到一百元/年"):
            require(phrase not in md, f"不得保留固定费用承诺：{phrase}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

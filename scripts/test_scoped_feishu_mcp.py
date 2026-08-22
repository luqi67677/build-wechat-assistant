#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scoped_feishu_mcp import (
    FeishuError,
    FeishuStore,
    build_server,
    load_scope_file,
    probe_store,
    resolve_and_save_scope,
    save_scope_file,
    validate_node_runtime,
)
from scoped_knowledge_mcp import KnowledgeError

HAS_MCP = importlib.util.find_spec("mcp") is not None


class FakeContext:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self._bwa_confirmation_schema = object

    async def elicit(self, **_kwargs):
        action = "accept" if self.accepted else "decline"
        return SimpleNamespace(action=action, data=SimpleNamespace(confirm=self.accepted))


class HermesConsentContext:
    _bwa_confirmation_schema = object

    async def elicit(self, **_kwargs):
        return SimpleNamespace(action="accept", data=None)


@unittest.skipIf(os.name == "nt", "该集成测试使用 POSIX shell 假 CLI；Windows 运行时由真实 lark-cli.exe 验证")
class ScopedFeishuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.log = base / "stdin.log"
        self.fake = base / "lark-cli"
        self.fake.write_text(
            "#!/bin/sh\n"
            "case \" $* \" in *\" docs +fetch \"*)\n"
            "  printf '%s\\n' '{\"data\":{\"document\":{\"document_id\":\"docABC123\",\"revision_id\":7,\"title\":\"样例\",\"content\":\"正文\"}}}'\n"
            "  ;;\n"
            "*)\n"
            f"  /bin/cat > '{self.log}'\n"
            "  printf '%s\\n' '{\"data\":{\"document_id\":\"newDOC456\",\"url\":\"https://example.feishu.cn/docx/newDOC456\"}}'\n"
            "  ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.node = base / "node"
        self.node.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then printf 'v22.0.0\\n'; exit 0; fi\n"
            "if [ \"$1\" = -e ]; then printf '{\"release\":\"node\",\"version\":\"22.0.0\",\"execPath\":\"%s\",\"v8\":\"12.0\"}\\n' \"$0\"; exit 0; fi\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        self.node.chmod(self.node.stat().st_mode | stat.S_IXUSR)
        self.url = "https://example.feishu.cn/wiki/wikiABC123"
        self.store = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123",
            "bot", "wiki-node", "wikiABC123"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reads_only_fixed_document(self) -> None:
        result = self.store.read_document(self.url + "?from=test")
        self.assertEqual(result["document_id"], "docABC123")
        self.assertEqual(result["content"], "正文")

    def test_other_document_rejected_before_process(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            with self.assertRaisesRegex(FeishuError, "联网前拒绝"):
                self.store.read_document("https://example.feishu.cn/wiki/otherABC999")
        run.assert_not_called()

    def test_lookalike_domain_is_rejected_before_process(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            with self.assertRaisesRegex(FeishuError, "官方文档链接"):
                self.store.read_document("https://feishu.cn.attacker.invalid/wiki/wikiABC123")
        run.assert_not_called()

    def test_non_document_official_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(FeishuError, "官方文档链接"):
            FeishuStore.open(
                self.fake, self.home, "test-readonly",
                "https://open.feishu.cn/document/server-docs", "docABC123",
            )

    def test_create_uses_fixed_parent_and_stdin(self) -> None:
        result = self.store.create_document("新笔记", "秘密不会出现在命令参数")
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "# 新笔记\n\n秘密不会出现在命令参数")

    def test_create_uses_current_v2_flags_without_content_in_argv(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"data":{"document_id":"newDOC456"}}'
            self.store.create_document("新笔记", "私有正文")
        command = run.call_args.args[0]
        self.assertIn("--doc-format", command)
        self.assertIn("--content", command)
        self.assertIn("--parent-token", command)
        self.assertNotIn("--title", command)
        self.assertNotIn("--markdown", command)
        self.assertNotIn("--wiki-node", command)
        self.assertNotIn("新笔记", command)
        self.assertNotIn("私有正文", command)
        self.assertEqual(run.call_args.kwargs["input"], "# 新笔记\n\n私有正文")

    def test_missing_create_scope_is_actionable_without_raw_cli_output(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = '{"error":"missing scope docx:document:create","token":"secret-value"}'
            run.return_value.stderr = "permission denied"
            with self.assertRaisesRegex(FeishuError, "docx:document:create") as raised:
                self.store.create_document("新笔记", "正文")
        self.assertIn("Agent", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_unknown_feishu_failure_never_echoes_raw_output(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = '{"token":"secret-value"}'
            run.return_value.stderr = "unexpected failure"
            with self.assertRaises(FeishuError) as raised:
                self.store.read_document()
        self.assertIn("只补充当前操作缺少的权限", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_personal_library_uses_parent_position(self) -> None:
        store = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123",
            "user", "wiki-space", "my_library",
        )
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"data":{"document_id":"newDOC456"}}'
            store.create_document("新笔记", "正文")
        command = run.call_args.args[0]
        self.assertIn("--parent-position", command)
        self.assertNotIn("--parent-token", command)

    def test_non_personal_wiki_space_requires_fixed_node(self) -> None:
        store = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123",
            "user", "wiki-space", "spaceABC123",
        )
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            with self.assertRaisesRegex(FeishuError, "具体父节点"):
                store.create_document("新笔记", "正文")
        run.assert_not_called()

    def test_read_only_store_rejects_creation(self) -> None:
        store = FeishuStore.open(self.fake, self.home, "test-readonly", self.url, "docABC123")
        with self.assertRaisesRegex(FeishuError, "只读模式"):
            store.create_document("标题", "内容")

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_read_only_server_does_not_expose_create_tool(self) -> None:
        store = FeishuStore.open(self.fake, self.home, "test-readonly", self.url, "docABC123")
        tools = {tool.name for tool in build_server(store)._tool_manager.list_tools()}
        self.assertEqual(tools, {"read_allowed_feishu_document"})

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_fixed_parent_server_exposes_read_and_create_tools(self) -> None:
        tools = {tool.name for tool in build_server(self.store)._tool_manager.list_tools()}
        self.assertEqual(tools, {
            "read_allowed_feishu_document",
            "create_feishu_knowledge_document",
        })

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_model_transfer_decline_stops_before_feishu_process(self) -> None:
        read_tool = next(
            tool for tool in build_server(self.store)._tool_manager.list_tools()
            if tool.name == "read_allowed_feishu_document"
        )
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            with self.assertRaisesRegex(KnowledgeError, "已拒绝"):
                asyncio.run(read_tool.fn(FakeContext(False), self.url))
        run.assert_not_called()

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_model_transfer_accept_reads_fixed_document(self) -> None:
        read_tool = next(
            tool for tool in build_server(self.store)._tool_manager.list_tools()
            if tool.name == "read_allowed_feishu_document"
        )
        result = asyncio.run(read_tool.fn(FakeContext(True), self.url))
        self.assertEqual(result["document_id"], "docABC123")

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_hermes_action_accept_without_form_payload_is_confirmation(self) -> None:
        create_tool = next(
            tool for tool in build_server(self.store)._tool_manager.list_tools()
            if tool.name == "create_feishu_knowledge_document"
        )
        result = asyncio.run(create_tool.fn("新笔记", "正文", HermesConsentContext()))
        self.assertEqual(result["status"], "created")

    def test_wrong_document_id_fails_closed(self) -> None:
        store = FeishuStore.open(self.fake, self.home, "test-readonly", self.url, "otherDOC999")
        with self.assertRaisesRegex(FeishuError, "不是已授权文档"):
            store.read_document()

    def test_probe_reports_only_safe_boolean_evidence(self) -> None:
        readonly = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123",
        )
        result = probe_store(readonly)
        self.assertEqual(result["result"], "LIVE_READ_OK")
        self.assertTrue(result["allowed_document_read"])
        self.assertTrue(result["unauthorized_document_blocked_before_network"])
        self.assertFalse(result["content_printed"])
        self.assertFalse(result["document_id_printed"])

    def test_every_cli_call_binds_the_dedicated_profile(self) -> None:
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"data":{"document_id":"docABC123","content":"正文"}}'
            self.store.read_document()
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ["--profile", "test-readonly"])
        self.assertLess(command.index("--profile"), command.index("docs"))

    def test_explicit_node_runtime_does_not_depend_on_inherited_path(self) -> None:
        store = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123", node=self.node,
        )
        with patch("scoped_feishu_mcp.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"data":{"document_id":"docABC123","content":"正文"}}'
            store.read_document()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], [str(self.node.resolve()), str(self.fake.resolve())])
        self.assertEqual(command[2:4], ["--profile", "test-readonly"])

    def test_node_runtime_identity_is_verified(self) -> None:
        resolved, version = validate_node_runtime(self.node)
        self.assertEqual(resolved, self.node.resolve())
        self.assertEqual(version, "v22.0.0")

    def test_arbitrary_executable_cannot_pose_as_node(self) -> None:
        with self.assertRaisesRegex(FeishuError, "不是可核验的 Node.js"):
            validate_node_runtime(self.fake)

    def test_version_only_impostor_cannot_pose_as_node(self) -> None:
        impostor = Path(self.temp.name) / "node-impostor"
        impostor.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then printf 'v22.0.0\\n'; exit 0; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        impostor.chmod(impostor.stat().st_mode | stat.S_IXUSR)
        with self.assertRaisesRegex(FeishuError, "身份探针"):
            validate_node_runtime(impostor)

    def test_invalid_node_runtime_is_rejected(self) -> None:
        with self.assertRaisesRegex(FeishuError, "Node.js"):
            FeishuStore.open(
                self.fake, self.home, "test-readonly", self.url, "docABC123",
                node=Path(self.temp.name) / "missing-node",
            )

    def test_invalid_profile_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(FeishuError, "Profile 名称无效"):
            FeishuStore.open(self.fake, self.home, "../other", self.url, "docABC123")

    def test_agent_bound_profile_preserves_exact_hermes_home(self) -> None:
        hermes_home = Path(self.temp.name) / "hermes-root"
        hermes_home.mkdir()
        store = FeishuStore.open(
            self.fake, self.home, "test-readonly", self.url, "docABC123",
            hermes_home=hermes_home,
        )
        self.assertEqual(store._env()["HERMES_HOME"], str(hermes_home.resolve()))

    def test_symlink_hermes_home_is_rejected(self) -> None:
        target = Path(self.temp.name) / "hermes-target"
        target.mkdir()
        link = Path(self.temp.name) / "hermes-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(FeishuError, "Hermes 工作区无效"):
            FeishuStore.open(
                self.fake, self.home, "test-readonly", self.url, "docABC123",
                hermes_home=link,
            )

    def test_scope_file_round_trip_is_private(self) -> None:
        scope_dir = Path(self.temp.name) / "scope"
        scope_dir.mkdir(mode=0o700)
        scope_file = save_scope_file(scope_dir / "feishu.json", {
            "profile": "test-readonly",
            "document": self.url,
            "expected_document_id": "docABC123",
        })
        self.assertEqual(scope_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_scope_file(scope_file)["profile"], "test-readonly")

    def test_resolve_scope_saves_document_id_without_returning_content(self) -> None:
        scope_dir = Path(self.temp.name) / "resolved-scope"
        scope_dir.mkdir(mode=0o700)
        scope_file = resolve_and_save_scope(
            scope_dir / "feishu.json",
            {"profile": "test-readonly", "document": self.url, "identity": "user"},
            self.fake,
            self.home,
        )
        stored = load_scope_file(scope_file)
        self.assertEqual(stored["expected_document_id"], "docABC123")
        self.assertEqual(stored["identity"], "user")
        self.assertEqual(scope_file.stat().st_mode & 0o777, 0o600)

    def test_scope_file_refuses_open_parent_permissions(self) -> None:
        scope_dir = Path(self.temp.name) / "open-scope"
        scope_dir.mkdir(mode=0o777)
        with self.assertRaisesRegex(FeishuError, "父目录仍允许其他账号"):
            save_scope_file(scope_dir / "feishu.json", {
                "profile": "test-readonly",
                "document": self.url,
                "expected_document_id": "docABC123",
            })

    def test_scope_file_rejects_unknown_fields(self) -> None:
        scope_dir = Path(self.temp.name) / "scope-unknown"
        scope_dir.mkdir(mode=0o700)
        with self.assertRaisesRegex(FeishuError, "未知字段"):
            save_scope_file(scope_dir / "feishu.json", {
                "profile": "test-readonly",
                "document": self.url,
                "expected_document_id": "docABC123",
                "unexpected": "value",
            })

    def test_scope_file_refuses_overwrite(self) -> None:
        scope_dir = Path(self.temp.name) / "scope-existing"
        scope_dir.mkdir(mode=0o700)
        scope_file = scope_dir / "feishu.json"
        payload = {
            "profile": "test-readonly",
            "document": self.url,
            "expected_document_id": "docABC123",
        }
        save_scope_file(scope_file, payload)
        with self.assertRaisesRegex(FeishuError, "已存在"):
            save_scope_file(scope_file, payload)


if __name__ == "__main__":
    unittest.main()

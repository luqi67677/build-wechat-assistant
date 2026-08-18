#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scoped_knowledge_mcp import (
    KnowledgeError,
    KnowledgeStore,
    build_server,
    initialize_empty_root,
    require_confirmation,
    sha256_bytes,
)

HAS_MCP = importlib.util.find_spec("mcp") is not None


class FakeContext:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.messages: list[str] = []
        self._bwa_confirmation_schema = object

    async def elicit(self, **kwargs):
        self.messages.append(kwargs["message"])
        action = "accept" if self.accepted else "decline"
        return SimpleNamespace(action=action, data=SimpleNamespace(confirm=self.accepted))


class TimeoutContext:
    _bwa_confirmation_schema = object

    async def elicit(self, **_kwargs):
        return SimpleNamespace(action="cancel", data=None)


class EmptyContentAcceptContext:
    async def elicit(self, **kwargs):
        return SimpleNamespace(action="accept", data=kwargs["schema"]())


class ScopedKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "vault"
        self.state = base / "private-state"
        self.root.mkdir()
        (self.root / "existing.md").write_text("第一版\n苹果", encoding="utf-8")
        self.store = KnowledgeStore.open(self.root, self.state, writable=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_search_create_update_and_rollback(self) -> None:
        first = self.store.read_note("existing.md")
        self.assertEqual(first["content"], "第一版\n苹果")
        self.assertEqual(self.store.search_notes("苹果")[0]["line"], 2)

        created = self.store.create_note("new.md", "新内容")
        self.assertEqual(self.store.read_note("new.md")["content"], "新内容")
        self.store.rollback(created["change_id"])
        self.assertFalse((self.root / "new.md").exists())

        updated = self.store.update_note("existing.md", "第二版", first["sha256"])
        self.assertEqual(self.store.read_note("existing.md")["content"], "第二版")
        self.store.rollback(updated["change_id"])
        self.assertEqual(self.store.read_note("existing.md")["content"], "第一版\n苹果")

    def test_initialize_empty_root_creates_private_new_knowledge_base(self) -> None:
        root = Path(self.temp.name) / "new-knowledge"
        self.assertEqual(initialize_empty_root(root), root.resolve())
        self.assertTrue(root.is_dir())
        self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_initialize_empty_root_never_reuses_existing_location(self) -> None:
        root = Path(self.temp.name) / "already-there"
        root.mkdir()
        (root / "keep.md").write_text("不能覆盖", encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "已经存在"):
            initialize_empty_root(root)
        self.assertEqual((root / "keep.md").read_text(encoding="utf-8"), "不能覆盖")

    def test_read_only_mode_rejects_write(self) -> None:
        read_only = KnowledgeStore.open(self.root, self.state, writable=False)
        with self.assertRaisesRegex(KnowledgeError, "只读模式"):
            read_only.create_note("new.md", "内容")

    def test_state_directory_permissions_are_rehardened(self) -> None:
        self.state.chmod(0o777)
        KnowledgeStore.open(self.root, self.state, writable=False)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_already_private_state_does_not_require_chmod_permission(self) -> None:
        self.state.chmod(0o700)
        with patch.object(Path, "chmod", side_effect=PermissionError("sandbox denied chmod")):
            store = KnowledgeStore.open(self.root, self.state, writable=False)
        self.assertEqual(store.state_dir, self.state.resolve())

    def test_state_directory_stops_if_permissions_remain_open(self) -> None:
        self.state.chmod(0o777)
        with (
            patch.object(Path, "chmod", return_value=None),
            self.assertRaisesRegex(KnowledgeError, "其他账号访问"),
        ):
            KnowledgeStore.open(self.root, self.state, writable=False)

    def test_symlink_state_directory_is_rejected(self) -> None:
        target = Path(self.temp.name) / "state-target"
        target.mkdir()
        link = Path(self.temp.name) / "state-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(KnowledgeError, "状态目录无效"):
            KnowledgeStore.open(self.root, link, writable=False)

    def test_parent_and_absolute_paths_are_rejected(self) -> None:
        for path in ("../outside.md", "/tmp/outside.md", ".hidden.md", "folder/../outside.md"):
            with self.subTest(path=path), self.assertRaises(KnowledgeError):
                self.store.read_note(path)

    def test_symlink_escape_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret", encoding="utf-8")
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(KnowledgeError, "符号链接|越出"):
            self.store.read_note("link/secret.md")

    def test_update_requires_matching_digest(self) -> None:
        wrong = sha256_bytes(b"wrong")
        with self.assertRaisesRegex(KnowledgeError, "已经变化"):
            self.store.update_note("existing.md", "覆盖", wrong)
        self.assertEqual((self.root / "existing.md").read_text(encoding="utf-8"), "第一版\n苹果")

    def test_rollback_refuses_to_overwrite_later_change(self) -> None:
        created = self.store.create_note("new.md", "第一版")
        (self.root / "new.md").write_text("后来修改", encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "又被修改"):
            self.store.rollback(created["change_id"])
        self.assertTrue((self.root / "new.md").exists())

    def test_create_is_removed_when_receipt_cannot_be_saved(self) -> None:
        with (
            patch.object(KnowledgeStore, "_write_receipt", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.store.create_note("new.md", "不会留下半成品")
        self.assertFalse((self.root / "new.md").exists())

    def test_update_is_restored_when_receipt_cannot_be_saved(self) -> None:
        before = self.store.read_note("existing.md")
        with (
            patch.object(KnowledgeStore, "_write_receipt", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.store.update_note("existing.md", "不会留下未记录修改", before["sha256"])
        self.assertEqual((self.root / "existing.md").read_text(encoding="utf-8"), "第一版\n苹果")
        self.assertEqual(list(self.state.glob("*.bak")), [])

    def test_elicitation_accept_and_decline(self) -> None:
        accepted = FakeContext(True)
        asyncio.run(require_confirmation(accepted, "确认"))
        self.assertIn("发起本次请求的同一界面", accepted.messages[0])
        self.assertIn("/approve", accepted.messages[0])
        self.assertIn("/deny", accepted.messages[0])
        with self.assertRaisesRegex(KnowledgeError, "已拒绝"):
            asyncio.run(require_confirmation(FakeContext(False), "确认"))

    def test_elicitation_accepts_hermes_empty_content_form(self) -> None:
        asyncio.run(require_confirmation(EmptyContentAcceptContext(), "确认"))

    def test_elicitation_timeout_explains_where_to_retry(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "同一界面.*2 分钟.*新提示"):
            asyncio.run(require_confirmation(TimeoutContext(), "确认"))

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_read_only_server_registers_exactly_three_read_tools(self) -> None:
        # 回归：只读模式必须只暴露 3 个读取工具；references/tools.md 的门禁
        # 会在清单与选择不一致时取消安装，多注册写入工具会让只读知识库永远装不上。
        read_only = KnowledgeStore.open(self.root, self.state, writable=False)
        names = sorted(tool.name for tool in build_server(read_only)._tool_manager.list_tools())
        self.assertEqual(
            names,
            ["list_knowledge_notes", "read_knowledge_note", "search_knowledge_notes"],
        )

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_writable_server_registers_all_six_tools(self) -> None:
        names = sorted(tool.name for tool in build_server(self.store)._tool_manager.list_tools())
        self.assertEqual(
            names,
            [
                "create_knowledge_note",
                "list_knowledge_notes",
                "read_knowledge_note",
                "rollback_knowledge_change",
                "search_knowledge_notes",
                "update_knowledge_note",
            ],
        )

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_model_transfer_decline_stops_before_local_read(self) -> None:
        read_tool = next(
            tool for tool in build_server(self.store)._tool_manager.list_tools()
            if tool.name == "read_knowledge_note"
        )
        with patch.object(KnowledgeStore, "read_note") as read_note:
            with self.assertRaisesRegex(KnowledgeError, "已拒绝"):
                asyncio.run(read_tool.fn("existing.md", FakeContext(False)))
        read_note.assert_not_called()

    @unittest.skipUnless(HAS_MCP, "当前 Python 环境没有 MCP SDK")
    def test_model_transfer_accept_reads_local_note(self) -> None:
        read_tool = next(
            tool for tool in build_server(self.store)._tool_manager.list_tools()
            if tool.name == "read_knowledge_note"
        )
        result = asyncio.run(read_tool.fn("existing.md", FakeContext(True)))
        self.assertEqual(result["content"], "第一版\n苹果")


if __name__ == "__main__":
    unittest.main()

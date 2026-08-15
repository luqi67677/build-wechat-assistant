#!/usr/bin/env python3
from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scoped_coding_mcp import CodingStore, _git
from scoped_feishu_mcp import FeishuError, FeishuStore
from scoped_knowledge_mcp import KnowledgeError, KnowledgeStore, initialize_empty_root


class OptionalAbilitiesJourneyTests(unittest.TestCase):
    """One synthetic user journey across every non-account optional ability."""

    def test_read_first_then_consent_to_write_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            base = Path(raw_temp)

            vault = base / "vault"
            initialize_empty_root(vault)
            (vault / "inbox.md").write_text("待整理：苹果\n", encoding="utf-8")
            knowledge_state = base / "knowledge-state"
            read_only = KnowledgeStore.open(vault, knowledge_state, writable=False)
            self.assertEqual(read_only.search_notes("苹果")[0]["path"], "inbox.md")
            with self.assertRaisesRegex(KnowledgeError, "只读模式"):
                read_only.create_note("summary.md", "苹果摘要")

            writable = KnowledgeStore.open(vault, knowledge_state, writable=True)
            created = writable.create_note("summary.md", "苹果摘要")
            current = writable.read_note("summary.md")
            updated = writable.update_note("summary.md", "苹果摘要 V2", current["sha256"])
            self.assertEqual(writable.read_note("summary.md")["content"], "苹果摘要 V2")
            writable.rollback(updated["change_id"])
            self.assertEqual(writable.read_note("summary.md")["content"], "苹果摘要")
            writable.rollback(created["change_id"])
            self.assertFalse((vault / "summary.md").exists())

            lark_home = base / "lark-home"
            lark_home.mkdir()
            lark_stdin = base / "lark-stdin.log"
            fake_lark = base / "lark-cli"
            fake_lark.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in *\" docs +fetch \"*)\n"
                "  printf '%s\\n' '{\"data\":{\"document_id\":\"docABC123\",\"revision_id\":8,\"title\":\"知识库\",\"content\":\"飞书正文\"}}'\n"
                "  ;;\n"
                "*)\n"
                f"  /bin/cat > '{lark_stdin}'\n"
                "  printf '%s\\n' '{\"data\":{\"document_id\":\"newDOC456\",\"url\":\"https://example.feishu.cn/docx/newDOC456\"}}'\n"
                "  ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_lark.chmod(fake_lark.stat().st_mode | stat.S_IXUSR)
            fake_node = base / "node"
            fake_node.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --version ]; then printf 'v22.0.0\\n'; exit 0; fi\n"
                "if [ \"$1\" = -e ]; then printf '{\"release\":\"node\",\"version\":\"22.0.0\",\"execPath\":\"%s\",\"v8\":\"12.0\"}\\n' \"$0\"; exit 0; fi\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_node.chmod(fake_node.stat().st_mode | stat.S_IXUSR)
            allowed_url = "https://example.feishu.cn/wiki/wikiABC123"
            feishu = FeishuStore.open(
                fake_lark,
                lark_home,
                "journey-readonly",
                allowed_url,
                "docABC123",
                "bot",
                "wiki-node",
                "wikiABC123",
                node=fake_node,
            )
            self.assertEqual(feishu.read_document(allowed_url + "?from=journey")["content"], "飞书正文")
            with patch("scoped_feishu_mcp.subprocess.run") as run:
                with self.assertRaisesRegex(FeishuError, "联网前拒绝"):
                    feishu.read_document("https://example.feishu.cn/wiki/otherABC999")
            run.assert_not_called()
            self.assertEqual(feishu.create_document("新笔记", "只通过标准输入传递")["status"], "created")
            self.assertEqual(lark_stdin.read_text(encoding="utf-8"), "# 新笔记\n\n只通过标准输入传递")

            project = base / "project"
            project.mkdir()
            _git(project, "init", "-q")
            _git(project, "config", "user.email", "journey@example.invalid")
            _git(project, "config", "user.name", "Journey Test")
            (project / "sample.txt").write_text("before\n", encoding="utf-8")
            _git(project, "add", "sample.txt")
            _git(project, "commit", "-q", "-m", "initial")
            fake_codex = base / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = login ] && [ \"$2\" = status ]; then exit 0; fi\n"
                "case \" $* \" in *\" --sandbox workspace-write \"*) ;; *) exit 41;; esac\n"
                "case \" $* \" in *\" --ask-for-approval never \"*) ;; *) exit 42;; esac\n"
                "case \" $* \" in *\" --ephemeral \"*) ;; *) exit 43;; esac\n"
                "printf 'after\\n' > sample.txt\n"
                "printf '已在隔离工作区修改并检查。\\n'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
            coding = CodingStore.open(project, base / "coding-state", fake_codex, 60)
            prepared = coding.prepare("把 sample.txt 从 before 改成 after。")
            self.assertEqual((project / "sample.txt").read_text(encoding="utf-8"), "before\n")
            self.assertEqual(prepared["status"], "prepared")
            coding.apply(prepared["task_id"])
            self.assertEqual((project / "sample.txt").read_text(encoding="utf-8"), "after\n")
            coding.rollback(prepared["task_id"])
            self.assertEqual((project / "sample.txt").read_text(encoding="utf-8"), "before\n")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "release.py"
SPEC = importlib.util.spec_from_file_location("release", MODULE_PATH)
assert SPEC and SPEC.loader
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def make_fake_tree(root: Path) -> None:
    """搭建最小假发布树：只含版本替换与标题日期同步涉及的文件。"""
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "scripts" / "flow_policy.py").write_text(
        'VERSION = "0.4"\nSKILL_TITLE = f"# 2026-08-15 微信 AI 助手搭建 V{VERSION}"\n',
        encoding="utf-8",
    )
    (root / "references" / "flow-contract.json").write_text(
        '{\n  "skill_version": "0.4"\n}\n',
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        "# 2026-08-15 微信 AI 助手搭建 V0.5\n"
        "\n"
        "当前适配 Hermes Agent v0.20.0；历史版本 v0.4.1 不再支持。\n"
        "RFC 示例地址 192.0.2.1 与 203.0.113.9 仅作文档占位。\n"
        "正文中出现的发布日期 2026-08-15 不应被标题日期同步改写。\n",
        encoding="utf-8",
    )
    (root / "VERSION").write_text("V0.5\n", encoding="utf-8")


class NormalizeTests(unittest.TestCase):
    def test_accepts_prefixed_and_bare_two_part_versions(self) -> None:
        self.assertEqual(RELEASE.normalize("V0.2"), ("0.2", "V0.2"))
        self.assertEqual(RELEASE.normalize("0.2"), ("0.2", "V0.2"))
        self.assertEqual(RELEASE.normalize("  V1.10  "), ("1.10", "V1.10"))

    def test_rejects_invalid_formats(self) -> None:
        for raw in ("0.2.3", "V0.2.3", "v0.2", "V", "V0.", "abc", "0..2", ""):
            with self.subTest(raw=raw), self.assertRaises(SystemExit):
                RELEASE.normalize(raw)


class ReplaceVersionTests(unittest.TestCase):
    def test_replaces_all_version_fields_without_collateral_damage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_fake_tree(root)
            with contextlib.redirect_stdout(io.StringIO()):
                RELEASE.replace_version(root, "0.5", "V0.5")

            policy = (root / "scripts" / "flow_policy.py").read_text(encoding="utf-8")
            self.assertIn('VERSION = "0.5"', policy)
            contract = (root / "references" / "flow-contract.json").read_text(encoding="utf-8")
            self.assertIn('"skill_version": "0.5"', contract)
            skill = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("搭建 V0.5", skill)
            # 三位版本号与带小数尾巴的历史版本不得被误伤
            self.assertIn("v0.20.0", skill)
            self.assertIn("v0.4.1", skill)
            # RFC 示例 IP 不得被误伤
            self.assertIn("192.0.2.1", skill)
            self.assertIn("203.0.113.9", skill)
            self.assertEqual((root / "VERSION").read_text(encoding="utf-8"), "V0.5\n")


class SyncTitleDateTests(unittest.TestCase):
    def test_updates_skill_md_and_flow_policy_title_date_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_fake_tree(root)
            with contextlib.redirect_stdout(io.StringIO()):
                changed = RELEASE.sync_title_date(root, "2026-08-18")

            self.assertEqual(set(changed), {"SKILL.md", "scripts/flow_policy.py"})
            skill = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("# 2026-08-18 微信 AI 助手搭建 V0.5", skill)
            # 正文中的同样日期不受影响
            self.assertIn("发布日期 2026-08-15 不应被标题日期同步改写", skill)
            policy = (root / "scripts" / "flow_policy.py").read_text(encoding="utf-8")
            self.assertIn('SKILL_TITLE = f"# 2026-08-18 微信 AI 助手搭建 V{VERSION}"', policy)

    def test_defaults_to_local_today(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_fake_tree(root)
            with contextlib.redirect_stdout(io.StringIO()):
                RELEASE.sync_title_date(root)
            today = date.today().isoformat()
            skill = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"# {today} 微信 AI 助手搭建 V0.5", skill)
            policy = (root / "scripts" / "flow_policy.py").read_text(encoding="utf-8")
            self.assertIn(f'SKILL_TITLE = f"# {today} 微信 AI 助手搭建 V{{VERSION}}"', policy)

    def test_fails_closed_when_title_pattern_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_fake_tree(root)
            (root / "SKILL.md").write_text("# 微信 AI 助手搭建 V0.5\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                RELEASE.sync_title_date(root, "2026-08-18")


if __name__ == "__main__":
    unittest.main(verbosity=2)

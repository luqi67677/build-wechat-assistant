#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_skill.py"
SPEC = importlib.util.spec_from_file_location("validate_skill", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATE = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT / "scripts"))
SPEC.loader.exec_module(VALIDATE)


def copy_tree(target: Path) -> Path:
    """把整棵 Skill 树复制到临时目录，排除版本控制与字节码缓存。"""
    skill = target / "skill"
    shutil.copytree(
        ROOT,
        skill,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store"),
    )
    return skill


class ValidateSkillTests(unittest.TestCase):
    def run_validator(self, skill: Path) -> tuple[int, str]:
        stream = io.StringIO()
        # 打桩流程回归：完整 unittest 套件由外层发布验证负责；
        # 在临时副本里再跑一遍会递归触发本测试并成倍拖慢套件。
        with patch.object(VALIDATE, "run_flow_tests", return_value=True), contextlib.redirect_stdout(stream):
            code = VALIDATE.main([str(skill)])
        return code, stream.getvalue()

    def test_unmodified_tree_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            code, output = self.run_validator(copy_tree(Path(raw)))
        self.assertEqual(code, 0, output)
        self.assertIn(f"PASS build-wechat-assistant V{VALIDATE.VERSION}", output)

    def test_missing_required_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = copy_tree(Path(raw))
            (skill / "references" / "tools.md").unlink()
            code, output = self.run_validator(skill)
        self.assertEqual(code, 1)
        self.assertIn("缺少文件：references/tools.md", output)

    def test_tampered_contract_skill_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = copy_tree(Path(raw))
            contract_path = skill / "references" / "flow-contract.json"
            payload = json.loads(contract_path.read_text(encoding="utf-8"))
            payload["skill_version"] = "9.9"
            contract_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            code, output = self.run_validator(skill)
        self.assertEqual(code, 1)
        self.assertIn("流程契约版本必须为", output)

    def test_version_file_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = copy_tree(Path(raw))
            (skill / "VERSION").write_text("V9.9\n", encoding="utf-8")
            code, output = self.run_validator(skill)
        self.assertEqual(code, 1)
        self.assertIn("VERSION 文件 V9.9 与 flow_policy.VERSION", output)

    def test_missing_version_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = copy_tree(Path(raw))
            (skill / "VERSION").unlink()
            code, output = self.run_validator(skill)
        self.assertEqual(code, 1)
        self.assertIn("VERSION 文件缺失或不可读", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)

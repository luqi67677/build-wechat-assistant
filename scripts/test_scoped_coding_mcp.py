#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scoped_coding_mcp import (
    CodingError,
    CodingStore,
    _codex_command,
    _codex_failure_message,
    _git,
    _require_codex_cli_contract,
    _safe_codex_env,
    main as coding_main,
)


class ScopedCodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.project = base / "project"
        self.project.mkdir()
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.email", "test@example.invalid")
        _git(self.project, "config", "user.name", "Test")
        (self.project / "sample.txt").write_text("before\n", encoding="utf-8")
        _git(self.project, "add", "sample.txt")
        _git(self.project, "commit", "-q", "-m", "initial")
        self.fake_codex = base / "fake-codex"
        self.fake_codex.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = login ] && [ \"$2\" = status ]; then exit 0; fi\n"
            "case \" $* \" in *\" --sandbox workspace-write \"*) ;; *) exit 41;; esac\n"
            "case \" $* \" in *\" --ask-for-approval never \"*) ;; *) exit 42;; esac\n"
            "case \" $* \" in *\" --ephemeral \"*) ;; *) exit 43;; esac\n"
            "printf 'after\\n' > sample.txt\n"
            "printf '已修改并测试样例文件。\\n'\n",
            encoding="utf-8",
        )
        self.fake_codex.chmod(self.fake_codex.stat().st_mode | stat.S_IXUSR)
        self.store = CodingStore.open(self.project, base / "state", self.fake_codex, 60)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_does_not_touch_project_then_apply_and_rollback(self) -> None:
        prepared = self.store.prepare("把 sample.txt 从 before 改成 after，并检查结果。")
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")
        self.assertEqual(prepared["changed_files"], ["sample.txt"])
        self.assertEqual(self.store.inspect(prepared["task_id"])["status"], "prepared")

        applied = self.store.apply(prepared["task_id"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "after\n")
        rolled_back = self.store.rollback(prepared["task_id"])
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")

    def test_codex_global_approval_flags_precede_exec(self) -> None:
        command = _codex_command(self.fake_codex, self.project)
        exec_index = command.index("exec")
        self.assertLess(command.index("--sandbox"), exec_index)
        self.assertLess(command.index("--ask-for-approval"), exec_index)
        self.assertLess(command.index("-c"), exec_index)
        self.assertGreater(command.index("--ephemeral"), exec_index)
        self.assertGreater(command.index("--ignore-user-config"), exec_index)

    def test_codex_cli_contract_accepts_required_safe_flags(self) -> None:
        fake = Path(self.temp.name) / "contract-codex"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --help ]; then printf '%s\\n' '--sandbox --ask-for-approval --config workspace-write never'; exit 0; fi\n"
            "if [ \"$1\" = exec ] && [ \"$2\" = --help ]; then printf '%s\\n' '--ephemeral --ignore-user-config --cd'; exit 0; fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        _require_codex_cli_contract(fake)

    def test_codex_cli_contract_rejects_unsafe_old_cli(self) -> None:
        fake = Path(self.temp.name) / "old-codex"
        fake.write_text("#!/bin/sh\nprintf '%s\\n' '--sandbox'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        with self.assertRaisesRegex(CodingError, "缺少受控编程所需的安全参数") as raised:
            _require_codex_cli_contract(fake)
        self.assertIn("不会降级为无限制模式", str(raised.exception))

    def test_node_shebang_codex_requires_explicit_node(self) -> None:
        launcher = Path(self.temp.name) / "node-codex"
        launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        with self.assertRaisesRegex(CodingError, "--node <Node.js真实绝对路径>"):
            CodingStore.open(
                self.project, Path(self.temp.name) / "state-node-missing", launcher, 60
            )

    def test_explicit_verified_node_is_added_to_codex_path(self) -> None:
        launcher = Path(self.temp.name) / "node-codex-valid"
        launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        node_dir = Path(self.temp.name) / "node-bin"
        node_dir.mkdir()
        node = node_dir / "node"
        node.write_text("node", encoding="utf-8")
        node.chmod(node.stat().st_mode | stat.S_IXUSR)
        with patch(
            "scoped_coding_mcp.validate_node_runtime",
            return_value=(node.resolve(), "v22.0.0"),
        ):
            store = CodingStore.open(
                self.project, Path(self.temp.name) / "state-node-valid", launcher, 60, None, node
            )
        self.assertEqual(store.node, node.resolve())
        self.assertEqual(
            Path(_safe_codex_env(None, store.node)["PATH"].split(os.pathsep)[0]),
            node_dir.resolve(),
        )

    def test_readonly_codex_home_has_actionable_safe_recovery(self) -> None:
        result = subprocess.CompletedProcess(
            ["codex"],
            1,
            "",
            "failed to open state db: attempt to write a readonly database token=secret-value",
        )
        message = _codex_failure_message(result)
        self.assertIn("专用登录目录当前不可写", message)
        self.assertIn("主项目没有被修改", message)
        self.assertNotIn("secret-value", message)

    def test_dirty_project_is_rejected_before_codex(self) -> None:
        (self.project / "sample.txt").write_text("user change\n", encoding="utf-8")
        with self.assertRaisesRegex(CodingError, "已有未提交修改"):
            self.store.prepare("尝试修改项目")

    def test_apply_rejects_changed_project(self) -> None:
        prepared = self.store.prepare("准备一个修改")
        (self.project / "other.txt").write_text("later\n", encoding="utf-8")
        with self.assertRaisesRegex(CodingError, "已有未提交修改"):
            self.store.apply(prepared["task_id"])

    def test_apply_restores_project_when_task_state_cannot_be_saved(self) -> None:
        prepared = self.store.prepare("准备一个修改")
        with (
            patch.object(CodingStore, "_write_task", side_effect=OSError("disk full")),
            self.assertRaisesRegex(CodingError, "记录.*失败.*撤销"),
        ):
            self.store.apply(prepared["task_id"])
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")
        self.assertEqual(self.store.inspect(prepared["task_id"])["status"], "prepared")

    def test_existing_task_record_survives_atomic_replace_failure(self) -> None:
        prepared = self.store.prepare("准备一个修改")
        task_path = self.store._task_path(prepared["task_id"])
        before = task_path.read_bytes()
        with (
            patch("scoped_coding_mcp.os.replace", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.store._write_task(prepared["task_id"], {"status": "broken"})
        self.assertEqual(task_path.read_bytes(), before)
        self.assertEqual(list(task_path.parent.glob(".bwa-*")), [])

    def test_rollback_restores_applied_state_when_task_state_cannot_be_saved(self) -> None:
        prepared = self.store.prepare("准备一个修改")
        self.store.apply(prepared["task_id"])
        with (
            patch.object(CodingStore, "_write_task", side_effect=OSError("disk full")),
            self.assertRaisesRegex(CodingError, "回滚记录.*失败.*回滚前"),
        ):
            self.store.rollback(prepared["task_id"])
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "after\n")
        self.assertEqual(self.store.inspect(prepared["task_id"])["status"], "applied")

    def test_rollback_rejects_later_changes(self) -> None:
        prepared = self.store.prepare("准备一个修改")
        self.store.apply(prepared["task_id"])
        (self.project / "later.txt").write_text("later\n", encoding="utf-8")
        with self.assertRaisesRegex(CodingError, "其他修改"):
            self.store.rollback(prepared["task_id"])
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "after\n")

    def test_project_subdirectory_is_rejected(self) -> None:
        child = self.project / "child"
        child.mkdir()
        with self.assertRaisesRegex(CodingError, "根目录"):
            CodingStore.open(child, Path(self.temp.name) / "other-state", self.fake_codex, 60)

    def test_sensitive_file_patch_is_rejected(self) -> None:
        fake = Path(self.temp.name) / "sensitive-codex"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = login ] && [ \"$2\" = status ]; then exit 0; fi\n"
            "printf 'changed\\n' > AGENTS.md\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        store = CodingStore.open(self.project, Path(self.temp.name) / "state-2", fake, 60)
        with self.assertRaisesRegex(CodingError, "受保护文件"):
            store.prepare("修改受保护规则")
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_expired_codex_login_has_actionable_safe_recovery(self) -> None:
        fake = Path(self.temp.name) / "logged-out-codex"
        fake.write_text(
            "#!/bin/sh\nprintf 'Not logged in; token=secret-value\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        store = CodingStore.open(self.project, Path(self.temp.name) / "state-auth", fake, 60)
        with self.assertRaisesRegex(CodingError, "codex login status") as raised:
            store.prepare("修改样例文件")
        self.assertIn("主项目没有被修改", str(raised.exception))
        self.assertIn("不要在聊天里发送", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")

    def test_codex_quota_failure_has_actionable_safe_message(self) -> None:
        fake = Path(self.temp.name) / "quota-codex"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = login ] && [ \"$2\" = status ]; then exit 0; fi\n"
            "printf 'rate limit token=secret-value\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        store = CodingStore.open(self.project, Path(self.temp.name) / "state-quota", fake, 60)
        with self.assertRaisesRegex(CodingError, "额度、速率或计费状态") as raised:
            store.prepare("修改样例文件")
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")

    def test_unknown_codex_login_state_fails_before_worktree(self) -> None:
        fake = Path(self.temp.name) / "unknown-login-codex"
        fake.write_text("#!/bin/sh\nprintf 'unknown token=secret-value\\n' >&2\nexit 2\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        state = Path(self.temp.name) / "state-unknown-login"
        store = CodingStore.open(self.project, state, fake, 60)
        with self.assertRaisesRegex(CodingError, "登录状态无法核验") as raised:
            store.prepare("修改样例文件")
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertEqual(list((state / "worktrees").iterdir()), [])
        self.assertEqual((self.project / "sample.txt").read_text(encoding="utf-8"), "before\n")

    def test_dedicated_codex_home_overrides_inherited_global_home(self) -> None:
        base = Path(self.temp.name)
        dedicated_home = base / "dedicated-codex-home"
        observed = base / "observed-codex-home"
        fake = base / "home-aware-codex"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"$CODEX_HOME\" > '{observed}'\n"
            "if [ \"$1\" = login ] && [ \"$2\" = status ]; then exit 0; fi\n"
            "printf 'after\\n' > sample.txt\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        with patch.dict(os.environ, {"CODEX_HOME": str(base / "global-deepseek-home")}):
            store = CodingStore.open(
                self.project, base / "state-dedicated", fake, 60, dedicated_home
            )
            prepared = store.prepare("修改样例文件")
        self.assertEqual(observed.read_text(encoding="utf-8"), str(dedicated_home.resolve()))
        self.assertEqual(prepared["changed_files"], ["sample.txt"])
        self.assertEqual(stat.S_IMODE(dedicated_home.stat().st_mode), 0o700)

    def test_codex_home_inside_project_is_rejected(self) -> None:
        with self.assertRaisesRegex(CodingError, "不能放在代码项目里面"):
            CodingStore.open(
                self.project,
                Path(self.temp.name) / "state-home-project",
                self.fake_codex,
                60,
                self.project / ".private-codex-home",
            )

    def test_codex_home_inside_state_directory_is_rejected(self) -> None:
        state = Path(self.temp.name) / "state-home-nested"
        with self.assertRaisesRegex(CodingError, "不能放在编程状态目录里面"):
            CodingStore.open(
                self.project,
                state,
                self.fake_codex,
                60,
                state / "codex-home",
            )

    def test_cli_reports_private_directory_error_without_uncaught_exception(self) -> None:
        invalid_state = Path(self.temp.name) / "state-is-a-file"
        invalid_state.write_text("not a directory", encoding="utf-8")
        argv = [
            "scoped_coding_mcp.py",
            "--project", str(self.project),
            "--state-dir", str(invalid_state),
            "--codex", str(self.fake_codex),
            "--timeout", "60",
        ]
        with patch("sys.argv", argv), self.assertRaises(SystemExit) as raised:
            coding_main()
        self.assertIn("状态目录无法创建或访问", str(raised.exception))

    def test_cli_checks_codex_contract_before_starting_mcp(self) -> None:
        argv = [
            "scoped_coding_mcp.py",
            "--project", str(self.project),
            "--state-dir", str(Path(self.temp.name) / "state-cli-contract"),
            "--codex", str(self.fake_codex),
            "--timeout", "60",
        ]
        with (
            patch("sys.argv", argv),
            patch("scoped_coding_mcp._require_codex_cli_contract") as contract,
            patch("scoped_coding_mcp.build_server") as build_server,
        ):
            self.assertEqual(coding_main(), 0)
        contract.assert_called_once()
        build_server.return_value.run.assert_called_once_with(transport="stdio")


if __name__ == "__main__":
    unittest.main()

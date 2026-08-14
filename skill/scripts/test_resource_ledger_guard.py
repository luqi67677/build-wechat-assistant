#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "resource_ledger_guard.py"
SPEC = importlib.util.spec_from_file_location("resource_ledger_guard", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class ResourceLedgerGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.machine_id = self.home / "machine-id"
        self.machine_id.write_text("test-machine-id\n", encoding="utf-8")
        self.ledger = self.home / ".bwa-test-resource-ledger.json"
        self.root = self.home / ".hermes"
        self.workspace = self.home / "workspace"
        self.release = self.home / "release"
        self.proc = self.home / "proc"
        self.proc.mkdir(mode=0o700)
        self.unit_state = "not-found"
        self.active_state = "inactive"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def runner(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "show":
            return completed(self.unit_state)
        if arguments[0] == "is-active":
            return completed(self.active_state, 0 if self.active_state == "active" else 3)
        raise AssertionError(arguments)

    def init(self) -> dict[str, bool]:
        host_hash = GUARD._host_binding(self.machine_id, self.home)
        GUARD.create_plan(
            self.ledger,
            str(self.home),
            "smalltest",
            str(self.root),
            str(self.workspace),
            str(self.release),
            "a" * 64,
            host_hash,
        )
        return GUARD.activate_plan(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )

    def record_all(self) -> None:
        ordered = (
            ("hermes_root", self.root),
            ("profile_dir", self.root / "profiles" / "smalltest"),
            ("workspace", self.workspace),
            ("release_dir", self.release),
        )
        for resource, path in ordered:
            path.mkdir(parents=True, mode=0o700)
            checks = GUARD.record_created_resource(
                self.ledger,
                resource,
                home=self.home,
                machine_id_path=self.machine_id,
                root_fs=self.home,
            )
            self.assertTrue(all(checks.values()))

    def test_plan_is_exclusive_and_activation_rejects_preexisting_resources(self) -> None:
        self.assertTrue(all(self.init().values()))
        with self.assertRaisesRegex(GUARD.LedgerError, "plan_ledger_must_be_new"):
            self.init()
        other = self.home / ".other-ledger.json"
        existing = self.home / "existing"
        existing.mkdir()
        GUARD.create_plan(
            other,
            str(self.home),
            "smalltest",
            str(existing),
            str(self.home / "work2"),
            str(self.home / "release2"),
            "b" * 64,
            GUARD._host_binding(self.machine_id, self.home),
        )
        with self.assertRaisesRegex(GUARD.LedgerError, "preexisting_resource_conflict"):
            GUARD.activate_plan(
                other,
                home=self.home,
                machine_id_path=self.machine_id,
                root_fs=self.home,
                runner=self.runner,
            )

    def test_prewrite_fails_on_host_drift_or_surprise_resource(self) -> None:
        self.init()
        checks = GUARD.check_prewrite(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))
        self.workspace.mkdir()
        checks = GUARD.check_prewrite(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )
        self.assertFalse(checks["all_planned_resources_still_absent"])
        self.machine_id.write_text("different-machine\n", encoding="utf-8")
        checks = GUARD.check_prewrite(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )
        self.assertFalse(checks["ledger_host_binding_matches"])

    def test_activation_requires_exact_service_home_and_ledger_location(self) -> None:
        host_hash = GUARD._host_binding(self.machine_id, self.home)
        GUARD.create_plan(
            self.ledger,
            str(self.home),
            "smalltest",
            str(self.root),
            str(self.workspace),
            str(self.release),
            "a" * 64,
            host_hash,
        )
        wrong_home = self.home / "wrong-home"
        wrong_home.mkdir(mode=0o700)
        with self.assertRaisesRegex(GUARD.LedgerError, "plan_target_binding_mismatch"):
            GUARD.activate_plan(
                self.ledger,
                home=wrong_home,
                machine_id_path=self.machine_id,
                root_fs=self.home,
                runner=self.runner,
            )

    def test_local_plan_must_activate_on_exact_host_and_service_home(self) -> None:
        plan = self.home / "plan.json"
        host_hash = GUARD._host_binding(self.machine_id, self.home)
        checks = GUARD.create_plan(
            plan,
            str(self.home),
            "smalltest",
            str(self.root),
            str(self.workspace),
            str(self.release),
            "c" * 64,
            host_hash,
        )
        self.assertTrue(all(checks.values()))
        remote_ledger = self.ledger
        remote_ledger.write_bytes(plan.read_bytes())
        remote_ledger.chmod(0o600)
        checks = GUARD.activate_plan(
            remote_ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))
        payload = GUARD._load(remote_ledger)
        self.assertEqual(payload["activation_status"], "active")
        self.assertEqual(payload["service_uid"], os.geteuid())

    def test_public_plan_parent_and_legacy_cli_init_are_rejected(self) -> None:
        public = self.home / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaisesRegex(GUARD.LedgerError, "plan_ledger_parent_must_be_private"):
            GUARD.create_plan(
                public / "plan.json",
                str(self.home),
                "smalltest",
                str(self.root),
                str(self.workspace),
                str(self.release),
                "c" * 64,
                "d" * 64,
            )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            GUARD.parse_args(["init"])

    def test_seal_detects_inode_drift_and_cleanup_is_preview_only(self) -> None:
        self.init()
        self.record_all()
        self.unit_state = "loaded"
        checks = GUARD.seal_deployed(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))
        for kind, state in (
            ("model", "creation_started"),
            ("model", "created"),
            ("weixin", "creation_started"),
            ("weixin", "created"),
            ("model", "revoked_user_confirmed"),
            ("weixin", "revoked_user_confirmed"),
        ):
            GUARD.mark_authorization(
                self.ledger,
                kind,
                state,
                home=self.home,
                machine_id_path=self.machine_id,
                root_fs=self.home,
            )
        self.unit_state = "not-found"
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))
        self.assertTrue(self.root.exists())
        self.active_state = "unknown"
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertFalse(checks["test_unit_is_inactive"])
        self.active_state = "inactive"
        stray = self.proc / "777"
        stray.mkdir()
        (stray / "cmdline").write_bytes(b"/safe/python\0-m\0hermes_cli.main\0gateway\0run\0")
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertFalse(checks["same_service_user_has_no_gateway_process"])
        (stray / "cmdline").write_bytes(b"python\0-c\0safe\0")
        replacement = self.home / "replacement"
        replacement.mkdir(mode=0o700)
        original = self.workspace
        moved = self.home / "old-workspace"
        original.rename(moved)
        replacement.rename(original)
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertFalse(checks["recorded_test_resource_identity_has_no_drift"])

    def test_started_authorization_blocks_cleanup_until_outcome_is_confirmed(self) -> None:
        self.init()
        GUARD.mark_authorization(
            self.ledger,
            "model",
            "creation_started",
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
        )
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertFalse(checks["model_authorization_absent_or_revoke_confirmed"])
        GUARD.mark_authorization(
            self.ledger,
            "model",
            "absent_user_confirmed",
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
        )
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))

    def test_seal_refuses_host_drift_before_writing_state(self) -> None:
        self.init()
        self.record_all()
        self.unit_state = "loaded"
        self.machine_id.write_text("different-machine\n", encoding="utf-8")
        with self.assertRaisesRegex(GUARD.LedgerError, "ledger_binding_mismatch"):
            GUARD.seal_deployed(
                self.ledger,
                home=self.home,
                machine_id_path=self.machine_id,
                root_fs=self.home,
                runner=self.runner,
            )
        self.assertIsNone(GUARD._load(self.ledger)["sealed_resources"])

    def test_partial_cleanup_only_allows_recorded_resources_and_absent_authorizations(self) -> None:
        self.init()
        self.root.mkdir(mode=0o700)
        GUARD.record_created_resource(
            self.ledger,
            "hermes_root",
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
        )
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertTrue(all(checks.values()))
        self.workspace.mkdir(mode=0o700)
        checks = GUARD.preview_cleanup(
            self.ledger,
            home=self.home,
            machine_id_path=self.machine_id,
            root_fs=self.home,
            proc_root=self.proc,
            runner=self.runner,
        )
        self.assertFalse(checks["recorded_test_resource_identity_has_no_drift"])

    def test_no_delete_primitive_is_present(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("shutil.rmtree", "os.remove(", "Path.unlink(", "rm -"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

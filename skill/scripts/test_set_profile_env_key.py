#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "set_profile_env_key.py"
SPEC = importlib.util.spec_from_file_location("set_profile_env_key", MODULE_PATH)
assert SPEC and SPEC.loader
UPDATER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATER)


class ProfileEnvKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.env = self.root / ".env"
        self.env.write_text("MODEL_KEY=canary\nOBSIDIAN_VAULT_PATH=/old\nOBSIDIAN_VAULT_PATH=/duplicate\n", encoding="utf-8")
        self.env.chmod(0o600)
        self.vault = self.root / "vault"
        self.vault.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_update_is_idempotent_and_preserves_unrelated_line(self) -> None:
        UPDATER.update_key(self.env, "OBSIDIAN_VAULT_PATH", str(self.vault))
        UPDATER.update_key(self.env, "OBSIDIAN_VAULT_PATH", str(self.vault))
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("OBSIDIAN_VAULT_PATH="), 1)
        self.assertIn("MODEL_KEY=canary", text)

    def test_unapproved_key_and_relative_path_are_rejected(self) -> None:
        with self.assertRaisesRegex(UPDATER.UpdateError, "key_not_allowed"):
            UPDATER.update_key(self.env, "MODEL_KEY", str(self.vault))
        with self.assertRaisesRegex(UPDATER.UpdateError, "value_not_safe"):
            UPDATER.update_key(self.env, "OBSIDIAN_VAULT_PATH", "relative")

    def test_main_does_not_print_path_or_existing_value(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), patch.object(
            UPDATER, "resolve_env_path", return_value=self.env
        ):
            code = UPDATER.main([
                "--profile", "wechatassistant", "--key", "OBSIDIAN_VAULT_PATH",
                "--value", str(self.vault), "--hermes", "/fake/hermes",
                "--expected-hermes-root", str(self.root),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())["result"], "PASS")
        self.assertNotIn(str(self.vault), stream.getvalue())
        self.assertNotIn("canary", stream.getvalue())

    def test_public_resolver_rejects_another_profile_path(self) -> None:
        completed = UPDATER.subprocess.CompletedProcess([], 0, f"{self.env}\n", "")
        with patch.object(UPDATER.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(UPDATER.UpdateError, "profile_env_mismatch"):
                UPDATER.resolve_env_path("wechatassistant", "/fake/hermes", self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)

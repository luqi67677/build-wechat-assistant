#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_release_package.py"
SPEC = importlib.util.spec_from_file_location("verify_release_package", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VerifyReleasePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.release = Path(self.temp.name) / "release"
        self.release.mkdir(mode=0o700)
        self.target = self.release / "skill"
        self.target.mkdir(mode=0o700)
        self.zip_path = self.release / "build-wechat-assistant-V9.9.9.zip"
        self.sums = self.release / "SHA256SUMS"
        self.manifest = self.release / "FILES.sha256"
        self.files = {"SKILL.md": b"safe skill\n", "scripts/check.py": b"print('ok')\n"}
        self.write_package()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_package(self, entries: list[tuple[zipfile.ZipInfo | str, bytes]] | None = None) -> None:
        with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries or list(self.files.items()):
                archive.writestr(name, payload)
        archive_hash = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()
        self.sums.write_text(f"{archive_hash}  {self.zip_path.name}\n", encoding="utf-8")
        self.manifest.write_text(
            "".join(
                f"{hashlib.sha256(payload).hexdigest()}  ./{name}\n"
                for name, payload in sorted(self.files.items())
            ),
            encoding="utf-8",
        )
        for path in (self.zip_path, self.sums, self.manifest):
            path.chmod(0o600)

    def run_main(self) -> tuple[int, dict, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = VERIFY.main(
                [
                    "extract",
                    "--zip", str(self.zip_path),
                    "--sha256sums", str(self.sums),
                    "--files-manifest", str(self.manifest),
                    "--target-dir", str(self.target),
                ]
            )
        output = stream.getvalue()
        return code, json.loads(output), output

    def test_valid_package_is_verified_and_extracted_without_overwrite(self) -> None:
        code, payload, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "PASS")
        for name, content in self.files.items():
            self.assertEqual((self.target / name).read_bytes(), content)
        self.assertFalse((self.target / self.zip_path.name).exists())
        self.assertFalse((self.target / self.sums.name).exists())
        self.assertFalse((self.target / self.manifest.name).exists())
        self.assertNotIn(str(self.target), output)

    def test_zip_path_traversal_is_rejected_before_extraction(self) -> None:
        entries = list(self.files.items()) + [("../outside.txt", b"bad")]
        self.write_package(entries)
        code, payload, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "archive_path_invalid")
        self.assertFalse((self.target.parent / "outside.txt").exists())
        self.assertFalse((self.target / "SKILL.md").exists())

    def test_symlink_and_duplicate_archive_members_are_rejected(self) -> None:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entries, expected in (
            ([(link, b"SKILL.md")], "archive_entry_type_invalid"),
            ([(*list(self.files.items())[0],), (*list(self.files.items())[0],)], "archive_duplicate"),
        ):
            with self.subTest(expected=expected):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    self.write_package(entries)
                code, payload, _ = self.run_main()
                self.assertEqual(code, 2)
                self.assertEqual(payload["error"], expected)

    def test_checksum_manifest_and_existing_target_conflicts_fail_closed(self) -> None:
        cases = []
        self.sums.write_text("0" * 64 + f"  {self.zip_path.name}\n", encoding="utf-8")
        cases.append("zip_hash_mismatch")
        for expected in cases:
            code, payload, _ = self.run_main()
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], expected)
        self.write_package()
        self.manifest.write_text(self.manifest.read_text(encoding="utf-8") + f"{'0' * 64}  ./extra\n", encoding="utf-8")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "manifest_archive_mismatch")
        self.write_package()
        conflict = self.target / "SKILL.md"
        conflict.write_text("keep me", encoding="utf-8")
        code, payload, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "target_conflict")
        self.assertEqual(conflict.read_text(encoding="utf-8"), "keep me")

    def test_archive_file_cannot_also_be_a_parent_directory(self) -> None:
        self.files = {"scripts": b"file", "scripts/check.py": b"child"}
        self.write_package()
        code, payload, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "archive_entry_type_invalid")
        self.assertFalse((self.target / "scripts").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

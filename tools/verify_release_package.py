#!/usr/bin/env python3
"""验证版本化 ZIP 与双清单，并在私有目录中无覆盖解压。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path


HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


class PackageError(RuntimeError):
    """固定错误码；不得携带路径、成员名或文件内容。"""


def _private_regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not path.is_symlink()
        and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o077)
    )


def _private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and path.resolve(strict=True) == path.absolute()
        and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o077)
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PackageError("artifact_unreadable") from exc
    return digest.hexdigest()


def _portable_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _safe_relative(name: str, *, manifest: bool = False, directory: bool = False) -> str:
    if manifest:
        if not name.startswith("./"):
            raise PackageError("manifest_path_invalid")
        name = name[2:]
    error = "manifest_path_invalid" if manifest else "archive_path_invalid"
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise PackageError(error)
    raw = name[:-1] if directory and name.endswith("/") else name
    parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in parts):
        raise PackageError(error)
    if not directory and name.endswith("/"):
        raise PackageError(error)
    return "/".join(parts)


def _read_sha256sums(path: Path, zip_name: str) -> str:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, UnicodeError) as exc:
        raise PackageError("sha256sums_unreadable") from exc
    if len(lines) != 1 or "  " not in lines[0]:
        raise PackageError("sha256sums_invalid")
    digest, name = lines[0].split("  ", 1)
    if not HASH_RE.fullmatch(digest) or name != zip_name or "/" in name or "\\" in name:
        raise PackageError("sha256sums_invalid")
    return digest


def _read_manifest(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PackageError("manifest_unreadable") from exc
    if not lines or len(lines) > MAX_FILES or any(not line or "  " not in line for line in lines):
        raise PackageError("manifest_invalid")
    entries: dict[str, str] = {}
    portable: set[str] = set()
    for line in lines:
        digest, raw_name = line.split("  ", 1)
        if not HASH_RE.fullmatch(digest):
            raise PackageError("manifest_invalid")
        name = _safe_relative(raw_name, manifest=True)
        key = _portable_key(name)
        if name in entries or key in portable:
            raise PackageError("manifest_duplicate")
        entries[name] = digest
        portable.add(key)
    return entries


def _archive_inventory(archive: zipfile.ZipFile) -> tuple[dict[str, zipfile.ZipInfo], set[str]]:
    files: dict[str, zipfile.ZipInfo] = {}
    directories: set[str] = set()
    portable: set[str] = set()
    total = 0
    for info in archive.infolist():
        is_directory = info.is_dir()
        name = _safe_relative(info.filename, directory=is_directory)
        key = _portable_key(name)
        if name in files or name in directories or key in portable:
            raise PackageError("archive_duplicate")
        portable.add(key)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        allowed_type = file_type in ({0, stat.S_IFDIR} if is_directory else {0, stat.S_IFREG})
        if not allowed_type or info.flag_bits & 0x1:
            raise PackageError("archive_entry_type_invalid")
        if is_directory:
            directories.add(name)
            continue
        total += info.file_size
        if len(files) >= MAX_FILES or total > MAX_UNCOMPRESSED_BYTES:
            raise PackageError("archive_limits_exceeded")
        files[name] = info
    if not files:
        raise PackageError("archive_empty")
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise PackageError("archive_entry_type_invalid")
    return files, directories


def _verify_member_hashes(
    archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo], manifest: dict[str, str]
) -> None:
    for name, info in files.items():
        digest = hashlib.sha256()
        try:
            with archive.open(info, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise PackageError("archive_member_unreadable") from exc
        if digest.hexdigest() != manifest[name]:
            raise PackageError("member_hash_mismatch")


def _target_is_empty(target: Path, files: set[str]) -> None:
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise PackageError("target_unreadable") from exc
    if children:
        raise PackageError("target_conflict")
    for name in files:
        destination = target.joinpath(*name.split("/"))
        if destination.exists() or destination.is_symlink():
            raise PackageError("target_conflict")
        for parent in destination.parents:
            if parent == target:
                break
            if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
                raise PackageError("target_conflict")


def _extract(
    archive: zipfile.ZipFile,
    files: dict[str, zipfile.ZipInfo],
    manifest: dict[str, str],
    target: Path,
) -> None:
    for name in sorted(files):
        destination = target.joinpath(*name.split("/"))
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise PackageError("extract_failed") from exc
        if not _private_directory(destination.parent):
            raise PackageError("target_directory_unsafe")
        digest = hashlib.sha256()
        try:
            with archive.open(files[name], "r") as source, destination.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
            if os.name != "nt":
                destination.chmod(0o600)
        except FileExistsError as exc:
            raise PackageError("target_conflict") from exc
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise PackageError("extract_failed") from exc
        if digest.hexdigest() != manifest[name]:
            raise PackageError("extracted_hash_mismatch")


def verify_and_extract(zip_path: Path, sums: Path, manifest_path: Path, target: Path) -> dict[str, bool]:
    artifacts = {zip_path, sums, manifest_path}
    artifact_dir = zip_path.parent
    if not _private_directory(artifact_dir) or not _private_directory(target):
        raise PackageError("target_directory_unsafe")
    if target.name != "skill" or target.parent != artifact_dir:
        raise PackageError("target_scope_invalid")
    if any(path.parent != artifact_dir or not _private_regular(path) for path in artifacts):
        raise PackageError("artifact_scope_or_permissions_invalid")
    expected_zip_hash = _read_sha256sums(sums, zip_path.name)
    if _hash_file(zip_path) != expected_zip_hash:
        raise PackageError("zip_hash_mismatch")
    manifest = _read_manifest(manifest_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            files, _ = _archive_inventory(archive)
            if set(files) != set(manifest):
                raise PackageError("manifest_archive_mismatch")
            _target_is_empty(target, set(files))
            _verify_member_hashes(archive, files, manifest)
            _extract(archive, files, manifest, target)
    except zipfile.BadZipFile as exc:
        raise PackageError("archive_invalid") from exc
    return {
        "zip_hash_verified": True,
        "manifest_matches_archive": True,
        "unsafe_archive_entries_absent": True,
        "member_hashes_verified": True,
        "extracted_without_overwrite": True,
        "release_artifacts_outside_skill_tree": True,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="无覆盖验证并解压 build-wechat-assistant 发布包")
    sub = parser.add_subparsers(dest="action", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--zip", required=True)
    extract.add_argument("--sha256sums", required=True)
    extract.add_argument("--files-manifest", required=True)
    extract.add_argument("--target-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        checks = verify_and_extract(
            Path(args.zip), Path(args.sha256sums), Path(args.files_manifest), Path(args.target_dir)
        )
    except PackageError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    print(json.dumps({"result": "PASS", "checks": checks, "secrets_printed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

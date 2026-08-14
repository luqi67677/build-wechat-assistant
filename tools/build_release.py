#!/usr/bin/env python3
"""Build the deterministic build-wechat-assistant release candidate."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

ZIP_TIMESTAMP = (2026, 8, 14, 0, 0, 0)


class BuildError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assigned_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            value = value.args[0]
        return ast.literal_eval(value)
    raise BuildError(f"没有找到发布元数据：{name}")


def release_metadata(root: Path) -> tuple[str, frozenset[str]]:
    version = assigned_literal(root / "scripts" / "flow_policy.py", "VERSION")
    required = assigned_literal(root / "scripts" / "validate_skill.py", "REQUIRED_FILES")
    if not isinstance(version, str) or not isinstance(required, (set, frozenset)):
        raise BuildError("发布元数据格式无效。")
    return version, frozenset(required)


def inventory(root: Path, expected: frozenset[str]) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories.sort()
        names.sort()
        for name in directories:
            if (current_path / name).is_symlink():
                raise BuildError(f"Skill 中不允许符号链接目录：{(current_path / name).relative_to(root)}")
        for name in names:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BuildError(f"Skill 中只允许普通文件：{path.relative_to(root)}")
            files.append(path)
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    actual = {path.relative_to(root).as_posix() for path in files}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise BuildError(f"文件清单不匹配；缺少={missing}，多出={unexpected}。请先运行完整门禁。")
    return files


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build(skill_root: Path, release_dir: Path) -> tuple[Path, str]:
    root = skill_root.resolve(strict=True)
    if skill_root.is_symlink() or not root.is_dir():
        raise BuildError("Skill 根目录必须是真实目录。")
    destination = release_dir.resolve(strict=True)
    if destination == root or root in destination.parents:
        raise BuildError("发布目录不能放在 Skill 内部。")
    version, expected = release_metadata(root)
    files = inventory(root, expected)
    archive_path = destination / f"build-wechat-assistant-V{version}.zip"

    descriptor, raw_archive = tempfile.mkstemp(prefix=f".{archive_path.name}.", dir=destination)
    os.close(descriptor)
    temp_archive = Path(raw_archive)
    file_manifest: list[str] = []
    try:
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                data = path.read_bytes()
                file_manifest.append(f"{digest(data)}  ./{relative}")
                info = zipfile.ZipInfo(relative, ZIP_TIMESTAMP)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temp_archive.chmod(0o600)
        os.replace(temp_archive, archive_path)
    finally:
        temp_archive.unlink(missing_ok=True)

    archive_digest = digest(archive_path.read_bytes())
    atomic_write(destination / "FILES.sha256", ("\n".join(file_manifest) + "\n").encode())
    atomic_write(destination / "SHA256SUMS", f"{archive_digest}  {archive_path.name}\n".encode())
    verifier = root / "scripts" / "verify_release_package.py"
    atomic_write(destination / "verify_release_package.py", verifier.read_bytes())
    return archive_path, archive_digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成可复现的 build-wechat-assistant 发布候选")
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--release-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        archive, archive_digest = build(args.skill_root, args.release_dir)
    except (BuildError, OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"PASS {archive.name} {archive_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

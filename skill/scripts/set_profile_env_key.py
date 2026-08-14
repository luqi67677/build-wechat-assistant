#!/usr/bin/env python3
"""幂等写入允许的非秘密 Profile 环境键，不输出路径或现有环境内容。"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


ALLOWED_KEYS = {"OBSIDIAN_VAULT_PATH"}
ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class UpdateError(RuntimeError):
    pass


def resolve_env_path(profile: str, hermes: str, expected_root: Path) -> Path:
    try:
        result = subprocess.run(
            [hermes, "-p", profile, "config", "env-path"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError("hermes_unavailable") from exc
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        raise UpdateError("profile_env_unresolved")
    path = Path(output).expanduser()
    if (
        not path.is_absolute()
        or path != expected_root / "profiles" / profile / ".env"
        or not path.is_file()
        or path.is_symlink()
        or path.parent.is_symlink()
    ):
        raise UpdateError("profile_env_mismatch")
    return path


def posix_target_private(path: Path) -> bool:
    uid = os.geteuid()
    for target in (path.parent, path):
        try:
            info = target.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o077:
            return False
    return path.is_file()


def windows_target_private(path: Path) -> bool:
    script = r"""
$ErrorActionPreference = 'Stop'
$paths = $env:BWA_ENV_PATHS | ConvertFrom-Json
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowed = @($current,'S-1-5-18','S-1-5-32-544')
$ok = $true
foreach ($path in $paths) {
  $item = Get-Item -LiteralPath $path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $ok = $false }
  $acl = Get-Acl -LiteralPath $path
  $owner = $acl.Owner
  try { $owner = ([Security.Principal.NTAccount]$acl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
  if ($allowed -notcontains $owner) { $ok = $false }
  foreach ($entry in $acl.Access) {
    if ($entry.AccessControlType -ne 'Allow') { continue }
    $sid = $entry.IdentityReference.Value
    try { $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
    if ($allowed -notcontains $sid) { $ok = $false }
  }
}
if ($ok) { 'true' } else { 'false' }
"""
    env = os.environ.copy()
    env["BWA_ENV_PATHS"] = json.dumps([str(path.parent), str(path)])
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def update_key(env_path: Path, key: str, value: str) -> None:
    if key not in ALLOWED_KEYS:
        raise UpdateError("key_not_allowed")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or not candidate.is_dir() or candidate.is_symlink() or "\n" in value or "\x00" in value:
        raise UpdateError("value_not_safe_absolute_directory")
    private = windows_target_private(env_path) if os.name == "nt" else posix_target_private(env_path)
    if not private:
        raise UpdateError("profile_env_not_private")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise UpdateError("profile_env_unreadable") from exc
    replacement = f"{key}={candidate}"
    output: list[str] = []
    replaced = False
    for line in lines:
        match = ASSIGNMENT_RE.match(line)
        if match and match.group(1) == key:
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(replacement)
    try:
        with env_path.open("r+", encoding="utf-8", newline="\n") as handle:
            handle.seek(0)
            handle.write("\n".join(output) + "\n")
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise UpdateError("profile_env_update_failed") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="幂等写入允许的非秘密 Profile 环境键")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--key", required=True, choices=sorted(ALLOWED_KEYS))
    parser.add_argument("--value", required=True, help="已获授权的绝对目录；不会输出")
    parser.add_argument("--hermes", default="hermes")
    parser.add_argument("--expected-hermes-root", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        expected_root = Path(args.expected_hermes_root)
        if not expected_root.is_absolute() or expected_root.is_symlink() or not expected_root.is_dir():
            raise UpdateError("expected_root_invalid")
        env_path = resolve_env_path(args.profile, args.hermes, expected_root)
        update_key(env_path, args.key, args.value)
    except UpdateError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    print(json.dumps({"result": "PASS", "updated": True, "secrets_printed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

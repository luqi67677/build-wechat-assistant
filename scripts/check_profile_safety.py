#!/usr/bin/env python3
"""在不输出秘密值的前提下检查 Weixin Profile 的启动前安全门禁。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


FALSE_VALUES = {"", "0", "false", "no", "off"}
PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
YAML_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+|['\"][^'\"]+['\"]):(?:\s*(.*))?$")
OFFICIAL_WEIXIN_BASE_URL = "https://ilinkai.weixin.qq.com"
OFFICIAL_WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ALLOWED_PROFILE_WEIXIN_KEYS = {
    "WEIXIN_ACCOUNT_ID",
    "WEIXIN_TOKEN",
    "WEIXIN_DM_POLICY",
    "WEIXIN_GROUP_POLICY",
    "WEIXIN_ALLOWED_USERS",
    "WEIXIN_GROUP_ALLOWED_USERS",
    "WEIXIN_HOME_CHANNEL",
    "WEIXIN_HOME_CHANNEL_NAME",
    "WEIXIN_SPLIT_MULTILINE_MESSAGES",
    "WEIXIN_ALLOW_ALL_USERS",
    "WEIXIN_BASE_URL",
    "WEIXIN_CDN_BASE_URL",
}
SENSITIVE_EXTRA_KEYS = {
    "account_id",
    "token",
    "dm_policy",
    "group_policy",
    "allow_from",
    "group_allow_from",
    "base_url",
    "cdn_base_url",
}
KNOWN_NON_ACCESS_WEIXIN_LIST_PATHS = {
    ("platform_toolsets", "weixin"),
    ("known_plugin_toolsets", "weixin"),
    ("known_builtin_toolsets", "weixin"),
}


def _has_unsupported_yaml_syntax(raw: str) -> bool:
    """保守拒绝可能改变映射语义的 YAML 高级语法，不误读引号或注释。"""
    code: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            code.append(" ")
        elif quote == "'":
            if char == quote and index + 1 < len(raw) and raw[index + 1] == quote:
                code.extend((" ", " "))
                index += 1
            elif char == quote:
                quote = None
            code.append(" ")
        elif char in {"'", '"'}:
            quote = char
            code.append(" ")
        elif char == "#" and (not code or code[-1].isspace()):
            break
        else:
            code.append(char)
        index += 1

    visible = "".join(code)
    if any(token in visible for token in ("{", "}", "[", "]")):
        return True
    if re.search(r"(?:^|[\s:\-,])(?:&[^\s,]+|\*[^\s,]+|![^\s,]*)", visible):
        return True
    return re.search(r"(?:^|\s)<<\s*:", visible) is not None


class SafetyCheckError(RuntimeError):
    """无法安全完成检查。异常文本不得包含路径或秘密。"""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_env(path: Path) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SafetyCheckError("env_unreadable") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key in values:
            duplicates.add(key)
        values[key] = _unquote(value)
    return values, duplicates


def config_has_weixin_security_override(path: Path) -> tuple[bool, bool]:
    """返回 (能否可靠扫描, 是否存在会覆盖 .env 的安全相关配置)。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SafetyCheckError("config_unreadable") from exc

    stack: list[tuple[int, str]] = []
    override = False
    supported = True
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if _has_unsupported_yaml_syntax(raw):
            supported = False
        match = YAML_KEY_RE.match(raw)
        if not match:
            current = tuple(key for _, key in stack)
            if stack and _is_weixin_path(current) and current not in KNOWN_NON_ACCESS_WEIXIN_LIST_PATHS:
                supported = False
            continue
        indent = len(match.group(1).replace("\t", "        "))
        key = match.group(2).strip("'\"")
        value = (match.group(3) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        current = tuple(item for _, item in stack)
        if value.startswith("{") and (current[0] == "gateway" or "weixin" in value.lower()):
            supported = False
        if _is_weixin_path(current) and any(token in value for token in ("{", "}", "&", "*", "!")):
            supported = False
        if _is_weixin_base(current[:-1]):
            if current[-1] == "token":
                override = True
        if _is_weixin_extra(current[:-1]) and current[-1] in SENSITIVE_EXTRA_KEYS:
            override = True
        if (_is_weixin_base(current) or _is_weixin_extra(current)) and value.startswith("{"):
            supported = False
    return supported, override


def _is_weixin_base(path: tuple[str, ...]) -> bool:
    return path[-3:] == ("gateway", "platforms", "weixin") or path[-2:] == ("platforms", "weixin")


def _is_weixin_extra(path: tuple[str, ...]) -> bool:
    return path[-4:] == ("gateway", "platforms", "weixin", "extra") or path[-3:] == ("platforms", "weixin", "extra")


def _is_weixin_path(path: tuple[str, ...]) -> bool:
    return _is_weixin_base(path) or _is_weixin_extra(path) or "weixin" in path


def posix_permissions_private(paths: list[Path]) -> bool:
    uid = os.geteuid()
    for path in paths:
        try:
            info = path.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or info.st_uid != uid:
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            return False
    return True


def collect_sensitive_paths(config_path: Path, env_path: Path) -> list[Path]:
    """枚举已存在的 Profile、微信和认证敏感路径，不跟随目录链接。"""
    profile_dir = config_path.parent
    paths: list[Path] = [profile_dir, config_path, env_path]
    candidates = [profile_dir / "weixin", profile_dir / "cache", profile_dir / "auth.json"]
    try:
        runtime_home = Path.home().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafetyCheckError("runtime_home_unreadable") from exc
    candidates.append(runtime_home / ".qwen")
    if profile_dir.parent.name == "profiles":
        hermes_root = profile_dir.parent.parent
        candidates.extend(
            [
                hermes_root / "auth.json",
                hermes_root / "shared",
                hermes_root / "os-home" / ".qwen",
            ]
        )

    for candidate in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        paths.append(candidate)
        try:
            if candidate.is_dir() and not candidate.is_symlink():
                pending = [candidate]
                while pending:
                    directory = pending.pop()
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            child = Path(entry.path)
                            paths.append(child)
                            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                                pending.append(child)
        except OSError as exc:
            raise SafetyCheckError("sensitive_store_unreadable") from exc
    return list(dict.fromkeys(paths))


def windows_permissions_private(paths: list[Path]) -> bool:
    """用 SID 检查 ACL；只返回真假，不输出所有者、路径或 ACL。"""
    script = r"""
$ErrorActionPreference = 'Stop'
$paths = $env:BWA_ACL_PATHS | ConvertFrom-Json
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowedSids = @($current,'S-1-5-18','S-1-5-32-544')
$ok = $true
foreach ($path in $paths) {
  $item = Get-Item -LiteralPath $path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $ok = $false }
  $acl = Get-Acl -LiteralPath $path
  $ownerSid = $acl.Owner
  try { $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
  if ($allowedSids -notcontains $ownerSid) { $ok = $false }
  foreach ($entry in $acl.Access) {
    if ($entry.AccessControlType -ne 'Allow') { continue }
    $sid = $entry.IdentityReference.Value
    try { $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
    if ($allowedSids -notcontains $sid) { $ok = $false }
  }
}
if ($ok) { 'true' } else { 'false' }
"""
    env = os.environ.copy()
    env["BWA_ACL_PATHS"] = json.dumps([str(path) for path in paths])
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


def resolve_profile_paths(profile: str, hermes: str, expected_root: Path) -> tuple[Path, Path]:
    paths: list[Path] = []
    for command in ("path", "env-path"):
        try:
            result = subprocess.run(
                [hermes, "-p", profile, "config", command],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SafetyCheckError("hermes_unavailable") from exc
        if result.returncode != 0:
            raise SafetyCheckError("profile_path_unresolved")
        output = result.stdout.strip()
        if not output or "\n" in output:
            raise SafetyCheckError("profile_path_ambiguous")
        paths.append(Path(output).expanduser())
    config_path, env_path = paths
    if (
        not PROFILE_RE.fullmatch(profile)
        or profile == "default"
        or not config_path.is_absolute()
        or not env_path.is_absolute()
        or config_path.parent != env_path.parent
        or config_path != expected_root / "profiles" / profile / "config.yaml"
        or env_path != expected_root / "profiles" / profile / ".env"
        or not config_path.is_file()
        or not env_path.is_file()
        or config_path.is_symlink()
        or env_path.is_symlink()
        or config_path.parent.is_symlink()
    ):
        raise SafetyCheckError("profile_path_mismatch")
    return config_path, env_path


def service_absent_and_gateway_stopped(profile: str, hermes: str) -> bool:
    """只接受官方无服务定义且无手工进程的精确状态行。"""
    try:
        result = subprocess.run(
            [hermes, "-p", profile, "gateway", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    lines = {
        ANSI_RE.sub("", line).strip().lstrip("✓✗").strip().lower()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
    }
    return "gateway is not running" in lines


def snapshot_profile_state(config_path: Path, env_path: Path) -> str:
    """用稳定相对身份、路径类型与修改时间生成修订号，不读取凭据内容。"""
    digest = hashlib.sha256()
    profile_dir = config_path.parent
    base = profile_dir.parent.parent if profile_dir.parent.name == "profiles" else profile_dir
    identified: list[tuple[str, Path]] = []
    for path in collect_sensitive_paths(config_path, env_path):
        try:
            identity = path.relative_to(base).as_posix()
        except ValueError:
            identity = path.absolute().as_posix()
        identified.append((identity, path))
    for identity, path in sorted(identified, key=lambda item: item[0]):
        try:
            info = path.lstat()
        except OSError as exc:
            raise SafetyCheckError("snapshot_unavailable") from exc
        digest.update(identity.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.S_IFMT(info.st_mode)).encode())
        digest.update(b"\0")
        digest.update(str(info.st_size).encode())
        digest.update(b"\0")
        digest.update(str(info.st_mtime_ns).encode())
        digest.update(b"\0")
        digest.update(str(info.st_ctime_ns).encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def evaluate(
    profile: str,
    config_path: Path,
    env_path: Path,
    *,
    gateway_stopped: bool,
) -> dict[str, bool]:
    values, duplicates = read_env(env_path)
    config_supported, config_override = config_has_weixin_security_override(config_path)
    allowed = [item.strip() for item in values.get("WEIXIN_ALLOWED_USERS", "").split(",") if item.strip()]
    home = values.get("WEIXIN_HOME_CHANNEL", "").strip()
    false_flags = all(
        values.get(key, "").strip().lower() in FALSE_VALUES
        for key in ("WEIXIN_ALLOW_ALL_USERS", "GATEWAY_ALLOW_ALL_USERS")
    )
    process_overrides_absent = not any(
        (key == "GATEWAY_ALLOW_ALL_USERS" or key.startswith("WEIXIN_")) and str(value).strip()
        for key, value in os.environ.items()
    )
    unknown_profile_weixin_keys_absent = not any(
        key.startswith("WEIXIN_") and key not in ALLOWED_PROFILE_WEIXIN_KEYS for key in values
    )
    profile_base_url = values.get("WEIXIN_BASE_URL", "").strip().rstrip("/")
    profile_cdn_base_url = values.get("WEIXIN_CDN_BASE_URL", "").strip().rstrip("/")
    endpoints_official_or_builtin = (
        profile_base_url in {"", OFFICIAL_WEIXIN_BASE_URL}
        and profile_cdn_base_url in {"", OFFICIAL_WEIXIN_CDN_BASE_URL}
    )
    same_parent = config_path.parent.resolve() == env_path.parent.resolve()
    profile_path_matches_name = (
        config_path.is_absolute()
        and env_path.is_absolute()
        and config_path.parent.name == profile
        and config_path.parent.parent.name == "profiles"
    )
    profile_store_path_scope_resolved = profile_path_matches_name
    permission_paths = collect_sensitive_paths(config_path, env_path)
    permissions_private = (
        windows_permissions_private(permission_paths)
        if os.name == "nt"
        else posix_permissions_private(permission_paths)
    )
    return {
        "profile_is_named_nondefault": bool(PROFILE_RE.fullmatch(profile)) and profile != "default",
        "profile_paths_share_directory": same_parent,
        "profile_path_matches_requested_name": profile_path_matches_name,
        "profile_paths_are_regular_files": config_path.is_file() and env_path.is_file() and not config_path.is_symlink() and not env_path.is_symlink(),
        "profile_store_path_scope_resolved": profile_store_path_scope_resolved,
        "secret_files_and_directory_private": permissions_private,
        "weixin_and_auth_stores_private": permissions_private,
        "env_has_no_duplicate_keys": not duplicates,
        "config_scan_supported": config_supported,
        "config_access_overrides_absent": not config_override,
        "process_weixin_overrides_absent": process_overrides_absent,
        "unknown_profile_weixin_keys_absent": unknown_profile_weixin_keys_absent,
        "weixin_endpoints_are_official_or_builtin": endpoints_official_or_builtin,
        "weixin_credentials_present": bool(values.get("WEIXIN_ACCOUNT_ID", "").strip()) and bool(values.get("WEIXIN_TOKEN", "").strip()),
        "dm_policy_is_allowlist": values.get("WEIXIN_DM_POLICY", "").strip().lower() == "allowlist",
        "group_policy_is_disabled": values.get("WEIXIN_GROUP_POLICY", "").strip().lower() == "disabled",
        "allow_all_flags_are_false": false_flags,
        "owner_allowlist_has_one_user": len(allowed) == 1,
        "home_channel_matches_owner": len(allowed) == 1 and home == allowed[0],
        "service_absent_and_gateway_not_running_until_persona_ready": gateway_stopped,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="不输出秘密值的 Weixin Profile 启动前安全检查")
    parser.add_argument("--profile", required=True, help="非 default 的 Hermes Profile 名称")
    parser.add_argument("--hermes", default="hermes", help="Hermes 启动器；默认使用 PATH 中的 hermes")
    parser.add_argument("--expected-hermes-root", required=True, help="本轮已批准的 Hermes 根绝对路径")
    parser.add_argument("--snapshot", action="store_true", help="仅输出不含秘密的状态修订号，用于向导陪同")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        expected_root = Path(args.expected_hermes_root)
        if not expected_root.is_absolute() or expected_root.is_symlink() or not expected_root.is_dir():
            raise SafetyCheckError("expected_root_invalid")
        config_path, env_path = resolve_profile_paths(args.profile, args.hermes, expected_root)
        if args.snapshot:
            revision = snapshot_profile_state(config_path, env_path)
            print(json.dumps({"result": "SNAPSHOT", "state_revision": revision, "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
            return 0
        checks = evaluate(
            args.profile,
            config_path,
            env_path,
            gateway_stopped=service_absent_and_gateway_stopped(args.profile, args.hermes),
        )
    except SafetyCheckError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
        return 2
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

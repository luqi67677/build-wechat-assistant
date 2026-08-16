#!/usr/bin/env python3
"""模型调用与扫码前安全门禁：只输出布尔结果，不输出路径、配置值或工具详情。"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_profile_safety import (  # noqa: E402
    SafetyCheckError,
    collect_sensitive_paths,
    posix_permissions_private,
    windows_permissions_private,
)


PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TOOL_LINE_RE = re.compile(r"^\s*[✓✗]?\s*(enabled|disabled)\s+([A-Za-z0-9_-]+)\b")
SAFE_ENABLED_TOOLSETS = {"clarify"}
# v0.40.0 的 tools disable 会保留一个运行时 check_fn 约束的 kanban 名称；
# 它只有在顶层 toolsets 明确包含 kanban 或调度环境存在时才注册工具。
SAFE_PLATFORM_TOOLSET_FORMS = (
    {"clarify"},
    {"clarify", "kanban"},
    {"clarify", "no_mcp"},
)
SYNC_MARKERS = (
    "icloud",
    "cloudstorage",
    "dropbox",
    "onedrive",
    "google drive",
    "googledrive",
    "syncthing",
    "baidunetdisk",
    "百度网盘",
    "坚果云",
    "obsidian",
    "vault",
)


class PreQrError(RuntimeError):
    """错误码不得包含路径、命令输出或配置值。"""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreQrError("hermes_unavailable") from exc


def _config_json(profile: str, hermes: str, key: str) -> Any:
    result = _run([hermes, "-p", profile, "config", "get", key, "--json"])
    if result.returncode != 0:
        raise PreQrError("config_unreadable")
    try:
        return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise PreQrError("config_not_json") from exc


def _single_path(result: subprocess.CompletedProcess[str]) -> Path:
    if result.returncode != 0:
        raise PreQrError("profile_path_unresolved")
    output = result.stdout.strip()
    if not output or "\n" in output:
        raise PreQrError("profile_path_ambiguous")
    return Path(output).expanduser()


def _profile_contract(profile: str, hermes: str, expected_root: Path) -> tuple[bool, Path, Path]:
    # profile show 的 Profile 名是位置参数；它不是一个可放在 -p 后的状态子命令。
    shown = _run([hermes, "profile", "show", profile])
    config_path = _single_path(_run([hermes, "-p", profile, "config", "path"]))
    env_path = _single_path(_run([hermes, "-p", profile, "config", "env-path"]))
    same_directory = False
    exact_profile_directory = False
    profile_directory_regular = False
    try:
        same_directory = config_path.parent.resolve() == env_path.parent.resolve()
        exact_profile_directory = (
            config_path.is_absolute()
            and env_path.is_absolute()
            and config_path == expected_root / "profiles" / profile / "config.yaml"
            and env_path == expected_root / "profiles" / profile / ".env"
        )
        profile_info = config_path.parent.lstat()
        profile_directory_regular = stat.S_ISDIR(profile_info.st_mode) and not config_path.parent.is_symlink()
    except OSError:
        pass
    valid = (
        shown.returncode == 0
        and bool(PROFILE_RE.fullmatch(profile))
        and profile != "default"
        and same_directory
        and exact_profile_directory
        and profile_directory_regular
        and config_path.is_file()
        and env_path.is_file()
        and not config_path.is_symlink()
        and not env_path.is_symlink()
    )
    return valid, config_path, env_path


def env_has_no_weixin_state(env_path: Path) -> bool:
    """只比较键名；不保存、返回或打印任何配置值。"""
    try:
        with env_path.open("rb") as stream:
            for raw_line in stream:
                stripped = raw_line.strip()
                if not stripped or stripped.startswith(b"#") or b"=" not in stripped:
                    continue
                key = stripped.partition(b"=")[0].strip()
                if key.startswith(b"WEIXIN_") or key == b"GATEWAY_ALLOW_ALL_USERS":
                    return False
    except OSError:
        return False
    return True


def _windows_workspace_private(path: Path) -> bool:
    script = r"""
$ErrorActionPreference = 'Stop'
$item = Get-Item -LiteralPath $env:BWA_WORKSPACE -Force
if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { 'false'; exit }
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowed = @($current,'S-1-5-18','S-1-5-32-544')
$acl = Get-Acl -LiteralPath $env:BWA_WORKSPACE
$owner = $acl.Owner
try { $owner = ([Security.Principal.NTAccount]$acl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
if ($allowed -notcontains $owner) { 'false'; exit }
foreach ($entry in $acl.Access) {
  if ($entry.AccessControlType -ne 'Allow') { continue }
  $sid = $entry.IdentityReference.Value
  try { $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
  if ($allowed -notcontains $sid) { 'false'; exit }
}
'true'
"""
    env = os.environ.copy()
    env["BWA_WORKSPACE"] = str(path)
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


def workspace_is_safe(raw: Any) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return False
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except (OSError, RuntimeError):
        return False
    if absolute != resolved or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    if resolved == Path(resolved.anchor):
        return False
    try:
        home = Path.home().resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    documents = home / "Documents"
    if resolved == home:
        return False
    if resolved == documents or documents in resolved.parents:
        return False
    lowered_parts = tuple(part.casefold() for part in resolved.parts)
    if any(marker in part for marker in SYNC_MARKERS for part in lowered_parts):
        return False
    if os.name == "nt":
        return _windows_workspace_private(resolved)
    return info.st_uid == os.geteuid() and not (stat.S_IMODE(info.st_mode) & 0o077)


def parse_tool_inventory(text: str) -> tuple[set[str], set[str], bool, bool]:
    """返回 (列出的工具集, 启用项, 是否完整解析, 是否出现 MCP 配置节)。"""
    listed: set[str] = set()
    enabled: set[str] = set()
    parsed = True
    section: str | None = None
    mcp_section = False
    for raw in ANSI_RE.sub("", text).splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Built-in toolsets ("):
            section = "tools"
            continue
        if stripped.startswith("Plugin toolsets ("):
            section = "tools"
            continue
        if stripped == "MCP servers:":
            section = "mcp"
            mcp_section = True
            continue
        if section == "tools":
            match = TOOL_LINE_RE.match(line)
            if not match:
                parsed = False
                continue
            state, name = match.groups()
            if name in listed:
                parsed = False
            listed.add(name)
            if state == "enabled":
                enabled.add(name)
    return listed, enabled, parsed, mcp_section


def service_absent_and_gateway_stopped(profile: str, hermes: str) -> bool:
    """只接受官方无服务定义且无手工进程的精确状态行。"""
    result = _run([hermes, "-p", profile, "gateway", "status"])
    if result.returncode != 0:
        return False
    lines = {
        ANSI_RE.sub("", line).strip().lstrip("✓✗").strip().lower()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
    }
    return "gateway is not running" in lines


def _tool_surface(profile: str, hermes: str, platform: str) -> tuple[bool, bool, bool]:
    result = _run([hermes, "-p", profile, "tools", "list", "--platform", platform])
    listed, enabled, parsed, mcp_section = parse_tool_inventory(f"{result.stdout}\n{result.stderr}")
    inventory_complete = result.returncode == 0 and parsed and "clarify" in listed
    return inventory_complete, enabled == SAFE_ENABLED_TOOLSETS, not mcp_section


def evaluate(profile: str, hermes: str, expected_root: Path) -> dict[str, bool]:
    profile_valid, config_path, env_path = _profile_contract(profile, hermes, expected_root)
    cli_inventory_complete, cli_only_clarify, cli_mcp_absent = _tool_surface(
        profile, hermes, "cli"
    )
    weixin_inventory_complete, weixin_only_clarify, weixin_mcp_absent = _tool_surface(
        profile, hermes, "weixin"
    )
    platform_toolsets = _config_json(profile, hermes, "platform_toolsets.weixin")
    top_toolsets = _config_json(profile, hermes, "toolsets")
    approval_mode = _config_json(profile, hermes, "approvals.mode")
    cli_reasoning = _config_json(profile, hermes, "display.show_reasoning")
    reasoning = _config_json(profile, hermes, "display.platforms.weixin.show_reasoning")
    memory_enabled = _config_json(profile, hermes, "memory.memory_enabled")
    user_profile_enabled = _config_json(profile, hermes, "memory.user_profile_enabled")
    workspace = _config_json(profile, hermes, "terminal.cwd")

    platform_list_valid = isinstance(platform_toolsets, list) and all(
        isinstance(item, str) for item in platform_toolsets
    )
    platform_set = set(platform_toolsets) if platform_list_valid else set()
    top_set = set(top_toolsets) if isinstance(top_toolsets, list) else set()
    # tools list 只显示 MCP 服务器名和过滤摘要，不输出连接参数或环境值；
    # 不调用 config get mcp_servers，避免把潜在秘密读入检查器内存。
    kanban_disabled = "kanban" not in top_set and not os.environ.get("HERMES_KANBAN_TASK", "").strip()
    process_weixin_overrides_absent = not any(
        (key == "GATEWAY_ALLOW_ALL_USERS" or key.startswith("WEIXIN_")) and str(value).strip()
        for key, value in os.environ.items()
    )
    try:
        sensitive_paths = collect_sensitive_paths(config_path, env_path)
    except SafetyCheckError as exc:
        raise PreQrError("secret_store_unreadable") from exc
    secret_stores_private = (
        windows_permissions_private(sensitive_paths)
        if os.name == "nt"
        else posix_permissions_private(sensitive_paths)
    )
    return {
        "profile_cli_contract_valid": profile_valid,
        "weixin_state_absent_before_qr": env_has_no_weixin_state(env_path),
        "process_weixin_overrides_absent_before_qr": process_weixin_overrides_absent,
        "dedicated_workspace_private": workspace_is_safe(workspace),
        "model_and_profile_secret_stores_private": secret_stores_private,
        "approval_mode_safe": approval_mode in {"manual", "smart"},
        "cli_tool_inventory_complete": cli_inventory_complete,
        "cli_only_clarify_enabled": cli_only_clarify,
        "cli_mcp_servers_absent": cli_mcp_absent,
        "weixin_tool_inventory_complete": weixin_inventory_complete,
        "weixin_only_clarify_enabled": weixin_only_clarify,
        "weixin_platform_toolsets_minimal": (
            platform_list_valid
            and len(platform_toolsets) == len(platform_set)
            and any(platform_set == allowed for allowed in SAFE_PLATFORM_TOOLSET_FORMS)
        ),
        "weixin_mcp_servers_absent": weixin_mcp_absent,
        "kanban_runtime_disabled": kanban_disabled,
        "cli_reasoning_disabled": cli_reasoning is False,
        "weixin_reasoning_disabled": reasoning is False,
        "builtin_memory_disabled": memory_enabled is False,
        "user_profile_injection_disabled": user_profile_enabled is False,
        "service_absent_and_gateway_stopped_before_qr": service_absent_and_gateway_stopped(profile, hermes),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="不输出秘密值的模型调用与 Weixin 扫码前安全检查")
    parser.add_argument("--profile", required=True, help="非 default 的 Hermes Profile 名称")
    parser.add_argument("--hermes", default="hermes", help="Hermes 启动器；默认使用 PATH 中的 hermes")
    parser.add_argument("--expected-hermes-root", required=True, help="本轮已批准的 Hermes 根绝对路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        expected_root = Path(args.expected_hermes_root)
        if not expected_root.is_absolute() or expected_root.is_symlink() or not expected_root.is_dir():
            raise PreQrError("expected_root_invalid")
        checks = evaluate(args.profile, args.hermes, expected_root)
    except PreQrError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

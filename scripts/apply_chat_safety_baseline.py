#!/usr/bin/env python3
"""在模型调用前及探测后把 CLI、Weixin 与 Profile 权限收缩到最小边界。"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_pre_qr_safety import (  # noqa: E402
    PROFILE_RE,
    evaluate,
    parse_tool_inventory,
    workspace_is_safe,
)


PLATFORMS = ("cli", "weixin")
CONFIG_BASELINE = (
    ("approvals.mode", "smart"),
    ("display.language", "zh"),
    ("display.show_reasoning", "false"),
    ("display.platforms.weixin.show_reasoning", "false"),
    ("display.tool_progress", "off"),
    ("display.platforms.weixin.tool_progress", "off"),
    ("display.interim_assistant_messages", "false"),
    ("display.platforms.weixin.interim_assistant_messages", "false"),
    ("display.long_running_notifications", "false"),
    ("display.platforms.weixin.long_running_notifications", "false"),
    ("display.busy_ack_detail", "false"),
    ("display.platforms.weixin.busy_ack_detail", "false"),
    ("display.background_process_notifications", "off"),
    ("session_reset.notify", "false"),
    ("memory.memory_enabled", "false"),
    ("memory.user_profile_enabled", "false"),
)


class BaselineError(RuntimeError):
    """固定错误码；不得携带路径、命令输出或配置值。"""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineError("hermes_unavailable") from exc


def _inventory(profile: str, hermes: str, platform: str) -> set[str]:
    result = _run([hermes, "-p", profile, "tools", "list", "--platform", platform])
    listed, _, parsed, mcp_section = parse_tool_inventory(f"{result.stdout}\n{result.stderr}")
    if result.returncode != 0 or not parsed or "clarify" not in listed:
        raise BaselineError("tool_inventory_unreadable")
    if mcp_section:
        raise BaselineError("mcp_present")
    return listed


def _single_path(command: list[str]) -> Path | None:
    result = _run(command)
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        return None
    return Path(output).expanduser()


def _profile_binding_valid(profile: str, hermes: str, expected_root: Path) -> bool:
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        return False
    if _run([hermes, "profile", "show", profile]).returncode != 0:
        return False
    config = _single_path([hermes, "-p", profile, "config", "path"])
    env = _single_path([hermes, "-p", profile, "config", "env-path"])
    if config is None or env is None:
        return False
    profile_dir = expected_root / "profiles" / profile
    try:
        directory_info = profile_dir.lstat()
        env_info = env.lstat()
        config_safe = not config.exists() and not config.is_symlink()
        if config.exists() or config.is_symlink():
            config_info = config.lstat()
            config_safe = stat.S_ISREG(config_info.st_mode) and not config.is_symlink()
    except OSError:
        return False
    return (
        config == profile_dir / "config.yaml"
        and env == profile_dir / ".env"
        and stat.S_ISDIR(directory_info.st_mode)
        and not profile_dir.is_symlink()
        and stat.S_ISREG(env_info.st_mode)
        and not env.is_symlink()
        and config_safe
    )


def _require_success(command: list[str]) -> None:
    if _run(command).returncode != 0:
        raise BaselineError("baseline_write_failed")


def _harden_profile_runtime_permissions(profile: str, expected_root: Path) -> None:
    """收紧模型探测可能新建的 Profile 内缓存；不越出已批准 Profile。"""
    if os.name == "nt":
        return
    profile_dir = expected_root / "profiles" / profile
    pending = [profile_dir, profile_dir / "config.yaml", profile_dir / ".env"]
    pending.extend(
        path
        for path in (profile_dir / "cache", profile_dir / "weixin", profile_dir / "auth.json")
        if path.exists() or path.is_symlink()
    )
    seen: set[Path] = set()
    uid = os.geteuid()
    try:
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != uid:
                raise BaselineError("runtime_permission_target_unsafe")
            if stat.S_ISDIR(info.st_mode):
                path.chmod(0o700)
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(info.st_mode):
                path.chmod(0o600)
            else:
                raise BaselineError("runtime_permission_target_unsafe")
    except BaselineError:
        raise
    except OSError as exc:
        raise BaselineError("runtime_permissions_hardening_failed") from exc


def apply(profile: str, hermes: str, expected_root: Path, workspace: Path) -> dict[str, bool]:
    if not _profile_binding_valid(profile, hermes, expected_root):
        raise BaselineError("profile_path_mismatch")
    if not workspace_is_safe(str(workspace)):
        raise BaselineError("workspace_unsafe")

    inventories = {platform: _inventory(profile, hermes, platform) for platform in PLATFORMS}
    for platform, listed in inventories.items():
        disable = sorted(listed - {"clarify"})
        if disable:
            _require_success(
                [hermes, "-p", profile, "tools", "disable", "--platform", platform, *disable]
            )
        _require_success(
            [hermes, "-p", profile, "tools", "enable", "--platform", platform, "clarify"]
        )

    _require_success([hermes, "-p", profile, "config", "set", "terminal.cwd", str(workspace)])
    for key, value in CONFIG_BASELINE:
        _require_success([hermes, "-p", profile, "config", "set", key, value])

    _harden_profile_runtime_permissions(profile, expected_root)
    checks = evaluate(profile, hermes, expected_root)
    if not all(checks.values()):
        raise BaselineError("baseline_recheck_failed")
    return checks


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="应用并复验模型调用前及探测后的最小聊天边界")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--hermes", required=True)
    parser.add_argument("--expected-hermes-root", required=True)
    parser.add_argument("--workspace", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        root = Path(args.expected_hermes_root)
        workspace = Path(args.workspace)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise BaselineError("expected_root_invalid")
        checks = apply(args.profile, args.hermes, root, workspace)
    except BaselineError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    print(json.dumps({"result": "PASS", "checks": checks, "secrets_printed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

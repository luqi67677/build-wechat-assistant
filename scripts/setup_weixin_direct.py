#!/usr/bin/env python3
"""直达 Hermes Weixin 扫码；用户只扫码，不选择终端菜单。"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

import isolation_guard as guard


PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
OFFICIAL_BASE_URL = "https://ilinkai.weixin.qq.com"
OFFICIAL_CDN_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
WEIXIN_KEYS = (
    "WEIXIN_ACCOUNT_ID",
    "WEIXIN_TOKEN",
    "WEIXIN_BASE_URL",
    "WEIXIN_CDN_BASE_URL",
    "WEIXIN_DM_POLICY",
    "WEIXIN_ALLOW_ALL_USERS",
    "WEIXIN_ALLOWED_USERS",
    "WEIXIN_GROUP_POLICY",
    "WEIXIN_GROUP_ALLOWED_USERS",
    "WEIXIN_HOME_CHANNEL",
)


class SetupError(RuntimeError):
    """只携带固定错误码，不携带路径、账号或秘密。"""


class RedactingWriter:
    """保留终端二维码，隐藏短时 URL 与成功回执中的账号 ID。"""

    def __init__(self, target: TextIO):
        self.target = target

    def write(self, value: str) -> int:
        original_length = len(value)
        value = re.sub(r"https?://[^\s\x1b]+", "[短时登录地址已隐藏]", value)
        if "account_id=" in value:
            value = re.sub(r"微信连接成功，account_id=.*", "微信扫码确认成功。", value)
        self.target.write(value)
        return original_length

    def flush(self) -> None:
        self.target.flush()

    def isatty(self) -> bool:
        return self.target.isatty()

    @property
    def encoding(self) -> str | None:
        return getattr(self.target, "encoding", None)


def _private_path(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return bool(
        expected
        and not stat.S_ISLNK(info.st_mode)
        and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o077)
    )


def _read_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() in WEIXIN_KEYS and value.strip():
                    keys.add(key.strip())
    except (OSError, UnicodeError) as exc:
        raise SetupError("profile_env_unreadable") from exc
    return keys


def _run_path(command: list[str], env: dict[str, str]) -> Path:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("hermes_unavailable") from exc
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        raise SetupError("profile_path_unresolved")
    return Path(output)


def _ordinary_environment(launcher: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not guard._secret_shaped(key)
        and key not in {"HERMES_HOME", "HERMES_PROFILE", "HERMES_CONFIG", "HERMES_ENV"}
    }
    env["PATH"] = os.pathsep.join(dict.fromkeys((str(launcher.parent), env.get("PATH", ""))))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def resolve_profile(args: argparse.Namespace) -> tuple[Path, Path, dict[str, str]]:
    profile = str(args.profile or "")
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise SetupError("profile_invalid")
    try:
        launcher = guard._validated_launcher(args.hermes)
    except guard.IsolationError as exc:
        raise SetupError(str(exc)) from exc

    if args.mode == "ordinary":
        env = _ordinary_environment(launcher)
        expected_root = None
    else:
        try:
            root = guard.validate_root(args.root)
            action = "run" if args.mode == "protected" else "run-cloud"
            guard.validate_interactive_root(root, action)
            env = guard.interactive_environment_for_root(root, str(launcher))
        except guard.IsolationError as exc:
            raise SetupError(str(exc)) from exc
        expected_root = root

    config_path = _run_path([str(launcher), "-p", profile, "config", "path"], env)
    env_path = _run_path([str(launcher), "-p", profile, "config", "env-path"], env)
    profile_dir = env_path.parent
    if (
        config_path != profile_dir / "config.yaml"
        or env_path != profile_dir / ".env"
        or profile_dir.name != profile
        or profile_dir.parent.name != "profiles"
        or (expected_root is not None and profile_dir != expected_root / "profiles" / profile)
        or not _private_path(profile_dir, directory=True)
        or not _private_path(env_path, directory=False)
        or (config_path.exists() and not _private_path(config_path, directory=False))
    ):
        raise SetupError("profile_path_mismatch")
    if _read_env_keys(env_path):
        raise SetupError("weixin_state_already_present")

    try:
        status = subprocess.run(
            [str(launcher), "-p", profile, "gateway", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("gateway_status_unavailable") from exc
    status_lines = {
        guard.ANSI_RE.sub("", line).strip().lstrip("✓✗").strip().lower()
        for line in f"{status.stdout}\n{status.stderr}".splitlines()
    }
    if status.returncode != 0 or "gateway is not running" not in status_lines:
        raise SetupError("gateway_must_be_stopped")

    profile_root = profile_dir.parent.parent
    env["HERMES_HOME"] = str(profile_dir)
    env["HERMES_SHARED_AUTH_DIR"] = str(profile_root / "shared")
    return launcher, profile_dir, env


def resolve_runtime_python(launcher: Path) -> Path:
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend((launcher.parent / "python.exe", launcher.parent / "python3.exe"))
    try:
        text = launcher.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    if text:
        first = text.splitlines()[0] if text.splitlines() else ""
        if first.startswith("#!") and "python" in first.casefold():
            candidates.append(Path(first[2:].strip().split()[0]))
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("exec "):
                continue
            try:
                parts = shlex.split(stripped)
            except ValueError:
                continue
            if len(parts) >= 3 and parts[0] == "exec":
                candidates.append(Path(parts[1]))
    for candidate in candidates:
        candidate = candidate.expanduser()
        if not candidate.is_absolute() or "python" not in candidate.name.casefold():
            continue
        try:
            candidate_info = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            resolved_info = resolved.lstat()
        except (OSError, RuntimeError):
            continue
        if not stat.S_ISREG(resolved_info.st_mode) or stat.S_ISLNK(resolved_info.st_mode) or not os.access(candidate, os.X_OK):
            continue
        if stat.S_ISLNK(candidate_info.st_mode):
            marker = candidate.parent.parent / "pyvenv.cfg"
            try:
                marker_info = marker.lstat()
            except OSError:
                continue
            owned = not hasattr(os, "geteuid") or (
                candidate_info.st_uid == os.geteuid() and marker_info.st_uid == os.geteuid()
            )
            if stat.S_ISREG(marker_info.st_mode) and not stat.S_ISLNK(marker_info.st_mode) and owned:
                return candidate.parent.resolve(strict=True) / candidate.name
            continue
        if stat.S_ISREG(candidate_info.st_mode):
            return resolved
    raise SetupError("hermes_runtime_python_unresolved")


def validate_credentials(credentials: object) -> dict[str, str]:
    if not isinstance(credentials, dict):
        raise SetupError("weixin_login_incomplete")
    values = {
        key: str(credentials.get(key) or "").strip()
        for key in ("account_id", "token", "base_url", "user_id")
    }
    if any("\n" in value or "\r" in value or "\0" in value for value in values.values()):
        raise SetupError("weixin_login_incomplete")
    if not values["account_id"] or not values["token"] or not values["user_id"]:
        raise SetupError("weixin_login_incomplete")
    if values["base_url"] and values["base_url"].rstrip("/") != OFFICIAL_BASE_URL:
        raise SetupError("weixin_endpoint_unexpected")
    values["base_url"] = OFFICIAL_BASE_URL
    return values


def save_safe_configuration(credentials: dict[str, str], save: Callable[[str, str], object]) -> None:
    owner = credentials["user_id"]
    values = {
        "WEIXIN_ACCOUNT_ID": credentials["account_id"],
        "WEIXIN_TOKEN": credentials["token"],
        "WEIXIN_BASE_URL": credentials["base_url"],
        "WEIXIN_CDN_BASE_URL": OFFICIAL_CDN_URL,
        "WEIXIN_DM_POLICY": "allowlist",
        "WEIXIN_ALLOW_ALL_USERS": "false",
        "WEIXIN_ALLOWED_USERS": owner,
        "WEIXIN_GROUP_POLICY": "disabled",
        "WEIXIN_GROUP_ALLOWED_USERS": "",
        "WEIXIN_HOME_CHANNEL": owner,
    }
    for key in WEIXIN_KEYS:
        save(key, values[key])


def reharden_weixin_store(profile_dir: Path) -> None:
    """扫码后只收紧当前 Profile 内 SDK 新建的 Weixin 状态。"""
    if os.name == "nt":
        return
    root = profile_dir / "weixin"
    if not root.exists() and not root.is_symlink():
        return
    pending = [root]
    paths: list[tuple[Path, int]] = []
    while pending:
        path = pending.pop()
        try:
            info = path.lstat()
        except OSError as exc:
            raise SetupError("weixin_store_unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or (hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
            raise SetupError("weixin_store_unsafe")
        if stat.S_ISDIR(info.st_mode):
            paths.append((path, 0o700))
            try:
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            except OSError as exc:
                raise SetupError("weixin_store_unreadable") from exc
        elif stat.S_ISREG(info.st_mode):
            paths.append((path, 0o600))
        else:
            raise SetupError("weixin_store_unsafe")
    try:
        for path, mode in paths:
            path.chmod(mode)
    except OSError as exc:
        raise SetupError("weixin_store_permissions_failed") from exc


def probe_runtime() -> None:
    try:
        import qrcode  # noqa: F401
        from gateway.platforms.weixin import check_weixin_requirements, qr_login
        from hermes_cli.gateway import save_env_value
    except Exception as exc:
        raise SetupError("weixin_runtime_unavailable") from exc
    if not callable(check_weixin_requirements) or not callable(qr_login) or not callable(save_env_value):
        raise SetupError("weixin_runtime_unavailable")
    if not check_weixin_requirements():
        raise SetupError("weixin_dependencies_missing")


def inner_setup(profile_dir: Path) -> None:
    try:
        active_home = Path(os.environ["HERMES_HOME"]).resolve(strict=True)
        expected = profile_dir.resolve(strict=True)
    except (KeyError, OSError, RuntimeError) as exc:
        raise SetupError("profile_runtime_mismatch") from exc
    if active_home != expected or not _private_path(expected, directory=True):
        raise SetupError("profile_runtime_mismatch")
    probe_runtime()
    from gateway.platforms.weixin import qr_login
    from hermes_cli.gateway import save_env_value

    print("请用微信扫描下面的二维码；不需要在窗口里选择任何菜单。")
    # stdout 与 stderr 都必须脱敏：qr_login 若向 stderr 打印短时 URL，
    # 只包装 stdout 会让凭据绕过 RedactingWriter。
    with (
        contextlib.redirect_stdout(RedactingWriter(sys.stdout)),
        contextlib.redirect_stderr(RedactingWriter(sys.stderr)),
    ):
        credentials = asyncio.run(qr_login(str(expected)))
    safe_credentials = validate_credentials(credentials)
    reharden_weixin_store(expected)
    save_safe_configuration(safe_credentials, save_env_value)
    print("微信连接与安全权限已自动保存。请回到对话，等待智能体完成检查。")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直达 Weixin 扫码，不显示全平台菜单")
    parser.add_argument("action", choices=("plan", "run", "_probe", "_inner"))
    parser.add_argument("--mode", choices=("ordinary", "protected", "cloud"))
    parser.add_argument("--profile")
    parser.add_argument("--hermes")
    parser.add_argument("--root")
    parser.add_argument("--profile-dir")
    return parser.parse_args(argv)


def _error_message(code: str) -> str:
    messages = {
        "trusted_tty_required": "当前没有可安全显示二维码的系统窗口，请保持微信未连接。",
        "weixin_state_already_present": "目标助手已经存在微信状态，未覆盖；请先确认是否需要重新授权。",
        "gateway_must_be_stopped": "目标微信服务仍在运行或状态不明确，已停止扫码流程。",
        "weixin_runtime_unavailable": "当前 Hermes 缺少可用的微信扫码组件。",
        "weixin_dependencies_missing": "当前 Hermes 的微信扫码依赖不完整。",
        "weixin_login_incomplete": "微信扫码未完成，请回到对话重新开始。",
        "weixin_endpoint_unexpected": "微信返回的服务地址不在已核验范围，已停止保存配置。",
    }
    return messages.get(code, "微信连接未完成，目标配置保持未启动，请回到对话查看下一步。")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.action == "_probe":
            probe_runtime()
            return 0
        if args.action == "_inner":
            if not guard.trusted_tty_available() or not args.profile_dir:
                raise SetupError("trusted_tty_required")
            inner_setup(Path(args.profile_dir))
            return 0
        if not args.mode or not args.profile or not args.hermes:
            raise SetupError("arguments_invalid")
        if args.action == "run" and not guard.trusted_tty_available():
            raise SetupError("trusted_tty_required")
        launcher, profile_dir, env = resolve_profile(args)
        runtime_python = resolve_runtime_python(launcher)
        command = [
            str(runtime_python),
            str(Path(__file__).resolve()),
            "_probe" if args.action == "plan" else "_inner",
        ]
        if args.action == "run":
            command.extend(("--profile-dir", str(profile_dir)))
        result = guard._private_subprocess_run(
            command,
            capture_output=args.action == "plan",
            text=args.action == "plan",
            timeout=30 if args.action == "plan" else None,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise SetupError("weixin_runtime_unavailable")
        if args.action == "plan":
            print(json.dumps({"result": "READY", "secrets_printed": False}, sort_keys=True))
        return 0
    except (SetupError, guard.IsolationError) as exc:
        code = str(exc)
        print(_error_message(code))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""为严格验收创建并绑定全新的 Hermes 根；不读取现有 Hermes 根。"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MARKER_NAME = ".build-wechat-assistant-isolated-root.json"
ROOT_PURPOSES = frozenset({"local-test", "local-persistent", "cloud-service"})
GLOBAL_READ_ONLY_COMMANDS = frozenset({("--help",), ("--version",)})
ALLOWED_CHECKERS = frozenset(
    {
        "apply_chat_safety_baseline.py",
        "check_pre_qr_safety.py",
        "check_profile_safety.py",
        "set_profile_env_key.py",
    }
)
SERVICE_GATEWAY_SUFFIXES = {
    "install": frozenset(
        {
            ("--no-start-now", "--no-start-on-login"),
            ("--force", "--no-start-now", "--start-on-login"),
            ("--help",),
        }
    ),
    "start": frozenset({()}),
    "stop": frozenset({()}),
    "restart": frozenset({()}),
    "status": frozenset({("--deep",)}),
    "uninstall": frozenset({(), ("--help",)}),
}
SAFE_PASSTHROUGH_KEYS = frozenset(
    {
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "TERM",
        "COLORTERM",
        "TZ",
    }
)
KNOWN_RUNTIME_HOME_AUTH_PATHS = (
    (".qwen", "oauth_creds.json"),
    (".claude", ".credentials.json"),
    (".codex", "auth.json"),
    (".config", "gh", "hosts.yml"),
)


class IsolationError(RuntimeError):
    """只携带固定错误码，不携带路径、命令输出或秘密。"""


def _private_subprocess_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    previous_umask = os.umask(0o077) if os.name != "nt" else None
    try:
        return subprocess.run(command, **kwargs)
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)


def _private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return False
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return False
    return os.name == "nt" or not (stat.S_IMODE(info.st_mode) & 0o077)


def _private_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return False
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return False
    return os.name == "nt" or not (stat.S_IMODE(info.st_mode) & 0o077)


def _owned_directory_not_writable(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and path.resolve(strict=True) == path
        and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o022)
    )


def _service_account_home() -> Path:
    if os.name == "nt":
        home = Path.home()
    else:
        try:
            import pwd

            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        except (ImportError, KeyError, OSError) as exc:
            raise IsolationError("service_home_unavailable") from exc
    try:
        return home.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IsolationError("service_home_unavailable") from exc


def _service_runtime_environment() -> dict[str, str]:
    if not hasattr(os, "geteuid"):
        raise IsolationError("service_user_bus_unavailable")
    uid = os.geteuid()
    runtime = Path("/run/user") / str(uid)
    bus = runtime / "bus"
    try:
        runtime_info = runtime.lstat()
        bus_info = bus.lstat()
    except OSError as exc:
        raise IsolationError("service_user_bus_unavailable") from exc
    if (
        not stat.S_ISDIR(runtime_info.st_mode)
        or stat.S_ISLNK(runtime_info.st_mode)
        or runtime_info.st_uid != uid
        or stat.S_IMODE(runtime_info.st_mode) & 0o077
        or not stat.S_ISSOCK(bus_info.st_mode)
        or stat.S_ISLNK(bus_info.st_mode)
        or bus_info.st_uid != uid
    ):
        raise IsolationError("service_user_bus_unavailable")
    return {
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
    }


def _absolute_unresolved(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise IsolationError("root_path_invalid")
    return path


def _path_within(path: Path, scope: Path) -> bool:
    try:
        path.relative_to(scope)
        return True
    except ValueError:
        return False


def _temporary_roots() -> tuple[Path, ...]:
    candidates = (Path(tempfile.gettempdir()), Path("/tmp"), Path("/private/tmp"))
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _local_persistent_scope() -> Path:
    home = _service_account_home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "build-wechat-assistant" / "isolated-roots"
    if os.name == "nt":
        return home / "AppData" / "Local" / "build-wechat-assistant" / "isolated-roots"
    return home / ".local" / "state" / "build-wechat-assistant" / "isolated-roots"


def _prepare_local_persistent_scope(parent: Path) -> None:
    expected = _local_persistent_scope()
    if parent != expected:
        raise IsolationError("local_persistent_root_outside_private_scope")
    try:
        expected.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise IsolationError("local_persistent_scope_unavailable") from exc
    if not _private_directory(expected):
        raise IsolationError("local_persistent_scope_unsafe")


def _validate_new_root_scope(root: Path, purpose: str) -> Path:
    if purpose not in ROOT_PURPOSES:
        raise IsolationError("root_purpose_invalid")
    try:
        parent = root.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IsolationError("root_parent_invalid") from exc
    if purpose == "local-test":
        if not any(_path_within(parent, scope) for scope in _temporary_roots()):
            raise IsolationError("local_test_root_outside_temporary_scope")
    elif purpose == "local-persistent":
        if parent != _local_persistent_scope() or not _private_directory(parent):
            raise IsolationError("local_persistent_root_outside_private_scope")
    else:
        home = _service_account_home()
        if parent != home:
            raise IsolationError("cloud_root_must_be_direct_child_of_service_home")
        if not _owned_directory_not_writable(home):
            raise IsolationError("service_home_unsafe")
    return parent


def create_root(raw_root: str, purpose: str = "local-test") -> dict[str, bool]:
    root = _absolute_unresolved(raw_root)
    if root.exists() or root.is_symlink():
        raise IsolationError("root_must_not_exist")
    parent = root.parent
    if purpose == "local-persistent":
        _prepare_local_persistent_scope(parent)
    try:
        parent_info = parent.lstat()
        parent_resolved = parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IsolationError("root_parent_invalid") from exc
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise IsolationError("root_parent_invalid")
    if parent_resolved != parent:
        raise IsolationError("root_parent_invalid")
    scope_root = _validate_new_root_scope(root, purpose)
    try:
        root.mkdir(mode=0o700)
        os_home = root / "os-home"
        shared = root / "shared"
        os_home.mkdir(mode=0o700)
        shared.mkdir(mode=0o700)
        info = root.lstat()
        marker = {
            "schema_version": 2,
            "purpose": purpose,
            "scope_root": str(scope_root),
            "root_device": info.st_dev,
            "root_inode": info.st_ino,
            "owner_uid": getattr(os, "geteuid", lambda: -1)(),
            "nonce": secrets.token_hex(16),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(root / MARKER_NAME, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(marker, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise IsolationError("root_create_failed_manual_review_required") from exc
    return root_checks(root)


def root_checks(root: Path) -> dict[str, bool]:
    marker_path = root / MARKER_NAME
    marker: object = None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        root_info = root.lstat()
        resolved_matches = root.resolve(strict=True) == root
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError):
        root_info = None
        resolved_matches = False
    marker_valid = (
        isinstance(marker, dict)
        and marker.get("schema_version") == 2
        and marker.get("purpose") in ROOT_PURPOSES
        and marker.get("scope_root") == str(root.parent)
    )
    binding_matches = bool(
        marker_valid
        and root_info is not None
        and marker.get("root_device") == root_info.st_dev
        and marker.get("root_inode") == root_info.st_ino
        and marker.get("owner_uid") == getattr(os, "geteuid", lambda: -1)()
        and isinstance(marker.get("nonce"), str)
        and len(marker["nonce"]) == 32
    )
    return {
        "isolated_root_is_private_directory": _private_directory(root),
        "isolated_root_has_no_symlink_resolution": resolved_matches,
        "isolated_root_marker_is_private_regular_file": _private_file(marker_path),
        "isolated_root_marker_binds_current_directory": binding_matches,
        "isolated_root_purpose_and_scope_are_bound": marker_valid,
        "isolated_os_home_is_private_directory": _private_directory(root / "os-home"),
        "isolated_shared_auth_is_private_directory": _private_directory(root / "shared"),
    }


def validate_root(raw_root: str) -> Path:
    root = _absolute_unresolved(raw_root)
    checks = root_checks(root)
    if not all(checks.values()):
        raise IsolationError("isolated_root_invalid")
    return root


def root_purpose(root: Path) -> str:
    try:
        marker = json.loads((root / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolationError("isolated_root_invalid") from exc
    purpose = marker.get("purpose") if isinstance(marker, dict) else None
    if purpose not in ROOT_PURPOSES:
        raise IsolationError("isolated_root_invalid")
    return str(purpose)


def validate_cloud_service_root(root: Path) -> None:
    if root_purpose(root) != "cloud-service":
        raise IsolationError("service_runner_requires_cloud_service_root")
    home = _service_account_home()
    if root.parent != home or not _owned_directory_not_writable(home):
        raise IsolationError("cloud_service_root_scope_drift")


def _validated_launcher(hermes: str) -> Path:
    launcher = Path(hermes)
    if not launcher.is_absolute():
        raise IsolationError("hermes_launcher_not_absolute")
    try:
        launcher = launcher.resolve(strict=True)
        launcher_info = launcher.lstat()
    except OSError as exc:
        raise IsolationError("hermes_launcher_invalid") from exc
    if not stat.S_ISREG(launcher_info.st_mode) or not os.access(launcher, os.X_OK):
        raise IsolationError("hermes_launcher_invalid")
    return launcher


def _base_environment(launcher: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in SAFE_PASSTHROUGH_KEYS or key.startswith("LC_"):
            env[key] = value
    env["PATH"] = os.pathsep.join(dict.fromkeys((str(launcher.parent), "/usr/bin", "/bin")))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def isolated_environment(root: Path, hermes: str) -> dict[str, str]:
    launcher = _validated_launcher(hermes)
    env = _base_environment(launcher)
    env.update(
        {
            "HOME": str(root / "os-home"),
            "HERMES_HOME": str(root),
            "HERMES_SHARED_AUTH_DIR": str(root / "shared"),
            "XDG_CONFIG_HOME": str(root / "os-home" / ".config"),
            "XDG_CACHE_HOME": str(root / "os-home" / ".cache"),
            "XDG_DATA_HOME": str(root / "os-home" / ".local" / "share"),
            "TMPDIR": str(root / "os-home"),
        }
    )
    return env


def service_environment(root: Path, hermes: str) -> dict[str, str]:
    """保留新服务账号的真实 HOME，但不继承任何凭据来源。"""
    if not sys.platform.startswith("linux"):
        raise IsolationError("service_runner_requires_linux")
    launcher = _validated_launcher(hermes)
    validate_cloud_service_root(root)
    home = _service_account_home()
    if not _owned_directory_not_writable(home):
        raise IsolationError("service_home_unsafe")
    env = _base_environment(launcher)
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(root),
            "HERMES_SHARED_AUTH_DIR": str(root / "shared"),
            "TMPDIR": str(root / "os-home"),
        }
    )
    env.update(_service_runtime_environment())
    return env


def cloud_interactive_environment(root: Path, hermes: str) -> dict[str, str]:
    """让云端配置/认证与最终 gateway 使用同一个服务账号 HOME。"""
    if not sys.platform.startswith("linux"):
        raise IsolationError("cloud_runner_requires_linux")
    launcher = _validated_launcher(hermes)
    validate_cloud_service_root(root)
    home = _service_account_home()
    if not _owned_directory_not_writable(home):
        raise IsolationError("service_home_unsafe")
    env = _base_environment(launcher)
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(root),
            "HERMES_SHARED_AUTH_DIR": str(root / "shared"),
            "TMPDIR": str(root / "os-home"),
        }
    )
    return env


def validate_interactive_root(root: Path, action: str) -> None:
    purpose = root_purpose(root)
    if action == "run" and purpose not in {"local-test", "local-persistent"}:
        raise IsolationError("local_runner_requires_local_test_root")
    if action == "run-cloud" and purpose != "cloud-service":
        raise IsolationError("cloud_runner_requires_cloud_service_root")


def trusted_tty_available() -> bool:
    try:
        return all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
    except (AttributeError, OSError):
        return False


def sensitive_interactive_command(command: list[str]) -> bool:
    if len(command) < 3 or command[0] not in {"-p", "--profile"}:
        return False
    tail = command[2:]
    return (
        tail[:1] == ["model"]
        or tail[:2] == ["gateway", "setup"]
        or tail[:2] == ["auth", "add"]
    )


def interactive_environment_for_root(root: Path, launcher: str) -> dict[str, str]:
    purpose = root_purpose(root)
    if purpose in {"local-test", "local-persistent"}:
        return isolated_environment(root, launcher)
    if purpose == "cloud-service":
        return cloud_interactive_environment(root, launcher)
    raise IsolationError("isolated_root_invalid")


def qwen_auth_command(mode: str) -> list[str]:
    if mode == "help":
        return ["auth", "--help"]
    if mode == "login":
        return ["auth", "qwen-oauth"]
    raise IsolationError("qwen_auth_mode_invalid")


def validate_service_command(command: list[str]) -> None:
    if len(command) < 4 or command[0] != "-p" or not PROFILE_RE.fullmatch(command[1]):
        raise IsolationError("service_command_invalid")
    if command[1] == "default" or command[2] != "gateway":
        raise IsolationError("service_command_invalid")
    action = command[3]
    allowed_suffixes = SERVICE_GATEWAY_SUFFIXES.get(action)
    if allowed_suffixes is None or tuple(command[4:]) not in allowed_suffixes:
        raise IsolationError("service_command_invalid")


def is_persistent_service_command(command: list[str]) -> bool:
    actions = {"install", "start", "stop", "restart", "uninstall"}
    return any(
        command[index] == "gateway" and command[index + 1] in actions
        for index in range(max(0, len(command) - 1))
    )


def is_protected_foreground_stop(command: list[str]) -> bool:
    return bool(
        len(command) == 4
        and command[0] in {"-p", "--profile"}
        and PROFILE_RE.fullmatch(command[1])
        and command[1] != "default"
        and command[2:] == ["gateway", "stop"]
    )


def validate_isolated_command(command: list[str]) -> None:
    if tuple(command) in GLOBAL_READ_ONLY_COMMANDS:
        return
    if len(command) == 5 and command[:2] == ["profile", "create"]:
        profile = command[2]
        if (
            PROFILE_RE.fullmatch(profile)
            and profile != "default"
            and command[3:] == ["--no-alias", "--no-skills"]
        ):
            return
    if len(command) == 3 and command[:2] == ["profile", "show"]:
        if PROFILE_RE.fullmatch(command[2]) and command[2] != "default":
            return
    if len(command) >= 3 and command[0] in {"-p", "--profile"}:
        profile = command[1]
        if PROFILE_RE.fullmatch(profile) and profile != "default" and command[2] not in {
            "profile",
            "update",
            "uninstall",
        }:
            return
    raise IsolationError("isolated_command_requires_nondefault_profile")


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return _private_subprocess_run(
            command, capture_output=True, text=True, timeout=30, check=False, env=env
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationError("hermes_unavailable") from exc


def _single_path(result: subprocess.CompletedProcess[str]) -> Path | None:
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        return None
    return Path(output)


def _directory_empty(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True  # 目录尚不存在 = 空（全新根还未创建 sessions/memories）
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and next(path.iterdir(), None) is None


def _secret_shaped(key: str) -> bool:
    upper = key.upper()
    return (
        upper.startswith("WEIXIN_")
        or upper == "GATEWAY_ALLOW_ALL_USERS"
        or upper in {"FAL_KEY", "HF_TOKEN", "ANTHROPIC_TOKEN"}
        or upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
    )


def _env_has_no_nonempty_secret(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            for raw in stream:
                line = raw.strip()
                if not line or line.startswith(b"#") or b"=" not in line:
                    continue
                raw_key, _, raw_value = line.partition(b"=")
                try:
                    key = raw_key.strip().decode("ascii")
                except UnicodeDecodeError:
                    return False
                if ENV_KEY_RE.fullmatch(key) and _secret_shaped(key) and raw_value.strip().strip(b"'\""):
                    return False
    except OSError:
        return False
    return True


def _runtime_home_known_auth_sources_absent(raw_home: str) -> bool:
    home = Path(raw_home)
    if not home.is_absolute() or home.is_symlink() or not home.is_dir():
        return False
    return all(
        not (candidate := home.joinpath(*parts)).exists() and not candidate.is_symlink()
        for parts in KNOWN_RUNTIME_HOME_AUTH_PATHS
    )


def fresh_profile_checks(root: Path, profile: str, hermes: str) -> dict[str, bool]:
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise IsolationError("profile_invalid")
    launcher = str(_validated_launcher(hermes))
    env = (
        cloud_interactive_environment(root, launcher)
        if root_purpose(root) == "cloud-service"
        else isolated_environment(root, launcher)
    )
    shown = _run([launcher, "profile", "show", profile], env)
    config = _single_path(_run([launcher, "-p", profile, "config", "path"], env))
    profile_env = _single_path(_run([launcher, "-p", profile, "config", "env-path"], env))
    status = _run([launcher, "-p", profile, "gateway", "status", "--deep"], env)
    expected = root / "profiles" / profile
    exact_paths = config == expected / "config.yaml" and profile_env == expected / ".env"
    status_lines = {
        ANSI_RE.sub("", line).strip().lstrip("✓✗").strip().lower()
        for line in f"{status.stdout}\n{status.stderr}".splitlines()
    }
    auth_candidates = (root / "auth.json", root / "shared" / "nous_auth.json", expected / "auth.json")
    return {
        "fresh_profile_show_succeeds": shown.returncode == 0,
        "fresh_profile_paths_bind_isolated_root": exact_paths,
        "fresh_profile_config_absent_and_env_is_regular": bool(
            exact_paths
            and config
            and profile_env
            and not config.exists()
            and not config.is_symlink()
            and _private_file(profile_env)
        ),
        "fresh_profile_sessions_are_empty": _directory_empty(expected / "sessions"),
        "fresh_profile_memories_are_empty": _directory_empty(expected / "memories"),
        "fresh_root_sessions_are_empty": _directory_empty(root / "sessions"),
        "fresh_root_memories_are_empty": _directory_empty(root / "memories"),
        "fresh_shared_auth_directory_is_empty": _directory_empty(root / "shared"),
        "fresh_isolated_os_home_is_empty": _directory_empty(root / "os-home"),
        "fresh_root_config_and_env_are_absent": all(
            not path.exists() and not path.is_symlink()
            for path in (root / "config.yaml", root / ".env", root / "auth.json")
        ),
        "fresh_profile_has_no_auth_store": all(not path.exists() and not path.is_symlink() for path in auth_candidates),
        "fresh_runtime_home_known_auth_sources_are_absent": _runtime_home_known_auth_sources_absent(
            env.get("HOME", str(root / "os-home"))
        ),
        "fresh_profile_env_has_no_nonempty_secret": bool(profile_env and _env_has_no_nonempty_secret(profile_env)),
        "fresh_profile_service_absent_and_gateway_stopped": status.returncode == 0
        and "gateway is not running" in status_lines,
    }


def _print_checks(checks: dict[str, bool]) -> int:
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, sort_keys=True))
    return 0 if passed else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="严格验收专用 Hermes 根隔离门禁")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create-root")
    create.add_argument("--root", required=True)
    create.add_argument("--purpose", choices=sorted(ROOT_PURPOSES), required=True)
    check = sub.add_parser("check-root")
    check.add_argument("--root", required=True)
    fresh = sub.add_parser("check-fresh")
    fresh.add_argument("--root", required=True)
    fresh.add_argument("--profile", required=True)
    fresh.add_argument("--hermes", required=True)
    run = sub.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("--hermes", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    cloud = sub.add_parser("run-cloud")
    cloud.add_argument("--root", required=True)
    cloud.add_argument("--hermes", required=True)
    cloud.add_argument("command", nargs=argparse.REMAINDER)
    qwen_auth = sub.add_parser("run-qwen-auth")
    qwen_auth.add_argument("--root", required=True)
    qwen_auth.add_argument("--qwen", required=True)
    qwen_auth.add_argument("--mode", choices=("help", "login"), required=True)
    service = sub.add_parser("run-service")
    service.add_argument("--root", required=True)
    service.add_argument("--hermes", required=True)
    service.add_argument("command", nargs=argparse.REMAINDER)
    checker = sub.add_parser("run-checker")
    checker.add_argument("--root", required=True)
    checker.add_argument("--hermes", required=True)
    checker.add_argument("--checker", choices=sorted(ALLOWED_CHECKERS), required=True)
    checker.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.action == "create-root":
            return _print_checks(create_root(args.root, args.purpose))
        root = validate_root(args.root)
        if args.action == "check-root":
            return _print_checks(root_checks(root))
        if args.action == "check-fresh":
            return _print_checks(fresh_profile_checks(root, args.profile, args.hermes))
        if args.action == "run-qwen-auth":
            if args.mode == "login" and not trusted_tty_available():
                raise IsolationError("trusted_tty_required")
            launcher = str(_validated_launcher(args.qwen))
            env = interactive_environment_for_root(root, launcher)
            try:
                return _private_subprocess_run(
                    [launcher, *qwen_auth_command(args.mode)],
                    check=False,
                    env=env,
                ).returncode
            except (OSError, subprocess.SubprocessError) as exc:
                raise IsolationError("qwen_cli_unavailable") from exc
        command = list(args.command)
        if command and command[0] == "--":
            command.pop(0)
        if not command:
            raise IsolationError("hermes_command_required")
        launcher = str(_validated_launcher(args.hermes))
        if args.action in {"run", "run-cloud"} and sensitive_interactive_command(command):
            if not trusted_tty_available():
                raise IsolationError("trusted_tty_required")
        if args.action == "run-service":
            validate_service_command(command)
            env = service_environment(root, launcher)
        elif args.action == "run-cloud":
            validate_interactive_root(root, args.action)
            if is_persistent_service_command(command):
                raise IsolationError("service_command_requires_run_service")
            validate_isolated_command(command)
            env = cloud_interactive_environment(root, launcher)
        else:
            if (
                args.action == "run"
                and is_persistent_service_command(command)
                and not is_protected_foreground_stop(command)
            ):
                raise IsolationError("service_command_requires_run_service")
            if args.action == "run":
                validate_interactive_root(root, args.action)
                validate_isolated_command(command)
            env = (
                cloud_interactive_environment(root, launcher)
                if args.action == "run-checker" and root_purpose(root) == "cloud-service"
                else isolated_environment(root, launcher)
            )
        try:
            if args.action == "run-checker":
                checker = Path(__file__).with_name(args.checker)
                return _private_subprocess_run(
                    [sys.executable, str(checker), *command], check=False, env=env
                ).returncode
            return _private_subprocess_run([launcher, *command], check=False, env=env).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            raise IsolationError("hermes_unavailable") from exc
    except IsolationError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

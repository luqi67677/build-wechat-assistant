#!/usr/bin/env python3
"""安装并布尔核验云端 Hermes 用户服务的 systemd 环境隔离。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from systemd_env_policy import (
    BLOCKED_SYSTEMD_ENV_KEYS,
    assignment_keys,
    guard_dropin_text,
    initial_environment_is_clean,
    is_blocked_systemd_key,
    manager_environment_is_clean,
    unset_environment_keys,
)


PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
ISOLATED_ROOT_MARKER = ".build-wechat-assistant-isolated-root.json"


class GuardError(RuntimeError):
    """只携带固定错误码，不携带路径、环境值或服务输出。"""


def _service_account_home() -> Path:
    try:
        import pwd

        home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve(strict=True)
    except (ImportError, KeyError, OSError, RuntimeError) as exc:
        raise GuardError("service_home_unavailable") from exc
    return home


def service_name(profile: str) -> str:
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise GuardError("profile_invalid")
    return f"hermes-gateway-{profile}.service"


def run_systemctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError("systemctl_unavailable") from exc


def manager_env_clean(runner=run_systemctl) -> bool:
    result = runner(["show-environment"])
    return result.returncode == 0 and manager_environment_is_clean(result.stdout)


def manager_home_matches(expected_home: Path, runner=run_systemctl) -> bool:
    result = runner(["show-environment"])
    if result.returncode != 0:
        return False
    values: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        key, separator, value = raw.partition("=")
        if separator and key in values:
            return False
        if separator:
            values[key] = value
    return values.get("HOME") == str(expected_home)


def _property(unit: str, name: str, runner=run_systemctl) -> str | None:
    result = runner(["show", unit, "--no-pager", "--property", name, "--value"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def resolve_expected_binding(profile: str, hermes: str, expected_hermes_root: str) -> tuple[Path, str]:
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise GuardError("profile_invalid")
    root = Path(expected_hermes_root)
    if not root.is_absolute() or root != Path(os.path.abspath(root)):
        raise GuardError("expected_hermes_root_invalid")
    try:
        root_info = root.lstat()
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GuardError("expected_hermes_root_invalid") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_resolved != root
        or (os.name != "nt" and stat.S_IMODE(root_info.st_mode) & 0o077)
    ):
        raise GuardError("expected_hermes_root_invalid")
    marker_path = root / ISOLATED_ROOT_MARKER
    try:
        marker_info = marker_path.lstat()
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError("expected_hermes_root_marker_invalid") from exc
    marker_valid = (
        stat.S_ISREG(marker_info.st_mode)
        and not stat.S_ISLNK(marker_info.st_mode)
        and (not hasattr(os, "geteuid") or marker_info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(marker_info.st_mode) & 0o077)
        and isinstance(marker, dict)
        and marker.get("schema_version") == 1
        and marker.get("root_device") == root_info.st_dev
        and marker.get("root_inode") == root_info.st_ino
        and marker.get("owner_uid") == getattr(os, "geteuid", lambda: -1)()
        and isinstance(marker.get("nonce"), str)
        and len(marker["nonce"]) == 32
    )
    if not marker_valid:
        raise GuardError("expected_hermes_root_marker_invalid")
    launcher = Path(hermes)
    if not launcher.is_absolute():
        raise GuardError("hermes_launcher_not_absolute")
    try:
        launcher_file = launcher.resolve(strict=True)
        info = launcher_file.lstat()
        with launcher_file.open("rb") as stream:
            first_line = stream.readline(4096).decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError):
        raise GuardError("hermes_launcher_unreadable")
    if not stat.S_ISREG(info.st_mode) or not os.access(launcher_file, os.X_OK) or not first_line.startswith("#!"):
        raise GuardError("hermes_launcher_invalid")
    interpreter = first_line[2:].strip()
    if not interpreter or " " in interpreter or not Path(interpreter).is_absolute():
        raise GuardError("hermes_interpreter_unresolved")
    try:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"LANG", "LANGUAGE", "LC_ALL", "TERM", "TZ"} or key.startswith("LC_")
        }
        env.update(
            {
                "PATH": os.pathsep.join((str(launcher_file.parent), "/usr/bin", "/bin")),
                "HOME": str(_service_account_home()),
                "HERMES_HOME": str(root),
                "HERMES_SHARED_AUTH_DIR": str(root / "shared"),
            }
        )
        result = subprocess.run(
            [str(launcher_file), "-p", profile, "config", "path"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError("profile_path_unresolved") from exc
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        raise GuardError("profile_path_unresolved")
    config_path = Path(output)
    profile_home = config_path.parent
    expected_home = root / "profiles" / profile
    if (
        config_path != expected_home / "config.yaml"
        or profile_home != expected_home
        or not config_path.is_file()
        or config_path.is_symlink()
        or profile_home.is_symlink()
    ):
        raise GuardError("profile_path_mismatch")
    return profile_home, interpreter


def _environment_assignments(text: str | None) -> dict[str, str] | None:
    if text is None:
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    values: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if not key or key in values:
            return None
        values[key] = value
    return values


def _execstart_argv(text: str | None) -> list[str] | None:
    if not text:
        return None
    match = re.search(r"argv\[\]=(.*?)(?:\s+;\s+(?:ignore_errors|start_time|stop_time|pid|code|status)=|\s*}\s*$)", text)
    raw = match.group(1).strip() if match else text.strip()
    if raw.startswith("ExecStart="):
        raw = raw.split("=", 1)[1].strip()
    try:
        argv = shlex.split(raw)
    except ValueError:
        return None
    return argv or None


def _expected_argv(profile: str, interpreter: str) -> list[str]:
    return [interpreter, "-m", "hermes_cli.main", "--profile", profile, "gateway", "run"]


def unit_binding_checks(
    profile: str,
    expected_home: Path,
    expected_interpreter: str,
    runner=run_systemctl,
) -> dict[str, bool]:
    unit = service_name(profile)
    environment = _environment_assignments(_property(unit, "Environment", runner))
    execstart = _execstart_argv(_property(unit, "ExecStart", runner))
    expected = _expected_argv(profile, expected_interpreter)
    return {
        "unit_execstart_matches_hermes_launcher": execstart == expected,
        "unit_execstart_binds_requested_profile": execstart == expected,
        "unit_hermes_home_matches_requested_profile": environment is not None
        and environment.get("HERMES_HOME") == str(expected_home),
    }


def service_state_checks(
    profile: str,
    expected_active: str,
    expected_enabled: str,
    runner=run_systemctl,
) -> dict[str, bool]:
    unit = service_name(profile)
    active = runner(["is-active", unit])
    enabled = runner(["is-enabled", unit])
    return {
        "service_active_state_matches_expected": active.stdout.strip() == expected_active,
        "service_enabled_state_matches_expected": enabled.stdout.strip() == expected_enabled,
    }


def unit_guard_checks(
    profile: str,
    expected_home: Path,
    expected_interpreter: str,
    runner=run_systemctl,
) -> dict[str, bool]:
    unit = service_name(profile)
    environment = _property(unit, "Environment", runner)
    environment_files = _property(unit, "EnvironmentFiles", runner)
    unset_environment = _property(unit, "UnsetEnvironment", runner)
    assigned = assignment_keys(environment) if environment is not None else None
    environment_values = _environment_assignments(environment)
    unset = unset_environment_keys(unset_environment) if unset_environment is not None else None
    checks = {
        "systemd_manager_has_no_uncontrolled_secret_environment": manager_env_clean(runner),
        "systemd_manager_home_matches_service_account": manager_home_matches(
            _service_account_home(), runner
        ),
        "unit_environment_has_no_uncontrolled_secret": assigned is not None
        and not any(is_blocked_systemd_key(key) for key in assigned),
        "unit_home_override_absent_or_matches_service_account": environment_values is not None
        and environment_values.get("HOME", str(_service_account_home()))
        == str(_service_account_home()),
        "unit_environment_files_absent": environment_files == "",
        "unit_unsets_all_known_secret_keys": unset is not None
        and BLOCKED_SYSTEMD_ENV_KEYS <= unset,
    }
    checks.update(unit_binding_checks(profile, expected_home, expected_interpreter, runner))
    return checks


def _same_uid_gateway_pids(proc_root: Path = Path("/proc")) -> tuple[set[int], bool]:
    """枚举当前服务账号全部 gateway run；读不到同账号进程时失败关闭。"""
    pids: set[int] = set()
    complete = True
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return pids, False
    uid = os.geteuid()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            info = cmdline_path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            complete = False
            continue
        if info.st_uid != uid:
            continue
        try:
            argv = [item.decode("utf-8", errors="strict") for item in cmdline_path.read_bytes().split(b"\0") if item]
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            complete = False
            continue
        if any(argv[index : index + 2] == ["gateway", "run"] for index in range(max(0, len(argv) - 1))):
            pids.add(int(entry.name))
    return pids, complete


def stopped_process_scope_checks(proc_root: Path = Path("/proc")) -> dict[str, bool]:
    pids, complete = _same_uid_gateway_pids(proc_root)
    return {
        "same_service_user_gateway_process_scan_complete": complete,
        "same_service_user_has_no_gateway_process": complete and not pids,
    }


def _safe_unit_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.geteuid()
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def _safe_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.geteuid()
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def install_guard(
    profile: str,
    expected_home: Path,
    expected_interpreter: str,
    home: Path | None = None,
    runner=run_systemctl,
) -> bool:
    unit = service_name(profile)
    home_dir = home or _service_account_home()
    config_dir = home_dir / ".config"
    systemd_dir = config_dir / "systemd"
    user_unit_dir = systemd_dir / "user"
    for directory in (config_dir, systemd_dir, user_unit_dir):
        if not _safe_directory(directory):
            raise GuardError("unit_directory_unsafe_or_missing")
    base_unit = user_unit_dir / unit
    if not _safe_unit_file(base_unit):
        raise GuardError("base_unit_unsafe_or_missing")
    if not all(unit_binding_checks(profile, expected_home, expected_interpreter, runner).values()):
        raise GuardError("base_unit_profile_binding_invalid")

    dropin_dir = user_unit_dir / f"{unit}.d"
    if dropin_dir.exists() or dropin_dir.is_symlink():
        if not _safe_directory(dropin_dir):
            raise GuardError("dropin_directory_unsafe")
    else:
        try:
            dropin_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise GuardError("dropin_directory_create_failed") from exc

    target = dropin_dir / "10-weixin-env-guard.conf"
    expected = guard_dropin_text()
    if target.exists() or target.is_symlink():
        if not _safe_unit_file(target):
            raise GuardError("guard_file_unsafe")
        try:
            if target.read_text(encoding="utf-8") != expected:
                raise GuardError("guard_file_conflict")
        except (OSError, UnicodeError) as exc:
            raise GuardError("guard_file_unreadable") from exc
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(expected)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise GuardError("guard_file_create_failed") from exc

    reload_result = runner(["daemon-reload"])
    if reload_result.returncode != 0:
        raise GuardError("daemon_reload_failed")
    return True


def runtime_checks(
    profile: str,
    expected_home: Path,
    expected_interpreter: str,
    expected_enabled: str,
    *,
    proc_root: Path = Path("/proc"),
    runner=run_systemctl,
) -> dict[str, bool]:
    checks = unit_guard_checks(profile, expected_home, expected_interpreter, runner)
    checks.update(service_state_checks(profile, "active", expected_enabled, runner))
    unit = service_name(profile)
    active = runner(["is-active", unit])
    pid_text = _property(unit, "MainPID", runner)
    try:
        pid = int(pid_text or "0")
    except ValueError:
        pid = 0
    process_environment_clean = False
    process_home_matches = False
    process_os_home_matches = False
    process_argv_matches = False
    if active.returncode == 0 and active.stdout.strip() == "active" and pid > 1:
        environ_path = proc_root / str(pid) / "environ"
        cmdline_path = proc_root / str(pid) / "cmdline"
        try:
            info = environ_path.stat()
            data = environ_path.read_bytes()
            cmdline_info = cmdline_path.stat()
            cmdline = [item.decode("utf-8", errors="strict") for item in cmdline_path.read_bytes().split(b"\0") if item]
            process_environment_clean = info.st_uid == os.geteuid() and initial_environment_is_clean(data)
            process_home_matches = info.st_uid == os.geteuid() and any(
                raw == f"HERMES_HOME={expected_home}".encode() for raw in data.split(b"\0")
            )
            process_os_home_matches = info.st_uid == os.geteuid() and any(
                raw == f"HOME={_service_account_home()}".encode() for raw in data.split(b"\0")
            )
            process_argv_matches = (
                cmdline_info.st_uid == os.geteuid()
                and cmdline == _expected_argv(profile, expected_interpreter)
            )
        except (OSError, UnicodeError):
            process_environment_clean = False
    checks.update(
        {
            "service_is_active_with_main_pid": active.returncode == 0
            and active.stdout.strip() == "active"
            and pid > 1,
            "service_initial_environment_has_no_uncontrolled_secret": process_environment_clean,
            "service_process_hermes_home_matches_requested_profile": process_home_matches,
            "service_process_home_matches_service_account": process_os_home_matches,
            "service_process_argv_matches_requested_profile": process_argv_matches,
        }
    )
    gateway_pids, scan_complete = _same_uid_gateway_pids(proc_root)
    checks.update(
        {
            "same_service_user_gateway_process_scan_complete": scan_complete,
            "target_is_only_gateway_for_service_user": scan_complete and gateway_pids == {pid},
        }
    )
    return checks


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="云端 systemd Weixin 环境隔离门禁")
    parser.add_argument("action", choices=("check-manager", "install", "check-prestart", "check-prerestart", "check-runtime"))
    parser.add_argument("--profile", help="除 check-manager 外必需的非 default Profile")
    parser.add_argument("--hermes", help="除 check-manager 外必需的 Hermes 绝对路径")
    parser.add_argument("--expected-hermes-root", help="除 check-manager 外必需的本轮专用 Hermes 根绝对路径")
    parser.add_argument("--expect-enabled", choices=("enabled", "disabled"), help="check-prestart/check-prerestart/check-runtime 必需的预期自启状态")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if not sys.platform.startswith("linux"):
            raise GuardError("systemd_guard_requires_linux")
        if args.action == "check-manager":
            checks = {"systemd_manager_has_no_uncontrolled_secret_environment": manager_env_clean()}
        else:
            if args.action in {"check-prestart", "check-prerestart", "check-runtime"} and not args.expect_enabled:
                raise GuardError("expected_enabled_required")
            if not args.profile or not args.hermes or not args.expected_hermes_root:
                raise GuardError("profile_hermes_and_expected_root_required")
            expected_home, expected_interpreter = resolve_expected_binding(
                args.profile, args.hermes, args.expected_hermes_root
            )
            if args.action == "install":
                install_guard(args.profile, expected_home, expected_interpreter)
                checks = unit_guard_checks(args.profile, expected_home, expected_interpreter)
                checks.update(service_state_checks(args.profile, "inactive", "disabled"))
                checks.update(stopped_process_scope_checks())
            elif args.action == "check-prestart":
                checks = unit_guard_checks(args.profile, expected_home, expected_interpreter)
                checks.update(service_state_checks(args.profile, "inactive", args.expect_enabled))
                checks.update(stopped_process_scope_checks())
            elif args.action == "check-prerestart":
                checks = runtime_checks(
                    args.profile, expected_home, expected_interpreter, args.expect_enabled
                )
            else:
                checks = runtime_checks(
                    args.profile, expected_home, expected_interpreter, args.expect_enabled
                )
    except GuardError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
        return 2
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

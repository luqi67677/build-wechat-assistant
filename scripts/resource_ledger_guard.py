#!/usr/bin/env python3
"""共享服务器小号测试资源台账：只建账、核验和预览，不执行删除。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


PROFILE_RE = re.compile(r"[a-z0-9]{2,32}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class LedgerError(RuntimeError):
    """只携带固定错误码，不携带主机、账号、路径或凭据。"""


def _service_account_home() -> Path:
    if os.name == "nt":
        home = Path.home()
    else:
        try:
            import pwd

            home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        except (ImportError, KeyError, OSError) as exc:
            raise LedgerError("service_home_unavailable") from exc
    try:
        return home.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LedgerError("service_home_unavailable") from exc


def _run_systemctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LedgerError("systemctl_unavailable") from exc


def _unit_load_state(unit: str, runner=_run_systemctl) -> str:
    result = runner(["show", unit, "--property", "LoadState", "--value"])
    if result.returncode != 0:
        return "unknown"
    value = result.stdout.strip()
    return value if value in {"loaded", "not-found"} else "unknown"


def _unit_active_state(unit: str, runner=_run_systemctl) -> str:
    result = runner(["is-active", unit])
    value = result.stdout.strip()
    return value if value in {"active", "inactive", "failed"} else "unknown"


def _same_uid_gateway_processes_absent(proc_root: Path = Path("/proc")) -> tuple[bool, bool]:
    complete = True
    found = False
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return False, False
    uid = os.geteuid()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        path = entry / "cmdline"
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            complete = False
            continue
        if info.st_uid != uid:
            continue
        try:
            argv = [item.decode("utf-8", errors="strict") for item in path.read_bytes().split(b"\0") if item]
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            complete = False
            continue
        if any(argv[index : index + 2] == ["gateway", "run"] for index in range(max(0, len(argv) - 1))):
            found = True
    return complete and not found, complete


def _host_binding(machine_id_path: Path = Path("/etc/machine-id"), root_fs: Path = Path("/")) -> str:
    try:
        machine_id = machine_id_path.read_bytes().strip()
        root_info = root_fs.stat()
    except OSError as exc:
        raise LedgerError("host_binding_unavailable") from exc
    if not machine_id:
        raise LedgerError("host_binding_unavailable")
    digest = hashlib.sha256()
    digest.update(machine_id)
    digest.update(b"\0")
    digest.update(str(root_info.st_dev).encode("ascii"))
    return digest.hexdigest()


def _private_regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
        and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o077)
    )


def _private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path
            and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
            and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o077)
        )
    except (OSError, RuntimeError):
        return False


def _owned_directory_not_writable(path: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path
            and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid())
            and (os.name == "nt" or not stat.S_IMODE(info.st_mode) & 0o022)
        )
    except (OSError, RuntimeError):
        return False


def _write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise LedgerError("ledger_create_failed") from exc


def _replace_private(path: Path, payload: dict[str, Any]) -> None:
    if not _private_regular(path):
        raise LedgerError("ledger_file_unsafe")
    temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(8)}"
    _write_exclusive(temporary, payload)
    try:
        os.replace(temporary, path)
    except OSError as exc:
        raise LedgerError("ledger_update_failed_manual_review_required") from exc


def _load(path: Path) -> dict[str, Any]:
    if not _private_regular(path):
        raise LedgerError("ledger_file_unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("ledger_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LedgerError("ledger_schema_invalid")
    return payload


def _resource_paths(payload: dict[str, Any]) -> dict[str, Path]:
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        raise LedgerError("ledger_schema_invalid")
    required = {"hermes_root", "profile_dir", "workspace", "release_dir"}
    if set(resources) != required or not all(isinstance(value, str) for value in resources.values()):
        raise LedgerError("ledger_schema_invalid")
    return {key: Path(value) for key, value in resources.items()}


def _planned_paths(service_home: Path, profile: str, hermes_root: str, workspace: str, release_dir: str) -> dict[str, str]:
    if (
        not service_home.is_absolute()
        or service_home != Path(os.path.abspath(service_home))
        or service_home == Path(service_home.anchor)
    ):
        raise LedgerError("service_home_invalid")
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise LedgerError("profile_invalid")
    paths: dict[str, Path] = {}
    for key, raw in (("hermes_root", hermes_root), ("workspace", workspace), ("release_dir", release_dir)):
        path = Path(raw)
        if not path.is_absolute() or path != Path(os.path.abspath(path)) or path == service_home or service_home not in path.parents:
            raise LedgerError("resource_outside_service_home")
        paths[key] = path
    if len(set(paths.values())) != 3 or any(
        left in right.parents or right in left.parents
        for index, left in enumerate(paths.values())
        for right in list(paths.values())[index + 1 :]
    ):
        raise LedgerError("resource_paths_overlap")
    paths["profile_dir"] = paths["hermes_root"] / "profiles" / profile
    return {key: str(value) for key, value in paths.items()}


def create_plan(
    ledger: Path,
    service_home: str,
    profile: str,
    hermes_root: str,
    workspace: str,
    release_dir: str,
    instance_label_sha256: str,
    host_binding_sha256: str,
) -> dict[str, bool]:
    if ledger.exists() or ledger.is_symlink() or not ledger.is_absolute():
        raise LedgerError("plan_ledger_must_be_new_absolute_file")
    if not _private_directory(ledger.parent):
        raise LedgerError("plan_ledger_parent_must_be_private_directory")
    if not SHA256_RE.fullmatch(instance_label_sha256) or not SHA256_RE.fullmatch(host_binding_sha256):
        raise LedgerError("instance_or_host_hash_invalid")
    home = Path(service_home)
    resources = _planned_paths(home, profile, hermes_root, workspace, release_dir)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ledger_id": secrets.token_hex(16),
        "host_binding_sha256": host_binding_sha256,
        "operator_instance_label_sha256": instance_label_sha256,
        "service_uid": None,
        "service_home": str(home),
        "profile": profile,
        "unit": f"hermes-gateway-{profile}.service",
        "resources": resources,
        "baseline": "planned_before_service_account_creation",
        "activation_status": "planned",
        "created_resources": {},
        "sealed_resources": None,
        "deployment_sealed": False,
        "authorizations": {"model": "not_created", "weixin": "not_created"},
        "admin_resources": {
            "service_account_must_be_created_after_plan": True,
            "linger_preexisting": False,
        },
    }
    _write_exclusive(ledger, payload)
    return {
        "resource_plan_created_before_server_write": True,
        "plan_binds_instance_label_hash": True,
        "plan_binds_readonly_host_fingerprint": True,
        "plan_requires_new_service_account": True,
        "plan_ledger_is_private_regular_file": _private_regular(ledger),
    }


def activate_plan(
    ledger: Path,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
    runner=_run_systemctl,
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    if payload.get("activation_status") != "planned" or payload.get("service_uid") is not None:
        raise LedgerError("plan_activation_state_invalid")
    if payload.get("service_home") != str(home) or payload.get("host_binding_sha256") != _host_binding(machine_id_path, root_fs):
        raise LedgerError("plan_target_binding_mismatch")
    if not _owned_directory_not_writable(home):
        raise LedgerError("service_home_unsafe")
    if ledger.parent != home:
        raise LedgerError("plan_ledger_must_be_in_service_home")
    paths = _resource_paths(payload)
    if not all(not path.exists() and not path.is_symlink() for path in paths.values()):
        raise LedgerError("preexisting_resource_conflict")
    if _unit_load_state(str(payload.get("unit", "")), runner) != "not-found":
        raise LedgerError("preexisting_resource_conflict")
    payload["service_uid"] = getattr(os, "geteuid", lambda: -1)()
    payload["activation_status"] = "active"
    payload["baseline"] = "all_planned_runtime_resources_absent"
    _replace_private(ledger, payload)
    return {
        "plan_host_binding_matches_current_server": True,
        "plan_service_home_matches_current_account": True,
        "planned_paths_and_unit_are_absent": True,
        "new_service_account_plan_activated": True,
        "ledger_file_is_private_regular": _private_regular(ledger),
    }


def _binding_checks(
    payload: dict[str, Any],
    ledger: Path,
    home: Path,
    machine_id_path: Path,
    root_fs: Path,
) -> dict[str, bool]:
    paths = _resource_paths(payload)
    admin_resources = payload.get("admin_resources")
    return {
        "ledger_host_binding_matches": payload.get("host_binding_sha256") == _host_binding(machine_id_path, root_fs),
        "ledger_service_uid_matches": payload.get("service_uid") == getattr(os, "geteuid", lambda: -1)(),
        "ledger_service_home_matches": payload.get("service_home") == str(home),
        "ledger_service_home_is_owned_and_not_writable": _owned_directory_not_writable(home),
        "ledger_file_is_in_service_home": ledger.parent == home,
        "ledger_profile_is_named_nondefault": bool(PROFILE_RE.fullmatch(str(payload.get("profile", ""))))
        and payload.get("profile") != "default",
        "ledger_resource_paths_remain_inside_service_home": all(
            path.is_absolute() and path != home and home in path.parents for path in paths.values()
        ),
        "ledger_plan_is_activated": payload.get("activation_status") == "active",
        "ledger_requires_post_plan_service_account": isinstance(admin_resources, dict)
        and admin_resources.get("service_account_must_be_created_after_plan") is True,
    }


def check_prewrite(
    ledger: Path,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
    runner=_run_systemctl,
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    paths = _resource_paths(payload)
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    checks.update(
        {
            "ledger_file_is_private_regular": _private_regular(ledger),
            "all_planned_resources_still_absent": all(
                not path.exists() and not path.is_symlink() for path in paths.values()
            ),
            "planned_unit_still_absent": _unit_load_state(str(payload.get("unit", "")), runner) == "not-found",
            "ledger_has_no_recorded_or_sealed_resource": payload.get("created_resources") == {}
            and payload.get("sealed_resources") is None
            and payload.get("deployment_sealed") is False,
        }
    )
    return checks


def _resource_identity(path: Path) -> dict[str, int | str]:
    if not _private_directory(path):
        raise LedgerError("created_resource_unsafe_or_missing")
    info = path.lstat()
    return {"device": info.st_dev, "inode": info.st_ino, "type": "directory"}


def _identity_matches(path: Path, expected: object) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        isinstance(expected, dict)
        and stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and expected.get("device") == info.st_dev
        and expected.get("inode") == info.st_ino
        and expected.get("type") == "directory"
    )


def _created_resources_match(payload: dict[str, Any]) -> bool:
    paths = _resource_paths(payload)
    created = payload.get("created_resources")
    if not isinstance(created, dict) or not set(created) <= set(paths):
        return False
    return all(
        _identity_matches(path, created[key]) if key in created else not path.exists() and not path.is_symlink()
        for key, path in paths.items()
    )


def record_created_resource(
    ledger: Path,
    resource: str,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    if not all(checks.values()):
        raise LedgerError("ledger_binding_mismatch")
    paths = _resource_paths(payload)
    if resource not in paths:
        raise LedgerError("resource_name_invalid")
    created = payload.get("created_resources")
    if not isinstance(created, dict) or not set(created) <= set(paths):
        raise LedgerError("ledger_schema_invalid")
    if resource == "profile_dir" and "hermes_root" not in created:
        raise LedgerError("parent_resource_not_recorded")
    identity = _resource_identity(paths[resource])
    if resource in created:
        if created[resource] != identity:
            raise LedgerError("created_resource_identity_drift")
    else:
        created[resource] = identity
        _replace_private(ledger, payload)
    checks.update(
        {
            "created_resource_identity_recorded": True,
            "all_recorded_resources_match_and_unrecorded_remain_absent": _created_resources_match(payload),
            "ledger_file_is_private_regular": _private_regular(ledger),
        }
    )
    return checks


def seal_deployed(
    ledger: Path,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
    runner=_run_systemctl,
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    if payload.get("sealed_resources") is not None:
        raise LedgerError("ledger_already_sealed")
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    if not all(checks.values()):
        raise LedgerError("ledger_binding_mismatch")
    paths = _resource_paths(payload)
    created = payload.get("created_resources")
    if not isinstance(created, dict) or set(created) != set(paths) or not _created_resources_match(payload):
        raise LedgerError("deployed_resources_not_recorded_or_drifted")
    unit_loaded = _unit_load_state(str(payload.get("unit", "")), runner) == "loaded"
    if not unit_loaded:
        raise LedgerError("deployed_unit_not_loaded")
    payload["sealed_resources"] = dict(created)
    payload["deployment_sealed"] = True
    _replace_private(ledger, payload)
    checks.update(
        {
            "all_deployed_resources_are_private_directories": True,
            "deployed_unit_is_loaded": True,
            "ledger_file_is_private_regular": _private_regular(ledger),
        }
    )
    return checks


def mark_authorization(
    ledger: Path,
    kind: str,
    state: str,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    if not all(checks.values()):
        raise LedgerError("ledger_binding_mismatch")
    authorizations = payload.get("authorizations")
    if kind not in {"model", "weixin"} or state not in {
        "creation_started",
        "created",
        "absent_user_confirmed",
        "revoked_user_confirmed",
    }:
        raise LedgerError("authorization_transition_invalid")
    if not isinstance(authorizations, dict) or kind not in authorizations:
        raise LedgerError("ledger_schema_invalid")
    previous = authorizations[kind]
    allowed = (
        previous == "not_created"
        and state == "creation_started"
        or previous == "creation_started"
        and state in {"created", "absent_user_confirmed"}
        or previous == "created"
        and state == "revoked_user_confirmed"
    )
    if not allowed:
        raise LedgerError("authorization_transition_invalid")
    authorizations[kind] = state
    _replace_private(ledger, payload)
    checks.update(
        {
            "authorization_transition_recorded": True,
            "ledger_file_is_private_regular": _private_regular(ledger),
        }
    )
    return checks


def _sealed_resources_match(payload: dict[str, Any]) -> bool:
    paths = _resource_paths(payload)
    sealed = payload.get("sealed_resources")
    if not isinstance(sealed, dict) or set(sealed) != set(paths):
        return False
    for key, path in paths.items():
        expected = sealed.get(key)
        if not _identity_matches(path, expected):
            return False
    return True


def preview_cleanup(
    ledger: Path,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
    proc_root: Path = Path("/proc"),
    runner=_run_systemctl,
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    authorizations = payload.get("authorizations", {})
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    gateways_absent, process_scan_complete = _same_uid_gateway_processes_absent(proc_root)
    sealed = payload.get("sealed_resources")
    recorded_scope_safe = _sealed_resources_match(payload) if sealed is not None else _created_resources_match(payload)
    checks.update(
        {
            "recorded_test_resource_identity_has_no_drift": recorded_scope_safe,
            "test_unit_is_uninstalled": _unit_load_state(str(payload.get("unit", "")), runner) == "not-found",
            "test_unit_is_inactive": _unit_active_state(str(payload.get("unit", "")), runner) == "inactive",
            "same_service_user_gateway_process_scan_complete": process_scan_complete,
            "same_service_user_has_no_gateway_process": gateways_absent,
            "model_authorization_absent_or_revoke_confirmed": isinstance(authorizations, dict)
            and authorizations.get("model")
            in {"not_created", "absent_user_confirmed", "revoked_user_confirmed"},
            "weixin_authorization_absent_or_revoke_confirmed": isinstance(authorizations, dict)
            and authorizations.get("weixin")
            in {"not_created", "absent_user_confirmed", "revoked_user_confirmed"},
            "cleanup_is_preview_only": True,
        }
    )
    return checks


def verify_cleanup(
    ledger: Path,
    *,
    home: Path | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    root_fs: Path = Path("/"),
    proc_root: Path = Path("/proc"),
    runner=_run_systemctl,
) -> dict[str, bool]:
    home = (home or _service_account_home()).resolve(strict=True)
    payload = _load(ledger)
    paths = _resource_paths(payload)
    authorizations = payload.get("authorizations", {})
    checks = _binding_checks(payload, ledger, home, machine_id_path, root_fs)
    gateways_absent, process_scan_complete = _same_uid_gateway_processes_absent(proc_root)
    checks.update(
        {
            "all_exact_test_resource_paths_are_absent": all(
                not path.exists() and not path.is_symlink() for path in paths.values()
            ),
            "test_unit_remains_uninstalled": _unit_load_state(str(payload.get("unit", "")), runner) == "not-found",
            "same_service_user_gateway_process_scan_complete": process_scan_complete,
            "same_service_user_has_no_gateway_process": gateways_absent,
            "model_authorization_absent_or_revoke_confirmed": isinstance(authorizations, dict)
            and authorizations.get("model")
            in {"not_created", "absent_user_confirmed", "revoked_user_confirmed"},
            "weixin_authorization_absent_or_revoke_confirmed": isinstance(authorizations, dict)
            and authorizations.get("weixin")
            in {"not_created", "absent_user_confirmed", "revoked_user_confirmed"},
        }
    )
    return checks


def _print_checks(checks: dict[str, bool]) -> int:
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, sort_keys=True))
    return 0 if passed else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="共享服务器小号测试资源台账门禁")
    sub = parser.add_subparsers(dest="action", required=True)
    host = sub.add_parser("host-binding")
    plan = sub.add_parser("create-plan")
    plan.add_argument("--ledger", required=True)
    plan.add_argument("--service-home", required=True)
    plan.add_argument("--profile", required=True)
    plan.add_argument("--hermes-root", required=True)
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--release-dir", required=True)
    plan.add_argument("--instance-label-sha256", required=True)
    plan.add_argument("--host-binding-sha256", required=True)
    activate = sub.add_parser("activate-plan")
    activate.add_argument("--ledger", required=True)
    record = sub.add_parser("record-created")
    record.add_argument("--ledger", required=True)
    record.add_argument(
        "--resource",
        choices=("hermes_root", "profile_dir", "workspace", "release_dir"),
        required=True,
    )
    for name in ("check-prewrite", "seal-deployed", "preview-cleanup", "verify-cleanup"):
        item = sub.add_parser(name)
        item.add_argument("--ledger", required=True)
    mark = sub.add_parser("mark-authorization")
    mark.add_argument("--ledger", required=True)
    mark.add_argument("--kind", choices=("model", "weixin"), required=True)
    mark.add_argument(
        "--state",
        choices=("creation_started", "created", "absent_user_confirmed", "revoked_user_confirmed"),
        required=True,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.action == "host-binding":
            print(json.dumps({"result": "PASS", "host_binding_sha256": _host_binding(), "secrets_printed": False}, sort_keys=True))
            return 0
        ledger = Path(args.ledger)
        if args.action == "create-plan":
            checks = create_plan(
                ledger,
                args.service_home,
                args.profile,
                args.hermes_root,
                args.workspace,
                args.release_dir,
                args.instance_label_sha256,
                args.host_binding_sha256,
            )
        elif args.action == "activate-plan":
            checks = activate_plan(ledger)
        elif args.action == "record-created":
            checks = record_created_resource(ledger, args.resource)
        elif args.action == "check-prewrite":
            checks = check_prewrite(ledger)
        elif args.action == "seal-deployed":
            checks = seal_deployed(ledger)
        elif args.action == "mark-authorization":
            checks = mark_authorization(ledger, args.kind, args.state)
        elif args.action == "preview-cleanup":
            checks = preview_cleanup(ledger)
        else:
            checks = verify_cleanup(ledger)
    except LedgerError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    return _print_checks(checks)


if __name__ == "__main__":
    raise SystemExit(main())

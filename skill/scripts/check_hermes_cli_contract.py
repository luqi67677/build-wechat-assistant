#!/usr/bin/env python3
"""在全新临时 HERMES_HOME 中验证当前 Hermes CLI 契约，不读取真实秘密。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION_RE = re.compile(r"Hermes Agent v?(\d+\.\d+\.\d+)")


class ContractError(RuntimeError):
    pass


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError("command_unavailable") from exc


def _ok(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == 0


def run_contract(hermes: str) -> tuple[dict[str, bool], str]:
    temp_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(dir=temp_parent) as raw_temp:
        root = Path(raw_temp).resolve()
        hermes_root = root / "hermes-root"
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        path_parts = [str(Path(hermes).resolve().parent), "/usr/bin", "/bin"]
        env = {
            "PATH": os.pathsep.join(dict.fromkeys(path_parts)),
            "HERMES_HOME": str(hermes_root),
            "TMPDIR": str(root),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        profile = "bwaclitest"
        version_result = _run([hermes, "--version"], env)
        version_match = VERSION_RE.search(version_result.stdout)
        version = version_match.group(1) if version_match else "unknown"

        created = _run(
            [hermes, "profile", "create", profile, "--no-alias", "--no-skills"],
            env,
        )
        shown = _run([hermes, "profile", "show", profile], env)
        config_path = _run([hermes, "-p", profile, "config", "path"], env)
        env_path = _run([hermes, "-p", profile, "config", "env-path"], env)
        expected_profile_dir = hermes_root / "profiles" / profile
        paths_isolated = (
            _ok(config_path)
            and _ok(env_path)
            and Path(config_path.stdout.strip()) == expected_profile_dir / "config.yaml"
            and Path(env_path.stdout.strip()) == expected_profile_dir / ".env"
        )

        help_commands = (
            [hermes, "-p", profile, "gateway", "setup", "--help"],
            [hermes, "-p", profile, "gateway", "run", "--help"],
            [hermes, "-p", profile, "tools", "list", "--help"],
            [hermes, "-p", profile, "fallback", "--help"],
            [hermes, "-p", profile, "pairing", "approve", "--help"],
        )
        help_ok = all(_ok(_run(command, env)) for command in help_commands)
        auth_add_help = _run([hermes, "-p", profile, "auth", "add", "--help"], env)
        auth_add_text = f"{auth_add_help.stdout}\n{auth_add_help.stderr}"
        provider_scoped_auth = (
            _ok(auth_add_help)
            and "provider" in auth_add_text.lower()
            and "--type" in auth_add_text
            and "oauth" in auth_add_text.lower()
        )
        profile_create_help = _run([hermes, "profile", "create", "--help"], env)
        profile_create_text = f"{profile_create_help.stdout}\n{profile_create_help.stderr}"
        isolated_create_flags = _ok(profile_create_help) and all(
            flag in profile_create_text for flag in ("--no-alias", "--no-skills")
        )
        gateway_help = _run([hermes, "gateway", "--help"], env)
        gateway_help_text = f"{gateway_help.stdout}\n{gateway_help.stderr}"
        weixin_advertised = _ok(gateway_help) and "Weixin" in gateway_help_text and "setup" in gateway_help_text
        status_help = _run([hermes, "gateway", "status", "--help"], env)
        status_help_text = f"{status_help.stdout}\n{status_help.stderr}"
        deep_status_advertised = _ok(status_help) and "--deep" in status_help_text
        install_help = _run([hermes, "-p", profile, "gateway", "install", "--help"], env)
        install_text = f"{install_help.stdout}\n{install_help.stderr}"
        flags_ok = _ok(install_help) and all(
            flag in install_text
            for flag in ("--start-now", "--no-start-now", "--start-on-login", "--no-start-on-login")
        )

        baseline = _run(
            [
                sys.executable,
                str(Path(__file__).with_name("apply_chat_safety_baseline.py")),
                "--profile",
                profile,
                "--hermes",
                hermes,
                "--expected-hermes-root",
                str(hermes_root),
                "--workspace",
                str(workspace),
            ],
            env,
        )
        try:
            baseline_payload = json.loads(baseline.stdout)
        except (json.JSONDecodeError, TypeError):
            baseline_payload = {}
        baseline_ok = _ok(baseline) and baseline_payload.get("result") == "PASS"

        pre_qr = _run(
            [
                sys.executable,
                str(Path(__file__).with_name("check_pre_qr_safety.py")),
                "--profile",
                profile,
                "--hermes",
                hermes,
                "--expected-hermes-root",
                str(hermes_root),
            ],
            env,
        )
        try:
            pre_qr_payload = json.loads(pre_qr.stdout)
        except (json.JSONDecodeError, TypeError):
            pre_qr_payload = {}
        pre_qr_ok = _ok(pre_qr) and pre_qr_payload.get("result") == "PASS"
        status = _run([hermes, "-p", profile, "gateway", "status", "--deep"], env)
        status_lines = {
            re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip().lstrip("✓✗").strip().lower()
            for line in f"{status.stdout}\n{status.stderr}".splitlines()
        }
        service_absent_and_stopped = _ok(status) and "gateway is not running" in status_lines

        checks = {
            "version_detected": _ok(version_result) and version != "unknown",
            "isolated_profile_created": _ok(created),
            "profile_show_uses_positional_name": _ok(shown),
            "profile_paths_are_isolated": paths_isolated,
            "required_help_surface_present": help_ok,
            "provider_scoped_auth_advertised": provider_scoped_auth,
            "isolated_profile_create_flags_advertised": isolated_create_flags,
            "weixin_gateway_advertised": weixin_advertised,
            "deep_gateway_status_advertised": deep_status_advertised,
            "deep_gateway_status_executes": _ok(status),
            "service_start_flags_advertised": flags_ok,
            "safe_config_keys_writable": baseline_ok,
            "weixin_tool_policy_mutable": baseline_ok,
            "cli_and_weixin_chat_baseline_applies": baseline_ok,
            "pre_qr_guard_passes": pre_qr_ok,
            "service_absent_and_gateway_remains_stopped": service_absent_and_stopped,
        }
        return checks, version


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在隔离临时根验证 Hermes CLI 能力契约")
    parser.add_argument("--hermes", required=True, help="待验证的 Hermes 启动器绝对路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        checks, version = run_contract(args.hermes)
    except ContractError as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "secrets_printed": False}, sort_keys=True))
        return 2
    passed = all(checks.values())
    print(
        json.dumps(
            {
                "result": "PASS" if passed else "FAIL",
                "hermes_version": version,
                "checks": checks,
                "secrets_printed": False,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

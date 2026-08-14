#!/usr/bin/env python3
"""云端部署前只输出布尔结论；不输出主机、账号、IP、路径或凭据。"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from systemd_env_policy import manager_environment_is_clean


HOST_RE = re.compile(r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z")
FIXED_TLS_HOSTS = ("hermes-agent.nousresearch.com", "ilinkai.weixin.qq.com")


def safe_public_hostname(value: str) -> bool:
    return bool(HOST_RE.fullmatch(value)) and value.lower() != "localhost"


def run_ok(command: list[str], accepted: set[str] | None = None) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if accepted is None:
        return result.returncode == 0
    return result.stdout.strip().lower() in accepted


def systemd_user_manager_env_clean() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and manager_environment_is_clean(result.stdout)


def tls_reachable(host: str) -> bool:
    if not safe_public_hostname(host):
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            return False
        for item in addresses:
            address = ipaddress.ip_address(item[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                return False
        with socket.create_connection((host, 443), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError, ValueError):
        return False


def memory_gib() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (OSError, ValueError):
        return 0.0


def disk_free_gib() -> float:
    try:
        return shutil.disk_usage(Path.home()).free / 1024**3
    except OSError:
        return 0.0


def evaluate(model_host: str) -> dict[str, bool]:
    machine = platform.machine().lower()
    uid = getattr(os, "getuid", lambda: -1)()
    checks = {
        "supported_linux": platform.system() == "Linux",
        "supported_architecture": machine in {"x86_64", "amd64", "aarch64", "arm64"},
        "service_account_is_nonroot": not hasattr(os, "geteuid") or os.geteuid() != 0,
        "required_commands_present": all(shutil.which(name) for name in ("systemctl", "loginctl", "curl", "git", "xz")),
        "systemd_runtime_running": Path("/run/systemd/system").is_dir() and run_ok(["systemctl", "is-system-running"], {"running", "degraded"}),
        "user_service_manager_available": run_ok(["systemctl", "--user", "show-environment"]),
        "systemd_manager_has_no_uncontrolled_secret_environment": systemd_user_manager_env_clean(),
        "linger_enabled": run_ok(["loginctl", "show-user", str(uid), "-p", "Linger", "--value"], {"yes"}),
        "cpu_at_least_two": (os.cpu_count() or 0) >= 2,
        "memory_at_least_1_5_gib": memory_gib() >= 1.5,
        "disk_free_at_least_5_gib": disk_free_gib() >= 5,
        "timezone_detected": bool(os.environ.get("TZ") or Path("/etc/localtime").exists()),
        "model_host_is_public_hostname": safe_public_hostname(model_host),
        "hermes_docs_tls_reachable": tls_reachable(FIXED_TLS_HOSTS[0]),
        "weixin_ilink_tls_reachable": tls_reachable(FIXED_TLS_HOSTS[1]),
        "model_api_tls_reachable": tls_reachable(model_host),
    }
    return checks


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="云端部署前的非秘密布尔检查")
    parser.add_argument("--model-host", required=True, help="已选模型的官方 API 主机名，不含协议、路径或密钥")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    checks = evaluate(args.model_host)
    passed = all(checks.values())
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "secrets_printed": False}, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

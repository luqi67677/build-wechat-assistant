#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

from scoped_feishu_mcp import FeishuError, validate_node_runtime

PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_SCOPES = frozenset({
    "docx:document:create",
    "docx:document:readonly",
    "wiki:node:create",
    "wiki:node:read",
    "wiki:space:read",
})
OFFICIAL_AUTH_HOSTS = ("feishu.cn", "larksuite.com")
URL_RE = re.compile(r"https://[^\s\x1b]+")


class FeishuOAuthError(RuntimeError):
    pass


def _validated_node(node: Path) -> Path:
    try:
        return validate_node_runtime(node)[0]
    except FeishuError as exc:
        raise FeishuOAuthError(str(exc)) from exc


def _official_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
        return None
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in OFFICIAL_AUTH_HOSTS):
        return None
    return value


def _find(payload: object, keys: set[str]) -> object | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value not in (None, ""):
                return value
            found = _find(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find(value, keys)
            if found is not None:
                return found
    return None


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    for raw in (result.stdout, result.stderr):
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _safe_failure(payload: dict[str, Any]) -> str:
    error_type = _find(payload, {"type"})
    message = str(_find(payload, {"message", "hint"}) or "")
    lowered = f"{error_type} {message}".lower()
    if "scope" in lowered or "permission" in lowered or "authorization" in lowered:
        return "飞书专用应用还缺少本次文档操作的最小权限；请在已打开的飞书官方页面确认后重试。"
    return "飞书官方授权没有完成；请确认页面中的账号与授权结果后重试。"


def _environment(credential_home: Path, hermes_home: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    env.update({
        "HOME": str(credential_home),
        "HERMES_HOME": str(hermes_home),
        "XDG_CONFIG_HOME": str(credential_home / ".config"),
        "XDG_DATA_HOME": str(credential_home / ".local" / "share"),
        "XDG_CACHE_HOME": str(credential_home / ".cache"),
        "LARK_CLI_NO_PROXY": "1",
    })
    return env


def initialize_application(
    node: Path,
    lark_cli: Path,
    credential_home: Path,
    hermes_home: Path,
    profile: str,
    *,
    opener: Callable[[str], bool] = webbrowser.open_new_tab,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> dict[str, object]:
    """Create the assistant's dedicated Feishu app without exposing CLI output."""
    node = _validated_node(node)
    process = popen(
        [
            str(node), str(lark_cli), "config", "init", "--new", "--name", profile,
            "--brand", "feishu", "--lang", "zh", "--force-init",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_environment(credential_home, hermes_home),
    )
    lines: queue.Queue[str] = queue.Queue()

    def collect(stream: io.TextIOBase | None) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            lines.put(line)

    readers = [
        threading.Thread(target=collect, args=(process.stdout,), daemon=True),
        threading.Thread(target=collect, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()

    opened = False
    deadline = time.monotonic() + 600

    def consume(line: str) -> None:
        nonlocal opened
        if opened:
            return
        for candidate in URL_RE.findall(line):
            official = _official_url(candidate.rstrip("'\"),.;"))
            if official is not None:
                if not opener(official):
                    raise FeishuOAuthError("系统未能打开飞书官方应用创建页面，请检查浏览器后重试。")
                opened = True
                return

    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise FeishuOAuthError("飞书官方应用创建等待超时；请重新发起后完成页面确认。")
            try:
                consume(lines.get(timeout=0.25))
            except queue.Empty:
                continue
        for reader in readers:
            reader.join(timeout=1)
        while not lines.empty():
            consume(lines.get_nowait())
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise

    if process.returncode != 0:
        raise FeishuOAuthError(
            "这位微信助手的飞书专用应用没有创建完成；请确认已打开的飞书官方页面后重试。"
        )
    return {
        "result": "APPLICATION_READY",
        "profile": profile,
        "secrets_printed": False,
    }


def authorize(
    node: Path,
    lark_cli: Path,
    credential_home: Path,
    hermes_home: Path,
    profile: str,
    scopes: tuple[str, ...],
    *,
    opener: Callable[[str], bool] = webbrowser.open_new_tab,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    node = _validated_node(node)
    env = _environment(credential_home, hermes_home)
    request = runner(
        [str(node), str(lark_cli), "--profile", profile, "auth", "login", "--scope", " ".join(scopes),
         "--no-wait", "--json"],
        capture_output=True, text=True, check=False, timeout=60, env=env,
    )
    request_payload = _payload(request)
    if request.returncode != 0:
        console_url = _official_url(_find(request_payload, {"console_url"}))
        if console_url and opener(console_url):
            return {"result": "APP_PERMISSION_CONFIRMATION_REQUIRED", "secrets_printed": False}
        raise FeishuOAuthError(_safe_failure(request_payload))

    device_code = _find(request_payload, {"device_code"})
    verification_url = _official_url(_find(
        request_payload,
        {"verification_url", "verification_uri", "verification_uri_complete"},
    ))
    if not isinstance(device_code, str) or not device_code or verification_url is None:
        raise FeishuOAuthError("飞书没有返回可核验的官方授权会话，请稍后重试。")
    if not opener(verification_url):
        raise FeishuOAuthError("系统未能打开飞书官方授权页面，请检查默认浏览器后重试。")

    completed = runner(
        [str(node), str(lark_cli), "--profile", profile, "auth", "login", "--device-code", device_code, "--json"],
        capture_output=True, text=True, check=False, timeout=360, env=env,
    )
    completed_payload = _payload(completed)
    if completed.returncode != 0:
        console_url = _official_url(_find(completed_payload, {"console_url"}))
        if console_url and opener(console_url):
            return {"result": "APP_PERMISSION_CONFIRMATION_REQUIRED", "secrets_printed": False}
        raise FeishuOAuthError(_safe_failure(completed_payload))
    return {
        "result": "AUTHORIZED",
        "profile": profile,
        "scopes": list(scopes),
        "secrets_printed": False,
    }


def _existing_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise FeishuOAuthError(f"{label}必须是已存在且非符号链接的绝对目录。")
    return path.resolve(strict=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全打开飞书用户授权页面，不输出授权 URL 或设备码")
    parser.add_argument("action", choices=("init", "plan", "launch"))
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--lark-cli", required=True, type=Path)
    parser.add_argument("--credential-home", required=True, type=Path)
    parser.add_argument("--hermes-home", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--scope", action="append")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        node = _validated_node(args.node)
        lark_cli = args.lark_cli
        if not lark_cli.is_absolute() or lark_cli.is_symlink() or not lark_cli.is_file() or not os.access(lark_cli, os.X_OK):
            raise FeishuOAuthError("飞书连接工具必须是已核验且可执行的绝对路径。")
        credential_home = _existing_directory(args.credential_home, "飞书凭据目录")
        hermes_home = _existing_directory(args.hermes_home, "Hermes 工作区")
        if not PROFILE_RE.fullmatch(args.profile):
            raise FeishuOAuthError("飞书专用 Profile 名称无效。")
        scopes = tuple(dict.fromkeys(args.scope or ()))
        if set(scopes) - ALLOWED_SCOPES:
            raise FeishuOAuthError("请求包含未批准的飞书权限，已停止。")
        if args.action == "init":
            if scopes:
                raise FeishuOAuthError("创建飞书专用应用时不应提前申请业务权限。")
            print(json.dumps(initialize_application(
                node, lark_cli.resolve(strict=True),
                credential_home, hermes_home, args.profile,
            ), ensure_ascii=False, sort_keys=True))
            return 0
        if not scopes:
            raise FeishuOAuthError("飞书授权必须明确列出本次需要的最小权限。")
        if args.action == "plan":
            print(json.dumps({
                "result": "READY",
                "profile": args.profile,
                "scopes": list(scopes),
                "opens_official_page": True,
                "secrets_printed": False,
            }, ensure_ascii=False, sort_keys=True))
            return 0
        print(json.dumps(authorize(
            node, lark_cli.resolve(strict=True), credential_home,
            hermes_home, args.profile, scopes,
        ), ensure_ascii=False, sort_keys=True))
        return 0
    except (FeishuOAuthError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

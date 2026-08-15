#!/usr/bin/env python3
"""云端 Weixin 用户服务不得从 systemd manager 继承的环境键。"""
from __future__ import annotations

import re
import shlex


BLOCKED_SYSTEMD_ENV_KEYS = frozenset(
    {
        "HERMES_SHARED_AUTH_DIR",
        "HERMES_PROFILE",
        "HERMES_CONFIG",
        "HERMES_ENV",
        "HERMES_OAUTH_TRACE",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GATEWAY_ALLOW_ALL_USERS",
        "WEIXIN_ACCOUNT_ID",
        "WEIXIN_ALLOWED_USERS",
        "WEIXIN_ALLOW_ALL_USERS",
        "WEIXIN_BASE_URL",
        "WEIXIN_CDN_ALLOWLIST",
        "WEIXIN_CDN_BASE_URL",
        "WEIXIN_COPY_LINE_WIDTH",
        "WEIXIN_DM_POLICY",
        "WEIXIN_GROUP_ALLOWED_USERS",
        "WEIXIN_GROUP_POLICY",
        "WEIXIN_HOME_CHANNEL",
        "WEIXIN_HOME_CHANNEL_NAME",
        "WEIXIN_HOME_CHANNEL_THREAD_ID",
        "WEIXIN_RATE_LIMIT_CIRCUIT_OPEN_SECONDS",
        "WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD",
        "WEIXIN_RATE_LIMIT_CIRCUIT_WINDOW_SECONDS",
        "WEIXIN_SEND_CHUNK_DELAY_SECONDS",
        "WEIXIN_SEND_CHUNK_RETRIES",
        "WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS",
        "WEIXIN_SPLIT_MULTILINE_MESSAGES",
        "WEIXIN_TARGET_RE",
        "WEIXIN_TOKEN",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "NVIDIA_API_KEY",
        "LM_API_KEY",
        "GLM_API_KEY",
        "ZAI_API_KEY",
        "Z_AI_API_KEY",
        "KIMI_API_KEY",
        "KIMI_CN_API_KEY",
        "STEPFUN_API_KEY",
        "ARCEEAI_API_KEY",
        "GMI_API_KEY",
        "FIREWORKS_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_CN_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "OPENCODE_ZEN_API_KEY",
        "OPENCODE_GO_API_KEY",
        "HF_TOKEN",
        "OLLAMA_API_KEY",
        "XIAOMI_API_KEY",
        "UPSTAGE_API_KEY",
        "AZURE_FOUNDRY_API_KEY",
    }
)
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def is_blocked_systemd_key(key: str) -> bool:
    upper = key.upper()
    return (
        upper in BLOCKED_SYSTEMD_ENV_KEYS
        or upper == "GATEWAY_ALLOW_ALL_USERS"
        or upper.startswith("WEIXIN_")
        or upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
    )


def manager_environment_is_clean(text: str) -> bool:
    for raw in text.splitlines():
        key, separator, _ = raw.partition("=")
        if separator and ENV_KEY_RE.fullmatch(key) and is_blocked_systemd_key(key):
            return False
    return True


def assignment_keys(text: str) -> set[str] | None:
    try:
        assignments = shlex.split(text, posix=True)
    except ValueError:
        return None
    keys: set[str] = set()
    for assignment in assignments:
        key, separator, _ = assignment.partition("=")
        if not separator or not ENV_KEY_RE.fullmatch(key):
            return None
        keys.add(key)
    return keys


def unset_environment_keys(text: str) -> set[str] | None:
    try:
        entries = shlex.split(text, posix=True)
    except ValueError:
        return None
    keys: set[str] = set()
    for entry in entries:
        key = entry.partition("=")[0]
        if not ENV_KEY_RE.fullmatch(key):
            return None
        keys.add(key)
    return keys


def initial_environment_is_clean(data: bytes) -> bool:
    for item in data.split(b"\0"):
        if not item or b"=" not in item:
            continue
        raw_key = item.split(b"=", 1)[0]
        try:
            key = raw_key.decode("ascii")
        except UnicodeDecodeError:
            return False
        if is_blocked_systemd_key(key):
            return False
    return True


def guard_dropin_text() -> str:
    return "[Service]\nUnsetEnvironment=" + " ".join(sorted(BLOCKED_SYSTEMD_ENV_KEYS)) + "\n"

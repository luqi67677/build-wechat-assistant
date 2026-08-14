#!/usr/bin/env python3
"""由 Agent 打开不回传交互输出的系统终端，用户无需输入启动命令。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath


PROFILE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
PROVIDER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
REMOTE_SERVICE_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SSH_TARGET_RE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,252}|"
    r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}@[A-Za-z0-9][A-Za-z0-9_.-]{0,252})"
)
SECRET_PROMPT = b"Paste your API key:"
SECRET_OUTPUT_LIMIT = 65536
SECRET_INPUT_LIMIT = 4096
MACOS_SECRET_DIALOG = """
try
    activate
    set response to display dialog "请粘贴模型 API Key，然后点“确定”" default answer "" with title "微信助手安全配置" buttons {"取消", "确定"} default button "确定" cancel button "取消" with hidden answer
    return "OK" & linefeed & (text returned of response)
on error number -128
    return "CANCEL"
end try
"""


class HandoffError(RuntimeError):
    pass


def _absolute_local(value: object, label: str) -> Path:
    if not isinstance(value, str):
        raise HandoffError(f"{label}_invalid")
    path = Path(value)
    if not path.is_absolute() or any(char in value for char in ("\n", "\r", "\0")):
        raise HandoffError(f"{label}_invalid")
    return path


def _absolute_remote(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise HandoffError(f"{label}_invalid")
    if not PurePosixPath(value).is_absolute() or any(char in value for char in ("\n", "\r", "\0")):
        raise HandoffError(f"{label}_invalid")
    return value


def _interaction_command(kind: str, provider: str | None, label: str) -> list[str]:
    if not provider or not PROVIDER_RE.fullmatch(provider):
        raise HandoffError("provider_invalid")
    if kind == "model-oauth":
        return ["auth", "add", provider, "--type", "oauth"]
    if kind == "model-api-key":
        return ["auth", "add", provider, "--type", "api_key", "--label", label]
    raise HandoffError("interaction_kind_invalid")


def build_command(args: argparse.Namespace) -> list[str]:
    profile = str(args.profile or "")
    if not PROFILE_RE.fullmatch(profile) or profile == "default":
        raise HandoffError("profile_invalid")
    weixin_setup = args.kind == "weixin-setup"
    if weixin_setup and args.provider:
        raise HandoffError("provider_not_allowed_for_weixin")
    interaction = [] if weixin_setup else _interaction_command(args.kind, args.provider, profile)

    if args.mode == "ordinary":
        hermes = str(_absolute_local(args.hermes, "hermes"))
        if weixin_setup:
            helper = str(Path(__file__).with_name("setup_weixin_direct.py").resolve())
            return [
                sys.executable,
                helper,
                "run",
                "--mode",
                "ordinary",
                "--profile",
                profile,
                "--hermes",
                hermes,
            ]
        return [hermes, "-p", profile, *interaction]

    if args.mode == "protected":
        root = str(_absolute_local(args.root, "root"))
        hermes = str(_absolute_local(args.hermes, "hermes"))
        if weixin_setup:
            helper = str(Path(__file__).with_name("setup_weixin_direct.py").resolve())
            return [
                sys.executable,
                helper,
                "run",
                "--mode",
                "protected",
                "--profile",
                profile,
                "--hermes",
                hermes,
                "--root",
                root,
            ]
        guard = str(Path(__file__).with_name("isolation_guard.py").resolve())
        return [
            sys.executable,
            guard,
            "run",
            "--root",
            root,
            "--hermes",
            hermes,
            "--",
            "-p",
            profile,
            *interaction,
        ]

    if args.mode == "cloud":
        target = str(args.ssh_target or "")
        if not SSH_TARGET_RE.fullmatch(target) or target.startswith("-"):
            raise HandoffError("ssh_target_invalid")
        root = _absolute_remote(args.root, "root")
        hermes = _absolute_remote(args.hermes, "hermes")
        skill_root = _absolute_remote(args.remote_skill_root, "remote_skill_root")
        remote_python = str(args.remote_python or "")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", remote_python) or remote_python.startswith("-"):
            raise HandoffError("remote_python_invalid")
        service_user = str(args.remote_service_user or "")
        if not REMOTE_SERVICE_USER_RE.fullmatch(service_user) or service_user == "root":
            raise HandoffError("remote_service_user_invalid")
        account_switch = str(args.remote_account_switch or "")
        if weixin_setup:
            inner = [
                remote_python,
                str(PurePosixPath(skill_root) / "scripts" / "setup_weixin_direct.py"),
                "run",
                "--mode",
                "cloud",
                "--profile",
                profile,
                "--hermes",
                hermes,
                "--root",
                root,
            ]
        else:
            inner = [
                remote_python,
                str(PurePosixPath(skill_root) / "scripts" / "isolation_guard.py"),
                "run-cloud",
                "--root",
                root,
                "--hermes",
                hermes,
                "--",
                "-p",
                profile,
                *interaction,
            ]
        if account_switch == "root-runuser":
            remote = ["runuser", "--login", service_user, "--command", shlex.join(inner)]
        elif account_switch == "sudo":
            remote = ["sudo", "--login", "--user", service_user, "--", *inner]
        elif account_switch == "direct":
            explicit_ssh_user = target.split("@", 1)[0] if "@" in target else ""
            if explicit_ssh_user != service_user:
                raise HandoffError("direct_ssh_user_mismatch")
            remote = inner
        else:
            raise HandoffError("remote_account_switch_invalid")
        return ["ssh", "-tt", "--", target, shlex.join(remote)]

    raise HandoffError("mode_invalid")


def _launch_macos(command_text: str) -> None:
    script = (
        "on run argv\n"
        "tell application \"Terminal\"\n"
        "activate\n"
        "do script (item 1 of argv)\n"
        "end tell\n"
        "end run"
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, command_text],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise HandoffError("trusted_terminal_open_failed")


def _launch_windows(command: list[str]) -> None:
    creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(
            command,
            creationflags=creation_flags,
            close_fds=True,
        )
    except OSError as exc:
        raise HandoffError("trusted_terminal_open_failed") from exc


def _launch_linux(command_text: str) -> None:
    candidates = (
        ("x-terminal-emulator", ["-e", "sh", "-lc", command_text]),
        ("gnome-terminal", ["--", "sh", "-lc", command_text]),
        ("konsole", ["-e", "sh", "-lc", command_text]),
        ("xterm", ["-e", "sh", "-lc", command_text]),
    )
    for executable, suffix in candidates:
        resolved = shutil.which(executable)
        if not resolved:
            continue
        try:
            subprocess.Popen(
                [resolved, *suffix],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
        return
    raise HandoffError("trusted_terminal_open_failed")


def launch_in_terminal(command: list[str]) -> None:
    if os.name == "nt":
        _launch_windows(command)
        return
    command_text = shlex.join(command)
    if sys.platform == "darwin":
        _launch_macos(command_text)
        return
    if sys.platform.startswith("linux"):
        _launch_linux(command_text)
        return
    raise HandoffError("trusted_terminal_platform_unsupported")


def _validate_secret_input(secret: str | None) -> str:
    if secret is None:
        raise HandoffError("secret_input_cancelled")
    secret = secret.strip()
    if not secret:
        raise HandoffError("secret_input_empty")
    if len(secret) > SECRET_INPUT_LIMIT or any(char in secret for char in ("\n", "\r", "\0")):
        raise HandoffError("secret_input_invalid")
    return secret


def _request_macos_secret() -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", MACOS_SECRET_DIALOG],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HandoffError("native_secret_dialog_unavailable") from exc
    if result.returncode != 0:
        raise HandoffError("native_secret_dialog_unavailable")
    output = result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout
    if output == "CANCEL":
        raise HandoffError("secret_input_cancelled")
    if not output.startswith("OK\n"):
        raise HandoffError("native_secret_dialog_unavailable")
    return _validate_secret_input(output[3:])


def _request_tk_secret() -> str:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        secret = simpledialog.askstring(
            "微信助手安全配置",
            "请粘贴模型 API Key，然后点“确定”",
            show="*",
            parent=root,
        )
        root.destroy()
    except Exception as exc:
        raise HandoffError("native_secret_dialog_unavailable") from exc
    return _validate_secret_input(secret)


def _request_native_secret() -> str:
    if sys.platform == "darwin":
        return _request_macos_secret()
    return _request_tk_secret()


def _stop_handoff_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        raise HandoffError("secret_handoff_stop_failed") from exc


def run_native_api_key_handoff(
    command: list[str],
    provider: str,
    *,
    prompt_timeout: float = 60,
    completion_timeout: float = 30,
    use_pty: bool = False,
) -> None:
    master_fd: int | None = None
    slave_fd: int | None = None
    try:
        if use_pty:
            if os.name == "nt":
                raise HandoffError("native_secret_tty_unavailable")
            import pty

            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = None
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
    except OSError as exc:
        raise HandoffError("secret_handoff_start_failed") from exc
    if not use_pty and (process.stdin is None or process.stdout is None):
        _stop_handoff_process(process)
        raise HandoffError("secret_handoff_pipe_unavailable")

    prompt_seen = threading.Event()
    reader_done = threading.Event()
    output_overflow = threading.Event()
    chunks: list[bytes] = []

    def collect_output() -> None:
        tail = b""
        total = 0
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 256) if use_pty and master_fd is not None else process.stdout.read1(256)
                except OSError:
                    break
                if not chunk:
                    break
                total += len(chunk)
                if total > SECRET_OUTPUT_LIMIT:
                    output_overflow.set()
                    break
                chunks.append(chunk)
                tail = (tail + chunk)[-2048:]
                if SECRET_PROMPT in tail:
                    prompt_seen.set()
        finally:
            reader_done.set()

    reader = threading.Thread(target=collect_output, daemon=True)
    reader.start()
    try:
        if not prompt_seen.wait(timeout=prompt_timeout):
            raise HandoffError(
                "secret_handoff_output_too_large"
                if output_overflow.is_set()
                else "secret_prompt_not_observed"
            )

        secret = _request_native_secret()
        secret_bytes = secret.encode("utf-8")
        if use_pty and master_fd is not None:
            os.write(master_fd, secret_bytes + b"\n")
        else:
            process.stdin.write(secret_bytes + b"\n")
            process.stdin.flush()
            process.stdin.close()
        secret = ""
        try:
            returncode = process.wait(timeout=completion_timeout)
        except subprocess.TimeoutExpired as exc:
            raise HandoffError("secret_handoff_incomplete") from exc
        reader_done.wait(timeout=3)
        output = b"".join(chunks)
        secret_echoed = secret_bytes in output
        secret_bytes = b""
        if secret_echoed:
            raise HandoffError("secret_echo_detected")
        success_marker = f"Added {provider} credential".encode("utf-8")
        if returncode != 0 or success_marker not in output or output_overflow.is_set():
            raise HandoffError("credential_save_unverified")
    except HandoffError:
        _stop_handoff_process(process)
        raise
    except (BrokenPipeError, OSError) as exc:
        _stop_handoff_process(process)
        raise HandoffError("secret_handoff_incomplete") from exc
    finally:
        reader_done.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()
        if slave_fd is not None:
            os.close(slave_fd)
        if master_fd is not None:
            os.close(master_fd)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="由 Agent 打开受信交互窗口")
    parser.add_argument("action", choices=("plan", "launch"))
    parser.add_argument("--mode", choices=("ordinary", "protected", "cloud"), required=True)
    parser.add_argument("--kind", choices=("model-oauth", "model-api-key", "weixin-setup"), required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--hermes", required=True)
    parser.add_argument("--root")
    parser.add_argument("--ssh-target")
    parser.add_argument("--remote-skill-root")
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--remote-service-user")
    parser.add_argument("--remote-account-switch", choices=("root-runuser", "sudo", "direct"))
    return parser.parse_args(argv)


def _result(state: str, *, native_secret_dialog: bool = False) -> dict[str, object]:
    return {
        "result": state,
        "trusted_terminal": True,
        "native_secret_dialog": native_secret_dialog,
        "credential_saved": state == "SAVED",
        "user_terminal_typing_required": False,
        "command_printed": False,
        "secrets_printed": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        command = build_command(args)
        native_secret_dialog = args.kind == "model-api-key" and (
            args.mode == "cloud" or os.name != "nt"
        )
        if args.action == "launch":
            if native_secret_dialog:
                run_native_api_key_handoff(
                    command,
                    str(args.provider),
                    use_pty=args.mode != "cloud" and os.name != "nt",
                )
            else:
                launch_in_terminal(command)
        state = "SAVED" if args.action == "launch" and native_secret_dialog else (
            "OPENED" if args.action == "launch" else "READY"
        )
        print(json.dumps(_result(state, native_secret_dialog=native_secret_dialog), sort_keys=True))
        return 0
    except HandoffError as exc:
        print(
            json.dumps(
                {
                    "result": "ERROR",
                    "error": str(exc),
                    "command_printed": False,
                    "secrets_printed": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

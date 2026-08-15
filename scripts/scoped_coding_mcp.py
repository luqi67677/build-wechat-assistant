#!/usr/bin/env python3
"""Stage Codex changes in an isolated git worktree before explicit application.

This MCP server never exposes a generic shell.  Codex works in a disposable
detached worktree; the user's project is changed only by a second, separately
confirmed tool call that applies the reviewed patch.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from scoped_knowledge_mcp import (
    KnowledgeError,
    McpContext,
    _private_directory,
    require_confirmation,
    sha256_bytes,
)
from scoped_feishu_mcp import FeishuError, validate_node_runtime

MAX_TASK_CHARS = 4000
MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_CHANGED_FILES = 50
TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SENSITIVE_PATH_RE = re.compile(
    r"(^|/)(?:\.git(?:/|$)|\.codex(?:/|$)|\.env(?:\.|$)|AGENTS\.md$|CLAUDE\.md$|\.cursorrules$)",
    re.IGNORECASE,
)
CODEX_AUTH_RECOVERY = (
    "Codex 当前未登录或登录已失效；主项目没有被修改。请让 Agent 先确认用户 Codex 使用方式："
    "OpenAI 账号在可信窗口启动官方登录或设备登录，自定义模型（如 DeepSeek）走 `codex login --with-api-key`，"
    "确认 `codex login status` 成功后重试；不要在聊天里发送 API key 或 token。"
)
CODEX_HOME_WRITE_RECOVERY = (
    "Codex 专用登录目录当前不可写；主项目没有被修改。请让 Agent 检查该目录属于当前运行账号、"
    "POSIX 权限为 0700，并确认运行微信助手的进程能写入后再重试；不要覆盖用户其他 Codex 配置。"
)


class CodingError(KnowledgeError):
    pass


def _run(command: list[str], *, cwd: Path | None = None, input_text: str | None = None,
         timeout: int = 120, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodingError("编程工具未能正常运行；项目没有被应用任何修改。") from exc
    return result


def _git(root: Path, *args: str, timeout: int = 120) -> str:
    result = _run(["git", "-C", str(root), *args], timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1][:300] if detail else "未知 Git 错误"
        raise CodingError(f"Git 检查失败：{message}")
    return result.stdout


def _safe_codex_env(codex_home: Path | None = None, node: Path | None = None) -> dict[str, str]:
    allowed = (
        "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
        "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    )
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    if node is not None:
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(
            dict.fromkeys([str(node.parent), *filter(None, current.split(os.pathsep))])
        )
    return env


def _codex_requires_node(codex: Path) -> bool:
    try:
        with codex.open("rb") as stream:
            first_line = stream.readline(256).strip()
    except OSError:
        return False
    return bool(re.fullmatch(rb"#!\s*/usr/bin/env(?:\s+-S)?\s+node(?:\s+.*)?", first_line))


def _codex_command(codex: Path, worktree: Path) -> list[str]:
    return [
        str(codex),
        "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
        "-c", "sandbox_workspace_write.network_access=false",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "-C", str(worktree),
        "-",
    ]


def _codex_failure_message(result: subprocess.CompletedProcess[str]) -> str:
    output = f"{result.stdout}\n{result.stderr}".lower()
    if (
        "readonly database" in output
        or "attempt to write a readonly database" in output
        or ("state db" in output and "operation not permitted" in output)
    ):
        return CODEX_HOME_WRITE_RECOVERY
    if any(marker in output for marker in (
        "not logged in", "login required", "authentication required", "unauthorized",
        "token expired", "invalid token", "401",
    )):
        return CODEX_AUTH_RECOVERY
    if any(marker in output for marker in ("rate limit", "quota", "credit", "insufficient")):
        return "Codex 当前额度、速率或计费状态不允许完成任务；主项目没有被修改。请在 OpenAI 官方页面核对后重试。"
    return "Codex 没有成功完成隔离任务；主项目没有被修改。请查看 Codex 登录、额度和服务状态后重试。"


def _require_codex_login(codex: Path, codex_home: Path | None = None,
                         node: Path | None = None) -> None:
    result = _run(
        [str(codex), "login", "status"],
        timeout=30,
        env=_safe_codex_env(codex_home, node),
    )
    if result.returncode == 0:
        return
    output = f"{result.stdout}\n{result.stderr}".lower()
    if any(marker in output for marker in (
        "not logged in", "login required", "authentication required", "unauthorized",
        "token expired", "invalid token", "401",
    )):
        raise CodingError(CODEX_AUTH_RECOVERY)
    raise CodingError(
        "Codex 登录状态无法核验；主项目没有被修改。请让 Agent 只读运行 `codex login status`，"
        "修复或升级当前 Codex 后再重试，不要跳过这项检查。"
    )


def _require_codex_cli_contract(codex: Path, codex_home: Path | None = None,
                                node: Path | None = None) -> None:
    env = _safe_codex_env(codex_home, node)
    top = _run([str(codex), "--help"], timeout=30, env=env)
    execute = _run([str(codex), "exec", "--help"], timeout=30, env=env)
    top_help = f"{top.stdout}\n{top.stderr}"
    exec_help = f"{execute.stdout}\n{execute.stderr}"
    top_required = (
        "--sandbox",
        "--ask-for-approval",
        "--config",
        "workspace-write",
        "never",
    )
    exec_required = ("--ephemeral", "--ignore-user-config", "--cd")
    if (
        top.returncode != 0
        or execute.returncode != 0
        or any(flag not in top_help for flag in top_required)
        or any(flag not in exec_help for flag in exec_required)
    ):
        raise CodingError(
            "当前 Codex CLI 缺少受控编程所需的安全参数；主项目没有被修改。"
            "请让 Agent 通过 Codex 官方安装渠道修复或升级，并重新核对 `codex --help` 与 "
            "`codex exec --help` 后再接入；不会降级为无限制模式。"
        )


@dataclass(frozen=True)
class CodingStore:
    project: Path
    state_dir: Path
    codex: Path
    codex_home: Path | None = None
    node: Path | None = None
    timeout_seconds: int = 1800

    @classmethod
    def open(cls, project: Path, state_dir: Path, codex: Path,
             timeout_seconds: int = 1800, codex_home: Path | None = None,
             node: Path | None = None) -> CodingStore:
        resolved_project = project.resolve(strict=True)
        if project.is_symlink() or not resolved_project.is_dir():
            raise CodingError("代码项目目录无效，请重新选择。")
        top = _git(resolved_project, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve(strict=True) != resolved_project:
            raise CodingError("请选择 Git 仓库的根目录，不要选择它的父目录或子目录。")
        if (resolved_project / ".gitmodules").exists():
            raise CodingError("当前安全模式暂不支持包含 Git submodule 的项目。")
        resolved_state = _private_directory(state_dir)
        if resolved_state == resolved_project or resolved_project in resolved_state.parents:
            raise CodingError("编程状态目录不能放在代码项目里面。")
        resolved_codex = codex.resolve(strict=True)
        if not resolved_codex.is_file() or not os.access(resolved_codex, os.X_OK):
            raise CodingError("没有找到可执行的 Codex CLI。")
        resolved_node: Path | None = None
        if node is not None:
            try:
                resolved_node, _node_version = validate_node_runtime(node)
            except FeishuError as exc:
                raise CodingError("Codex 的 Node.js 运行时无效；请让 Agent 重新核验真实绝对路径。") from exc
        if _codex_requires_node(resolved_codex) and resolved_node is None:
            raise CodingError(
                "当前 Codex 是需要 Node.js 的启动脚本，但隔离 Gateway 不继承用户 PATH。"
                "请让 Agent 核验 Node.js 身份后，用 `--node <Node.js真实绝对路径>` 重新接入。"
            )
        resolved_codex_home = _private_directory(codex_home) if codex_home is not None else None
        if resolved_codex_home is not None:
            if resolved_codex_home == resolved_project or resolved_project in resolved_codex_home.parents:
                raise CodingError("Codex 专用登录目录不能放在代码项目里面。")
            if resolved_codex_home == resolved_state or resolved_state in resolved_codex_home.parents:
                raise CodingError("Codex 专用登录目录不能放在编程状态目录里面。")
        if not 30 <= timeout_seconds <= 3600:
            raise CodingError("Codex 任务超时必须在 30 到 3600 秒之间。")
        (resolved_state / "tasks").mkdir(mode=0o700, exist_ok=True)
        (resolved_state / "worktrees").mkdir(mode=0o700, exist_ok=True)
        return cls(resolved_project, resolved_state, resolved_codex, resolved_codex_home,
                   resolved_node, timeout_seconds)

    def _head(self) -> str:
        return _git(self.project, "rev-parse", "HEAD").strip()

    def _require_clean(self) -> None:
        if _git(self.project, "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise CodingError("项目里已有未提交修改；为避免覆盖，请先自行保存或提交，再开始微信编程任务。")

    def project_status(self) -> dict[str, Any]:
        status = _git(self.project, "status", "--porcelain=v1", "--untracked-files=all")
        return {
            "git_repository": True,
            "clean": not bool(status.strip()),
            "branch": _git(self.project, "branch", "--show-current").strip() or "detached",
            "head": self._head(),
            "submodules_supported": False,
        }

    def _task_path(self, task_id: str) -> Path:
        if not TASK_ID_RE.fullmatch(task_id):
            raise CodingError("编程任务编号无效。")
        return self.state_dir / "tasks" / f"{task_id}.json"

    def _write_task(self, task_id: str, payload: dict[str, Any], *, exclusive: bool = False) -> None:
        path = self._task_path(task_id)
        if exclusive:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return
        descriptor, raw_temp = tempfile.mkstemp(prefix=".bwa-", dir=path.parent)
        temp = Path(raw_temp)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _load_task(self, task_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._task_path(task_id)
        if path.is_symlink() or not path.is_file():
            raise CodingError("没有找到这个编程任务。")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodingError("编程任务记录损坏，已停止。") from exc
        return path, payload

    def _cleanup_worktree(self, worktree: Path) -> None:
        if not worktree.exists():
            return
        result = _run(["git", "-C", str(self.project), "worktree", "remove", "--force", str(worktree)])
        if result.returncode != 0 and worktree.exists():
            raise CodingError("隔离工作区未能安全清理；主项目没有被修改，请先检查私有状态目录。")

    def _changed_paths(self, worktree: Path) -> list[str]:
        output = _git(worktree, "diff", "--name-only", "--no-ext-diff")
        paths = [line for line in output.splitlines() if line]
        if len(paths) > MAX_CHANGED_FILES:
            raise CodingError(f"本次修改超过 {MAX_CHANGED_FILES} 个文件，已拒绝生成可应用补丁。")
        for path in paths:
            if path.startswith("/") or ".." in Path(path).parts or SENSITIVE_PATH_RE.search(path):
                raise CodingError(f"本次修改触及受保护文件 {path}，已拒绝生成可应用补丁。")
        return paths

    def _file_states(self, root: Path, paths: list[str]) -> dict[str, str | None]:
        states: dict[str, str | None] = {}
        for relative in paths:
            target = root / relative
            if not target.exists():
                states[relative] = None
                continue
            if target.is_symlink() or not target.is_file():
                raise CodingError(f"本次修改产生了不支持的文件类型：{relative}")
            states[relative] = sha256_bytes(target.read_bytes())
        return states

    def prepare(self, task: str) -> dict[str, Any]:
        task = task.strip()
        if not task or len(task) > MAX_TASK_CHARS:
            raise CodingError("代码任务不能为空且不能超过 4000 个字符。")
        _require_codex_login(self.codex, self.codex_home, self.node)
        self._require_clean()
        head = self._head()
        task_id = secrets.token_hex(16)
        worktree = self.state_dir / "worktrees" / task_id
        add = _run(["git", "-C", str(self.project), "worktree", "add", "--detach", str(worktree), head])
        if add.returncode != 0:
            raise CodingError("无法创建隔离代码工作区；主项目没有被修改。")
        try:
            prompt = (
                "你正在一个一次性、隔离的 Git worktree 中工作。完成下面这一个代码任务，进行必要的本地测试。"
                "只修改完成任务所需的项目文件；不要提交、推送、发布、部署，不要访问项目外文件，不要读取或写入秘密。"
                "任务：\n" + task
            )
            command = _codex_command(self.codex, worktree)
            result = _run(
                command, cwd=worktree, input_text=prompt, timeout=self.timeout_seconds,
                env=_safe_codex_env(self.codex_home, self.node),
            )
            if result.returncode != 0:
                raise CodingError(_codex_failure_message(result))
            if _git(worktree, "rev-parse", "HEAD").strip() != head:
                raise CodingError("Codex 改写了 Git 历史，已拒绝生成可应用补丁。")
            _git(worktree, "add", "-N", ".")
            _git(worktree, "diff", "--check")
            paths = self._changed_paths(worktree)
            if not paths:
                raise CodingError("Codex 没有产生代码修改；主项目保持不变。")
            patch = _git(worktree, "diff", "--binary", "--full-index", "--no-ext-diff", "--no-color")
            patch_bytes = patch.encode("utf-8")
            if len(patch_bytes) > MAX_PATCH_BYTES:
                raise CodingError("本次补丁超过 2 MB，已拒绝应用。")
            if "GIT binary patch" in patch:
                raise CodingError("当前安全模式不应用二进制文件修改。")
            summary = (result.stdout.strip() or "Codex 已完成修改。")[-4000:]
            payload = {
                "status": "prepared", "head": head, "task": task, "paths": paths,
                "patch": patch, "patch_sha256": sha256_bytes(patch_bytes), "summary": summary,
                "after_files": self._file_states(worktree, paths),
            }
            self._write_task(task_id, payload, exclusive=True)
            return {
                "task_id": task_id, "status": "prepared", "head": head, "changed_files": paths,
                "patch_sha256": payload["patch_sha256"], "summary": summary,
                "next_step": "请先查看变更摘要；只有再次确认后才会应用到原项目。",
            }
        finally:
            self._cleanup_worktree(worktree)

    def inspect(self, task_id: str) -> dict[str, Any]:
        _, payload = self._load_task(task_id)
        return {key: payload[key] for key in (
            "status", "head", "task", "paths", "patch_sha256", "summary"
        ) if key in payload}

    def apply(self, task_id: str) -> dict[str, Any]:
        _path, payload = self._load_task(task_id)
        if payload.get("status") != "prepared":
            raise CodingError("这个任务不在等待应用状态，不能重复应用。")
        self._require_clean()
        if self._head() != payload.get("head"):
            raise CodingError("项目版本已变化，已停止应用；请重新创建编程任务。")
        patch = payload.get("patch")
        if not isinstance(patch, str) or sha256_bytes(patch.encode("utf-8")) != payload.get("patch_sha256"):
            raise CodingError("补丁校验失败，已停止应用。")
        check = _run(["git", "-C", str(self.project), "apply", "--check", "-"], input_text=patch)
        if check.returncode != 0:
            raise CodingError("补丁与当前项目不兼容，已停止应用。")
        applied = _run(["git", "-C", str(self.project), "apply", "-"], input_text=patch)
        if applied.returncode != 0:
            raise CodingError("补丁应用失败；请检查项目状态，任务不会自动重试。")
        payload["status"] = "applied"
        try:
            self._write_task(task_id, payload)
        except OSError as exc:
            restored = _run(
                ["git", "-C", str(self.project), "apply", "--reverse", "--check", "-"],
                input_text=patch,
            )
            if restored.returncode == 0:
                restored = _run(
                    ["git", "-C", str(self.project), "apply", "--reverse", "-"],
                    input_text=patch,
                )
            if restored.returncode == 0:
                raise CodingError("任务记录保存失败，刚应用的补丁已自动撤销；项目保持原状，请修复私有状态目录后重试。") from exc
            raise CodingError("任务记录保存失败，而且补丁未能自动撤销；请停止后续修改并人工检查项目。") from exc
        return {"task_id": task_id, "status": "applied", "changed_files": payload["paths"]}

    def rollback(self, task_id: str) -> dict[str, Any]:
        _, payload = self._load_task(task_id)
        if payload.get("status") != "applied":
            raise CodingError("这个任务没有处于已应用状态，不能回滚。")
        if self._head() != payload.get("head"):
            raise CodingError("项目提交版本已变化，已停止自动回滚。")
        status_lines = _git(self.project, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
        dirty_paths = {line[3:].strip('"') for line in status_lines if len(line) >= 4}
        expected_paths = set(payload.get("paths", []))
        current_states = self._file_states(self.project, list(expected_paths))
        if dirty_paths != expected_paths or current_states != payload.get("after_files"):
            raise CodingError("应用后项目又发生了其他修改，已停止回滚，避免覆盖新工作。")
        reversed_patch = _run(
            ["git", "-C", str(self.project), "apply", "--reverse", "--check", "-"],
            input_text=payload["patch"],
        )
        if reversed_patch.returncode != 0:
            raise CodingError("回滚校验失败，项目没有被继续修改。")
        result = _run(
            ["git", "-C", str(self.project), "apply", "--reverse", "-"],
            input_text=payload["patch"],
        )
        if result.returncode != 0:
            raise CodingError("回滚失败，请停止后续修改并人工检查。")
        payload["status"] = "rolled_back"
        try:
            self._write_task(task_id, payload)
        except OSError as exc:
            restored = _run(
                ["git", "-C", str(self.project), "apply", "--check", "-"],
                input_text=payload["patch"],
            )
            if restored.returncode == 0:
                restored = _run(
                    ["git", "-C", str(self.project), "apply", "-"],
                    input_text=payload["patch"],
                )
            if restored.returncode == 0:
                raise CodingError("回滚记录保存失败，项目已自动恢复到回滚前状态；请修复私有状态目录后重试。") from exc
            raise CodingError("回滚记录保存失败，而且项目未能恢复到回滚前状态；请停止后续修改并人工检查。") from exc
        return {"task_id": task_id, "status": "rolled_back", "changed_files": payload["paths"]}


def build_server(store: CodingStore) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise CodingError("当前 Python 环境缺少 MCP 运行库，请使用 Hermes 自带的 Python 环境。") from exc

    server = FastMCP("微信助手受控编程")

    @server.tool()
    def inspect_code_project() -> dict[str, Any]:
        """只读检查已批准 Git 项目是否干净、可开始受控编程。"""
        return store.project_status()

    @server.tool()
    async def prepare_code_change(task: str, ctx: McpContext) -> dict[str, Any]:
        """确认后把项目内容交给 Codex，在一次性 worktree 生成待审补丁；不改原项目。"""
        await require_confirmation(
            ctx,
            "将把已批准项目中完成任务所需的代码交给 OpenAI Codex，可能消耗你的套餐或 API 额度。"
            "Codex 只在一次性工作区准备修改，不提交、不推送、不发布。是否确认开始？",
        )
        return store.prepare(task)

    @server.tool()
    def inspect_code_change(task_id: str) -> dict[str, Any]:
        """查看待应用编程任务的状态、摘要、文件清单和补丁校验值。"""
        return store.inspect(task_id)

    @server.tool()
    async def apply_code_change(task_id: str, ctx: McpContext) -> dict[str, Any]:
        """经第二次确认后把已校验补丁应用到原项目；不提交、不推送、不发布。"""
        await require_confirmation(ctx, "将把一项已校验的待审补丁应用到已批准项目。不会提交或发布。是否确认？")
        return store.apply(task_id)

    @server.tool()
    async def rollback_code_change(task_id: str, ctx: McpContext) -> dict[str, Any]:
        """经确认后回滚本工具刚应用的补丁；存在后续修改时拒绝覆盖。"""
        await require_confirmation(ctx, "将回滚本连接刚应用的一项补丁。若项目已有后续修改会自动拒绝。是否确认？")
        return store.rollback(task_id)

    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行受控 Codex 编程 MCP")
    parser.add_argument("--project", required=True, type=Path, help="用户批准的干净 Git 仓库根目录")
    parser.add_argument("--state-dir", required=True, type=Path, help="项目外的私有任务状态目录")
    parser.add_argument("--codex", required=True, type=Path, help="已安装 Codex CLI 的绝对路径")
    parser.add_argument("--codex-home", type=Path, help="可选：与用户全局配置隔离的 Codex 专用登录目录")
    parser.add_argument("--node", type=Path, help="可选：npm 版 Codex 启动脚本需要的 Node.js 真实绝对路径")
    parser.add_argument("--timeout", type=int, default=1800, help="单次 Codex 任务超时秒数（30-3600）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = (args.project, args.state_dir, args.codex)
    paths += ((args.codex_home,) if args.codex_home else ()) + ((args.node,) if args.node else ())
    if not all(path.is_absolute() for path in paths):
        raise SystemExit("项目、状态目录、Codex 和 Codex 专用登录目录都必须使用绝对路径。")
    try:
        store = CodingStore.open(
            args.project, args.state_dir, args.codex, args.timeout, args.codex_home, args.node
        )
        _require_codex_cli_contract(store.codex, store.codex_home, store.node)
        build_server(store).run(transport="stdio")
    except KnowledgeError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

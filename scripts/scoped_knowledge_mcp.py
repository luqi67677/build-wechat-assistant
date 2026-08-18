#!/usr/bin/env python3
"""A path-scoped knowledge-base MCP server with recoverable writes.

The server deliberately exposes no generic filesystem or shell tool.  Reads are
limited to one approved directory.  Every mutation uses MCP elicitation and is
recorded in a private state directory so it can be rolled back safely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from mcp.server.fastmcp import Context as McpContext
except ImportError:  # Core logic and release tests do not require the optional runtime.
    class McpContext:  # type: ignore[no-redef]
        pass


MAX_FILE_BYTES = 1024 * 1024
MAX_WRITE_BYTES = 256 * 1024
MAX_RESULTS = 50
ALLOWED_SUFFIXES = frozenset({".md", ".txt"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WEIXIN_APPROVAL_GUIDANCE = (
    "请只在发起本次请求的同一界面，于 2 分钟内按当前提示确认："
    "微信中回复 /approve 或 /deny；若当前界面显示确认菜单，请按菜单选择。"
    "在 Codex、飞书或其他无关窗口回复不会生效。"
)


class KnowledgeError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def initialize_empty_root(path: Path) -> Path:
    """Create one user-approved empty knowledge directory without overwriting anything."""
    if not path.is_absolute():
        raise KnowledgeError("新知识库必须使用用户确认过的绝对路径。")
    if path.exists() or path.is_symlink():
        raise KnowledgeError("目标位置已经存在；为避免覆盖或误用已有资料，已停止创建。")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise KnowledgeError("新知识库的上级文件夹不存在，请重新选择位置。") from exc
    if path.parent.is_symlink() or not parent.is_dir():
        raise KnowledgeError("新知识库的上级文件夹无效，请重新选择位置。")
    try:
        path.mkdir(mode=0o700)
        resolved = path.resolve(strict=True)
        details = resolved.stat()
        if resolved.parent != parent or path.is_symlink() or not resolved.is_dir():
            raise KnowledgeError("新知识库目录无法安全核验，已停止。")
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise KnowledgeError("新知识库目录不属于当前运行账号，已停止。")
        if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
            raise KnowledgeError("新知识库目录仍允许其他账号访问，已停止。")
    except Exception:
        try:
            path.rmdir()
        except OSError:
            pass
        raise
    return resolved


def _private_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise KnowledgeError("状态目录无法创建或访问，请重新选择一个私有目录。") from exc
    if path.is_symlink() or not path.is_dir():
        raise KnowledgeError("状态目录无效，请重新选择一个私有目录。")
    try:
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except OSError as exc:
        raise KnowledgeError("状态目录无法创建或访问，请重新选择一个私有目录。") from exc
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise KnowledgeError("状态目录不属于当前运行账号，已停止。")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        try:
            resolved.chmod(0o700)
        except OSError as exc:
            raise KnowledgeError("状态目录权限无法收紧，请重新选择一个私有目录。") from exc
        try:
            remaining_mode = stat.S_IMODE(resolved.stat().st_mode)
        except OSError as exc:
            raise KnowledgeError("状态目录权限无法复核，请重新选择一个私有目录。") from exc
        if remaining_mode & 0o077:
            raise KnowledgeError("状态目录仍允许其他账号访问，已停止。")
    return resolved


@dataclass(frozen=True)
class KnowledgeStore:
    root: Path
    state_dir: Path
    writable: bool = False

    @classmethod
    def open(cls, root: Path, state_dir: Path, writable: bool = False) -> KnowledgeStore:
        resolved_root = root.resolve(strict=True)
        if root.is_symlink() or not resolved_root.is_dir():
            raise KnowledgeError("知识库目录无效，请重新选择一个真实目录。")
        resolved_state = _private_directory(state_dir)
        if resolved_state == resolved_root or resolved_root in resolved_state.parents:
            raise KnowledgeError("状态目录不能放在知识库里面，请选择知识库外的私有目录。")
        return cls(resolved_root, resolved_state, writable)

    def _relative(self, raw: str) -> PurePosixPath:
        if not isinstance(raw, str) or not raw.strip() or "\\" in raw or "\x00" in raw:
            raise KnowledgeError("文件名无效，请使用知识库内的相对路径。")
        relative = PurePosixPath(raw.strip())
        if relative.is_absolute() or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
            raise KnowledgeError("文件名越出允许范围，请使用知识库内的非隐藏相对路径。")
        if relative.suffix.lower() not in ALLOWED_SUFFIXES:
            raise KnowledgeError("仅允许 Markdown 或纯文本文件（.md、.txt）。")
        return relative

    def _target(self, raw: str, *, must_exist: bool) -> tuple[PurePosixPath, Path]:
        relative = self._relative(raw)
        target = self.root.joinpath(*relative.parts)
        parent = target.parent
        try:
            resolved_parent = parent.resolve(strict=True)
        except FileNotFoundError as exc:
            raise KnowledgeError("目标文件夹不存在，请先选择已有文件夹。") from exc
        if resolved_parent != self.root and self.root not in resolved_parent.parents:
            raise KnowledgeError("目标路径越出允许的知识库范围。")
        if parent.is_symlink() or any(part.is_symlink() for part in [self.root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts))]):
            raise KnowledgeError("目标路径包含符号链接，已拒绝访问。")
        if must_exist:
            if not target.exists() or not target.is_file() or target.is_symlink():
                raise KnowledgeError("指定笔记不存在或不是普通文件。")
            resolved_target = target.resolve(strict=True)
            if resolved_target.parent != resolved_parent:
                raise KnowledgeError("目标文件越出允许范围。")
        elif target.exists() or target.is_symlink():
            raise KnowledgeError("同名笔记已经存在，请换一个文件名。")
        return relative, target

    def _read_bytes(self, target: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            raise KnowledgeError("笔记无法安全打开，可能已被移动或替换。") from exc
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise KnowledgeError("指定笔记不是普通文件，已停止读取。")
            if opened.st_size > MAX_FILE_BYTES:
                raise KnowledgeError("笔记超过 1 MB，当前安全模式不读取。")
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise KnowledgeError("笔记超过 1 MB，当前安全模式不读取。")
        return data

    def list_notes(self) -> list[str]:
        notes: list[str] = []
        for current, directories, files in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name for name in directories
                if not name.startswith(".") and not (current_path / name).is_symlink()
            )
            for name in sorted(files):
                path = current_path / name
                if name.startswith(".") or path.is_symlink() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                notes.append(path.relative_to(self.root).as_posix())
                if len(notes) >= MAX_RESULTS:
                    return notes
        return notes

    def read_note(self, path: str) -> dict[str, Any]:
        relative, target = self._target(path, must_exist=True)
        data = self._read_bytes(target)
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeError("笔记不是 UTF-8 文本，当前安全模式不读取。") from exc
        return {"path": relative.as_posix(), "sha256": sha256_bytes(data), "content": content}

    def search_notes(self, query: str) -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        if not needle or len(needle) > 200:
            raise KnowledgeError("搜索词不能为空且不能超过 200 个字符。")
        matches: list[dict[str, Any]] = []
        for relative in self.list_notes():
            try:
                note = self.read_note(relative)
            except KnowledgeError:
                continue
            for number, line in enumerate(note["content"].splitlines(), 1):
                if needle in line.casefold():
                    matches.append({"path": relative, "line": number, "excerpt": line[:240]})
                    if len(matches) >= MAX_RESULTS:
                        return matches
        return matches

    def _require_write_mode(self) -> None:
        if not self.writable:
            raise KnowledgeError("当前连接是只读模式；请先明确开启知识库写入。")

    def _encode_content(self, content: str) -> bytes:
        if not isinstance(content, str):
            raise KnowledgeError("笔记内容必须是文本。")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise KnowledgeError("单次写入不能超过 256 KB。")
        return data

    def _receipt_path(self, change_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", change_id):
            raise KnowledgeError("变更编号无效。")
        return self.state_dir / f"{change_id}.json"

    def _write_receipt(self, payload: dict[str, Any]) -> str:
        change_id = secrets.token_hex(16)
        receipt = self._receipt_path(change_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(receipt, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            receipt.unlink(missing_ok=True)
            raise
        return change_id

    def _load_receipt(self, change_id: str) -> tuple[Path, dict[str, Any]]:
        receipt = self._receipt_path(change_id)
        if receipt.is_symlink() or not receipt.is_file():
            raise KnowledgeError("没有找到这个可回滚变更。")
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeError("变更记录损坏，已停止回滚。") from exc
        if payload.get("status") != "active":
            raise KnowledgeError("这个变更已经回滚，不能重复操作。")
        return receipt, payload

    def _atomic_write(self, target: Path, data: bytes, mode: int = 0o600) -> None:
        descriptor, raw_temp = tempfile.mkstemp(prefix=".bwa-", dir=target.parent)
        temp = Path(raw_temp)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, mode & 0o777)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if target.is_symlink():
                raise KnowledgeError("目标在写入前变成了符号链接，已停止。")
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

    def create_note(self, path: str, content: str) -> dict[str, str]:
        self._require_write_mode()
        relative, target = self._target(path, must_exist=False)
        data = self._encode_content(content)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        digest = sha256_bytes(data)
        try:
            change_id = self._write_receipt({
                "kind": "create", "path": relative.as_posix(), "after_sha256": digest, "status": "active"
            })
        except Exception:
            try:
                if sha256_bytes(self._read_bytes(target)) == digest:
                    target.unlink()
            except (KnowledgeError, OSError):
                pass
            raise
        return {"change_id": change_id, "path": relative.as_posix(), "sha256": digest}

    def update_note(self, path: str, content: str, expected_sha256: str) -> dict[str, str]:
        self._require_write_mode()
        if not SHA256_RE.fullmatch(expected_sha256):
            raise KnowledgeError("更新需要上一版笔记的 SHA-256，以防覆盖别人的新修改。")
        relative, target = self._target(path, must_exist=True)
        before = self._read_bytes(target)
        before_digest = sha256_bytes(before)
        if before_digest != expected_sha256:
            raise KnowledgeError("笔记已经变化，已停止覆盖；请重新读取后再修改。")
        data = self._encode_content(content)
        backup_name = f"{secrets.token_hex(16)}.bak"
        backup = self.state_dir / backup_name
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(before)
            handle.flush()
            os.fsync(handle.fileno())
        current_mode = target.stat().st_mode
        if sha256_bytes(self._read_bytes(target)) != expected_sha256:
            backup.unlink(missing_ok=True)
            raise KnowledgeError("笔记在写入前再次变化，已停止覆盖。")
        self._atomic_write(target, data, current_mode)
        after_digest = sha256_bytes(data)
        try:
            change_id = self._write_receipt({
                "kind": "update", "path": relative.as_posix(), "before_sha256": before_digest,
                "after_sha256": after_digest, "backup": backup_name, "status": "active"
            })
        except Exception:
            try:
                if sha256_bytes(self._read_bytes(target)) == after_digest:
                    self._atomic_write(target, before, current_mode)
                    backup.unlink(missing_ok=True)
            except (KnowledgeError, OSError):
                pass
            raise
        return {"change_id": change_id, "path": relative.as_posix(), "sha256": after_digest}

    def rollback(self, change_id: str) -> dict[str, str]:
        self._require_write_mode()
        receipt, payload = self._load_receipt(change_id)
        _, target = self._target(payload.get("path", ""), must_exist=True)
        current = self._read_bytes(target)
        if sha256_bytes(current) != payload.get("after_sha256"):
            raise KnowledgeError("文件在本次变更后又被修改，已停止回滚，避免覆盖新内容。")
        kind = payload.get("kind")
        if kind == "create":
            target.unlink()
        elif kind == "update":
            backup_name = payload.get("backup")
            if not isinstance(backup_name, str) or not re.fullmatch(r"[0-9a-f]{32}\.bak", backup_name):
                raise KnowledgeError("回滚备份记录无效，已停止。")
            backup = self.state_dir / backup_name
            if backup.is_symlink() or not backup.is_file():
                raise KnowledgeError("回滚备份不存在，已停止。")
            before = backup.read_bytes()
            if sha256_bytes(before) != payload.get("before_sha256"):
                raise KnowledgeError("回滚备份校验失败，已停止。")
            self._atomic_write(target, before, target.stat().st_mode)
        else:
            raise KnowledgeError("未知变更类型，已停止回滚。")
        payload["status"] = "rolled_back"
        self._atomic_write(receipt, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), 0o600)
        return {"change_id": change_id, "status": "rolled_back", "path": payload["path"]}


async def require_confirmation(ctx: Any, message: str) -> None:
    prompt = f"{message}\n{WEIXIN_APPROVAL_GUIDANCE}"
    try:
        Confirmation = getattr(ctx, "_bwa_confirmation_schema", None)
        if Confirmation is None:
            try:
                from pydantic import BaseModel, Field

                class Confirmation(BaseModel):
                    # Hermes 当前的 MCP elicitation 客户端用 action 表示批准，
                    # 并在接受时返回空表单。默认 True 让空表单与批准动作兼容；
                    # 显式 false 仍按拒绝处理。
                    confirm: bool = Field(default=True, description="确认执行这一次操作")
            except ImportError:
                from dataclasses import dataclass

                @dataclass
                class Confirmation:
                    # 无 pydantic 时的等价降级：仍保留默认 True 的 confirm 字段。
                    confirm: bool = True

        result = await ctx.elicit(message=prompt, schema=Confirmation)
    except Exception as exc:
        raise KnowledgeError(
            "确认通道没有返回，本次操作未执行。请重新发起操作，并在原请求所在的同一界面按新提示确认。"
        ) from exc
    action = getattr(result, "action", None)
    if action == "cancel":
        raise KnowledgeError(
            "等待确认已超时，本次操作未执行。请重新发起操作，并在原请求所在的同一界面于 2 分钟内按新提示确认。"
        )
    if action != "accept":
        raise KnowledgeError("你已拒绝本次操作；没有读取或写入任何内容。")
    data = getattr(result, "data", None)
    explicit_confirm = getattr(data, "confirm", None)
    if explicit_confirm is False:
        raise KnowledgeError("你已拒绝本次操作；没有读取或写入任何内容。")


def build_server(store: KnowledgeStore) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise KnowledgeError("当前 Python 环境缺少 MCP 运行库，请使用 Hermes 自带的 Python 环境。") from exc

    server = FastMCP("微信助手受限知识库")

    @server.tool()
    async def list_knowledge_notes(ctx: McpContext) -> list[str]:
        """经逐次确认列出获准知识库中的笔记名，并把结果交给当前模型处理。"""
        await require_confirmation(
            ctx,
            "将列出已批准知识库中的笔记名称，并把结果发送给当前模型服务，仅用于这次问题。是否确认？",
        )
        return store.list_notes()

    @server.tool()
    async def read_knowledge_note(path: str, ctx: McpContext) -> dict[str, Any]:
        """经逐次确认读取获准知识库中的一篇笔记，并把正文交给当前模型处理。"""
        await require_confirmation(
            ctx,
            "将读取已批准知识库中的一篇笔记，并把正文发送给当前模型服务，仅用于这次问题。是否确认？",
        )
        return store.read_note(path)

    @server.tool()
    async def search_knowledge_notes(query: str, ctx: McpContext) -> list[dict[str, Any]]:
        """经逐次确认搜索获准知识库，并把匹配片段交给当前模型处理。"""
        await require_confirmation(
            ctx,
            "将在已批准知识库内搜索，并把命中的笔记名称和文本片段发送给当前模型服务，"
            "仅用于这次问题。是否确认？",
        )
        return store.search_notes(query)

    if store.writable:

        @server.tool()
        async def create_knowledge_note(path: str, content: str, ctx: McpContext) -> dict[str, str]:
            """经用户逐次确认后，在获准知识库中新建一篇可回滚笔记。"""
            await require_confirmation(ctx, "将在已批准知识库中新建一篇笔记，不会覆盖同名文件。是否确认？")
            return store.create_note(path, content)

        @server.tool()
        async def update_knowledge_note(path: str, content: str, expected_sha256: str, ctx: McpContext) -> dict[str, str]:
            """经用户逐次确认后更新笔记；版本变化时拒绝覆盖，并返回回滚编号。"""
            await require_confirmation(ctx, "将更新已批准知识库中的一篇笔记，并保留可校验备份。是否确认？")
            return store.update_note(path, content, expected_sha256)

        @server.tool()
        async def rollback_knowledge_change(change_id: str, ctx: McpContext) -> dict[str, str]:
            """经用户逐次确认后回滚本连接创建的变更；文件有后续修改时拒绝。"""
            await require_confirmation(ctx, "将回滚本连接此前记录的一项知识库变更。是否确认？")
            return store.rollback(change_id)

    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行目录受限的知识库 MCP")
    parser.add_argument("--root", required=True, type=Path, help="用户批准的知识库绝对路径")
    parser.add_argument("--state-dir", type=Path, help="知识库外的私有回滚状态目录")
    parser.add_argument("--initialize-only", action="store_true", help="只创建一个全新空知识库后退出")
    parser.add_argument("--write", action="store_true", help="启用逐次确认后的创建、更新和回滚")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.initialize_only:
            if args.state_dir is not None or args.write:
                raise KnowledgeError("初始化新知识库时不能同时启动 MCP 或开启写入。")
            initialize_empty_root(args.root)
            print("CREATED")
            return 0
        if args.state_dir is None:
            raise KnowledgeError("启动知识库连接时必须提供知识库外的私有状态目录。")
        if not args.root.is_absolute() or not args.state_dir.is_absolute():
            raise KnowledgeError("知识库和状态目录都必须是绝对路径。")
        store = KnowledgeStore.open(args.root, args.state_dir, args.write)
        build_server(store).run(transport="stdio")
    except KnowledgeError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

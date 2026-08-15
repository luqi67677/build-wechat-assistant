#!/usr/bin/env python3
"""Expose one Feishu document and one optional create destination through MCP."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

sys.dont_write_bytecode = True

from scoped_knowledge_mcp import KnowledgeError, McpContext, require_confirmation

MAX_CONTENT_CHARS = 120_000
MAX_CREATE_CHARS = 60_000
MAX_TITLE_CHARS = 120
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NODE_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
NODE_IDENTITY_SCRIPT = (
    "const p={release:process.release&&process.release.name,"
    "version:process.versions&&process.versions.node,"
    "execPath:process.execPath,v8:process.versions&&process.versions.v8};"
    "process.stdout.write(JSON.stringify(p));"
)
OFFICIAL_DOCUMENT_HOSTS = ("feishu.cn", "larksuite.com")
OFFICIAL_DOCUMENT_PATH_PREFIXES = ("/wiki/", "/docx/", "/docs/")
MAX_SCOPE_BYTES = 16 * 1024
SCOPE_REQUIRED_KEYS = frozenset({"profile", "document", "expected_document_id"})
SCOPE_OPTIONAL_KEYS = frozenset({"identity", "create_parent_kind", "create_parent_token"})
RESOLVE_REQUIRED_KEYS = frozenset({"profile", "document"})
RESOLVE_OPTIONAL_KEYS = frozenset({"identity"})
SAFE_PERMISSION_SCOPES = frozenset({
    "docx:document:create",
    "docx:document:readonly",
    "wiki:node:create",
    "wiki:node:read",
    "wiki:space:read",
})


class FeishuError(KnowledgeError):
    pass


def validate_node_runtime(node: Path) -> tuple[Path, str]:
    """Prove that an absolute executable is Node.js, not merely any binary."""
    if not node.is_absolute():
        raise FeishuError("Node.js 运行时必须使用绝对路径。")
    try:
        resolved = node.resolve(strict=True)
    except OSError as exc:
        raise FeishuError("没有找到可执行的 Node.js 运行时。") from exc
    if node.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise FeishuError("没有找到可执行的 Node.js 运行时。")
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
        if name in os.environ
    }
    try:
        result = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FeishuError("Node.js 运行时无法启动。") from exc
    version = result.stdout.strip()
    if result.returncode != 0 or not NODE_VERSION_RE.fullmatch(version):
        raise FeishuError("指定程序不是可核验的 Node.js 运行时。")
    try:
        probe = subprocess.run(
            [str(resolved), "-e", NODE_IDENTITY_SCRIPT],
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
            check=False,
        )
        identity = json.loads(probe.stdout)
        identity_path = Path(identity["execPath"]).resolve(strict=True)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FeishuError("指定程序不能完成 Node.js 身份探针。") from exc
    if (
        probe.returncode != 0
        or set(identity) != {"release", "version", "execPath", "v8"}
        or identity["release"] != "node"
        or identity["version"] != version.removeprefix("v")
        or not isinstance(identity["v8"], str)
        or not identity["v8"]
        or identity_path != resolved
    ):
        raise FeishuError("指定程序不能完成 Node.js 身份探针。")
    return resolved, version


def _validate_scope_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FeishuError("飞书范围文件格式无效。")
    keys = set(payload)
    if not SCOPE_REQUIRED_KEYS.issubset(keys) or keys - SCOPE_REQUIRED_KEYS - SCOPE_OPTIONAL_KEYS:
        raise FeishuError("飞书范围文件字段不完整或包含未知字段。")
    profile = payload["profile"]
    document = payload["document"]
    document_id = payload["expected_document_id"]
    identity = payload.get("identity", "bot")
    parent_kind = payload.get("create_parent_kind")
    parent_token = payload.get("create_parent_token")
    if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
        raise FeishuError("飞书专用 Profile 名称无效。")
    if not isinstance(document, str):
        raise FeishuError("请提供飞书官方文档链接。")
    normalized_document = FeishuStore._normalize_document(document)
    if not isinstance(document_id, str) or not TOKEN_RE.fullmatch(document_id):
        raise FeishuError("飞书文档 ID 无效。")
    if identity not in {"bot", "user"}:
        raise FeishuError("飞书身份只能是 bot 或 user。")
    if (parent_kind is None) != (parent_token is None):
        raise FeishuError("创建位置必须同时提供类型和固定 token。")
    if parent_kind not in {None, "wiki-node", "wiki-space", "folder-token"}:
        raise FeishuError("不支持这个飞书创建位置类型。")
    if parent_token is not None and (not isinstance(parent_token, str) or not TOKEN_RE.fullmatch(parent_token)):
        raise FeishuError("飞书创建位置 token 无效。")
    return {
        "profile": profile,
        "document": normalized_document,
        "expected_document_id": document_id,
        "identity": identity,
        "create_parent_kind": parent_kind,
        "create_parent_token": parent_token,
    }


def _private_scope_path(path: Path, *, must_exist: bool) -> Path:
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise FeishuError("飞书范围文件必须使用私有目录中的绝对 JSON 路径。")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise FeishuError("飞书范围文件的父目录无效。")
    parent_details = parent.stat()
    if hasattr(os, "getuid") and parent_details.st_uid != os.getuid():
        raise FeishuError("飞书范围文件的父目录不属于当前运行账号。")
    if os.name == "posix" and stat.S_IMODE(parent_details.st_mode) & 0o077:
        raise FeishuError("飞书范围文件的父目录仍允许其他账号访问。")
    if must_exist:
        if path.is_symlink() or not path.is_file():
            raise FeishuError("没有找到飞书范围文件。")
        details = path.stat()
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise FeishuError("飞书范围文件不属于当前运行账号。")
        if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
            raise FeishuError("飞书范围文件仍允许其他账号访问。")
    elif path.exists() or path.is_symlink():
        raise FeishuError("飞书范围文件已存在；为避免静默改权，请使用新的文件名。")
    return path


def save_scope_file(path: Path, payload: Any) -> Path:
    scope = _validate_scope_payload(payload)
    target = _private_scope_path(path, must_exist=False)
    data = (json.dumps(scope, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_SCOPE_BYTES:
        raise FeishuError("飞书范围文件过大。")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def load_scope_file(path: Path) -> dict[str, Any]:
    target = _private_scope_path(path, must_exist=True)
    if target.stat().st_size > MAX_SCOPE_BYTES:
        raise FeishuError("飞书范围文件过大。")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuError("飞书范围文件无法安全读取。") from exc
    return _validate_scope_payload(payload)


def resolve_and_save_scope(
    path: Path,
    payload: Any,
    lark_cli: Path,
    credential_home: Path,
    hermes_home: Path | None = None,
    node: Path | None = None,
) -> Path:
    """Resolve a Feishu document locally and persist its ID without printing content."""
    if not isinstance(payload, dict):
        raise FeishuError("飞书范围解析输入格式无效。")
    keys = set(payload)
    if not RESOLVE_REQUIRED_KEYS.issubset(keys) or keys - RESOLVE_REQUIRED_KEYS - RESOLVE_OPTIONAL_KEYS:
        raise FeishuError("飞书范围解析字段不完整或包含未知字段。")
    profile = payload["profile"]
    document = payload["document"]
    identity = payload.get("identity", "user")
    if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
        raise FeishuError("飞书专用 Profile 名称无效。")
    if not isinstance(document, str):
        raise FeishuError("请提供飞书官方文档链接。")
    normalized = FeishuStore._normalize_document(document)
    if identity not in {"bot", "user"}:
        raise FeishuError("飞书身份只能是 bot 或 user。")
    store = FeishuStore.open(
        lark_cli, credential_home, profile, normalized, "pending1", identity,
        hermes_home=hermes_home, node=node,
    )
    result = store._run([
        "docs", "+fetch", "--api-version", "v2", "--doc", normalized,
        "--as", identity, "--format", "json",
    ])
    document_id = _find_string(result, {"document_id", "doc_id"})
    if document_id is None or not TOKEN_RE.fullmatch(document_id):
        raise FeishuError("飞书没有返回可核验的文档 ID；白名单尚未创建。")
    return save_scope_file(path, {
        "profile": profile,
        "document": normalized,
        "expected_document_id": document_id,
        "identity": identity,
    })


def _find_string(payload: Any, names: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in names and isinstance(value, (str, int)):
                return str(value)
        for value in payload.values():
            found = _find_string(value, names)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_string(value, names)
            if found:
                return found
    return None


def _safe_missing_scopes(stdout: str, stderr: str) -> list[str]:
    output = f"{stdout}\n{stderr}"
    return sorted(scope for scope in SAFE_PERMISSION_SCOPES if scope in output)


@dataclass(frozen=True)
class FeishuStore:
    lark_cli: Path
    credential_home: Path
    profile: str
    allowed_document: str
    expected_document_id: str
    identity: str = "bot"
    create_parent_kind: str | None = None
    create_parent_token: str | None = None
    hermes_home: Path | None = None
    node: Path | None = None

    @classmethod
    def open(cls, lark_cli: Path, credential_home: Path, profile: str, allowed_document: str,
             expected_document_id: str, identity: str = "bot",
             create_parent_kind: str | None = None,
             create_parent_token: str | None = None,
             hermes_home: Path | None = None,
             node: Path | None = None) -> FeishuStore:
        try:
            executable = lark_cli.resolve(strict=True)
        except OSError as exc:
            raise FeishuError("没有找到可执行的飞书连接工具。") from exc
        home = credential_home.resolve(strict=True)
        if lark_cli.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise FeishuError("没有找到可执行的飞书连接工具。")
        if not home.is_dir() or credential_home.is_symlink():
            raise FeishuError("飞书凭据目录无效。")
        resolved_node: Path | None = None
        if node is not None:
            resolved_node, _node_version = validate_node_runtime(node)
        if not PROFILE_RE.fullmatch(profile):
            raise FeishuError("飞书专用 Profile 名称无效。")
        normalized = cls._normalize_document(allowed_document)
        if not TOKEN_RE.fullmatch(expected_document_id):
            raise FeishuError("飞书文档 ID 无效。")
        if identity not in {"bot", "user"}:
            raise FeishuError("飞书身份只能是 bot 或 user。")
        if (create_parent_kind is None) != (create_parent_token is None):
            raise FeishuError("创建位置必须同时提供类型和固定 token。")
        if create_parent_kind not in {None, "wiki-node", "wiki-space", "folder-token"}:
            raise FeishuError("不支持这个飞书创建位置类型。")
        if create_parent_token is not None and not TOKEN_RE.fullmatch(create_parent_token):
            raise FeishuError("飞书创建位置 token 无效。")
        resolved_hermes_home: Path | None = None
        if hermes_home is not None:
            resolved_hermes_home = hermes_home.resolve(strict=True)
            if hermes_home.is_symlink() or not resolved_hermes_home.is_dir():
                raise FeishuError("Hermes 工作区无效。")
        return cls(executable, home, profile, normalized, expected_document_id, identity,
                   create_parent_kind, create_parent_token, resolved_hermes_home, resolved_node)

    @staticmethod
    def _normalize_document(value: str) -> str:
        try:
            parsed = urlsplit(value.strip())
            port = parsed.port
        except ValueError as exc:
            raise FeishuError("请提供飞书官方文档链接。") from exc
        host = (parsed.hostname or "").lower().rstrip(".")
        official_host = any(host == suffix or host.endswith(f".{suffix}") for suffix in OFFICIAL_DOCUMENT_HOSTS)
        official_path = any(parsed.path.startswith(prefix) for prefix in OFFICIAL_DOCUMENT_PATH_PREFIXES)
        if (parsed.scheme != "https" or not official_host or not official_path
                or parsed.username is not None or parsed.password is not None or port is not None):
            raise FeishuError("请提供飞书官方文档链接。")
        return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))

    def _env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
                     "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            if name in os.environ:
                env[name] = os.environ[name]
        env.update({
            "HOME": str(self.credential_home),
            "XDG_CONFIG_HOME": str(self.credential_home / ".config"),
            "XDG_DATA_HOME": str(self.credential_home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.credential_home / ".cache"),
            "LARK_CLI_NO_PROXY": "1",
        })
        if self.hermes_home is not None:
            env["HERMES_HOME"] = str(self.hermes_home)
        return env

    def _run(self, args: list[str], *, stdin: str | None = None) -> dict[str, Any]:
        command = [str(self.lark_cli), "--profile", self.profile, *args]
        if self.node is not None:
            command.insert(0, str(self.node))
        try:
            result = subprocess.run(
                command,
                input=stdin, capture_output=True, text=True,
                env=self._env(), timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FeishuError("飞书连接工具未能正常运行，请稍后重试。") from exc
        if result.returncode != 0:
            missing = _safe_missing_scopes(result.stdout, result.stderr)
            if missing:
                scopes = "、".join(missing)
                raise FeishuError(
                    f"飞书缺少本次动作的最小权限：{scopes}。没有读取或创建文档；"
                    "请让 Agent 只在飞书官方页面申请这些权限，账号本人确认后重试。"
                )
            raise FeishuError("飞书操作失败：请检查专用应用是否已发布，并只补充当前操作缺少的权限。")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FeishuError("飞书返回了无法识别的结果，请更新连接工具后重试。") from exc
        if not isinstance(payload, dict):
            raise FeishuError("飞书返回了无法识别的结果。")
        return payload

    def read_document(self, source_url: str | None = None) -> dict[str, Any]:
        if source_url is not None and self._normalize_document(source_url) != self.allowed_document:
            raise FeishuError("该文档不在当前知识库白名单中，已在联网前拒绝。")
        payload = self._run([
            "docs", "+fetch", "--api-version", "v2", "--doc", self.allowed_document,
            "--as", self.identity, "--format", "json",
        ])
        document_id = _find_string(payload, {"document_id", "doc_id"})
        if document_id != self.expected_document_id:
            raise FeishuError("飞书返回的不是已授权文档，已拒绝读取。")
        content = _find_string(payload, {"content", "markdown"})
        if content is None:
            raise FeishuError("飞书结果缺少文档正文。")
        if len(content) > MAX_CONTENT_CHARS:
            raise FeishuError("飞书文档过长，请拆分后再读取。")
        return {
            "source": self.allowed_document,
            "document_id": document_id,
            "revision_id": _find_string(payload, {"revision_id", "revision"}),
            "title": _find_string(payload, {"title", "name"}) or "飞书知识库",
            "content": content,
            "access": "single_document_allowlist",
        }

    def create_document(self, title: str, markdown: str) -> dict[str, str]:
        if self.create_parent_kind is None or self.create_parent_token is None:
            raise FeishuError("当前飞书连接是只读模式；尚未批准固定创建位置。")
        title = title.strip()
        if not title or len(title) > MAX_TITLE_CHARS or any(character in title for character in "\r\n"):
            raise FeishuError("文档标题不能为空、不能换行，且不能超过 120 个字符。")
        if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_CREATE_CHARS:
            raise FeishuError("文档内容不能为空且不能超过 60000 个字符。")
        if self.create_parent_kind == "wiki-space" and self.create_parent_token != "my_library":
            raise FeishuError("飞书知识空间必须先锁定到具体父节点；只有个人文档库可直接作为创建位置。")
        parent_flag = (
            "--parent-position" if self.create_parent_kind == "wiki-space" else "--parent-token"
        )
        document = f"# {title}\n\n{markdown.lstrip()}"
        args = [
            "docs", "+create", "--api-version", "v2", "--as", self.identity,
            "--doc-format", "markdown", "--content", "-",
            parent_flag, self.create_parent_token,
        ]
        payload = self._run(args, stdin=document)
        document_id = _find_string(payload, {"document_id", "doc_id", "token"})
        url = _find_string(payload, {"url", "document_url"})
        if not document_id:
            raise FeishuError("飞书已响应，但没有返回可核验的文档 ID；请在飞书中检查后再重试。")
        return {"status": "created", "document_id": document_id, "url": url or ""}


def build_server(store: FeishuStore) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise FeishuError("当前 Python 环境缺少 MCP 运行库，请使用 Hermes 自带的 Python 环境。") from exc

    server = FastMCP("微信助手受限飞书知识库")

    @server.tool()
    async def read_allowed_feishu_document(ctx: McpContext, source_url: str | None = None) -> dict[str, Any]:
        """经逐次确认读取唯一获准的飞书文档，并把正文交给当前模型处理。"""
        await require_confirmation(
            ctx,
            "将读取已批准的飞书文档，并把正文发送给当前模型服务，仅用于回答这次问题。"
            "飞书只读授权本身不包含这项数据传输同意。是否确认？",
        )
        return store.read_document(source_url)

    if store.create_parent_kind is not None:
        @server.tool()
        async def create_feishu_knowledge_document(title: str, markdown: str, ctx: McpContext) -> dict[str, str]:
            """经逐次确认，在安装时锁定的飞书目录中新建文档；不能改到其他目录。"""
            await require_confirmation(
                ctx,
                "将在已批准的飞书知识库位置新建一篇文档。这是外部写入，创建后不会自动删除。是否确认？",
            )
            return store.create_document(title, markdown)

    return server


def probe_store(store: FeishuStore) -> dict[str, object]:
    """Verify live allowed read and local deny without returning document data."""
    blocked = False
    try:
        store.read_document("https://example.feishu.cn/wiki/bwaUnauthorized123")
    except FeishuError as exc:
        blocked = "联网前拒绝" in str(exc)
    if not blocked:
        raise FeishuError("未授权飞书文档没有在联网前被拒绝；已停止。")
    result = store.read_document()
    if not result.get("content") or result.get("document_id") != store.expected_document_id:
        raise FeishuError("指定飞书文档没有通过真实读取核验。")
    return {
        "result": "LIVE_READ_OK",
        "allowed_document_read": True,
        "unauthorized_document_blocked_before_network": True,
        "content_printed": False,
        "document_id_printed": False,
        "secrets_printed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行资源白名单式飞书知识库 MCP")
    parser.add_argument("--lark-cli", type=Path)
    parser.add_argument("--node", type=Path, help="运行官方 lark-cli 的 Node.js 绝对路径")
    parser.add_argument("--credential-home", type=Path)
    parser.add_argument("--hermes-home", type=Path, help="飞书应用绑定到 Agent 工作区时的精确 Hermes 根")
    parser.add_argument("--scope-file", type=Path, help="包含资源白名单的私有 JSON 文件")
    parser.add_argument("--write-scope", type=Path, help="从标准输入创建一次性私有范围文件后退出")
    parser.add_argument("--resolve-scope", type=Path, help="联网解析固定文档后直接创建私有范围文件")
    parser.add_argument("--probe", action="store_true", help="只输出脱敏的真实读取与越界拒绝结果")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.resolve_scope is not None:
            if args.write_scope is not None or args.scope_file is not None or args.probe:
                raise FeishuError("解析飞书范围时不能同时读取或创建另一份范围文件。")
            if args.lark_cli is None or args.node is None or args.credential_home is None:
                raise FeishuError("解析飞书范围需要 Node.js、工具和隔离凭据目录。")
            try:
                payload = json.load(sys.stdin)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FeishuError("标准输入不是有效的飞书范围 JSON。") from exc
            resolve_and_save_scope(
                args.resolve_scope, payload, args.lark_cli, args.credential_home,
                args.hermes_home, args.node,
            )
            print(json.dumps({
                "result": "SAVED",
                "content_printed": False,
                "document_id_printed": False,
                "secrets_printed": False,
            }, sort_keys=True))
            return 0
        if args.write_scope is not None:
            if args.probe or any(value is not None for value in (args.lark_cli, args.node, args.credential_home, args.hermes_home, args.scope_file, args.resolve_scope)):
                raise FeishuError("创建范围文件时不能同时启动 MCP。")
            try:
                payload = json.load(sys.stdin)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FeishuError("标准输入不是有效的飞书范围 JSON。") from exc
            save_scope_file(args.write_scope, payload)
            print(json.dumps({"result": "SAVED", "secrets_printed": False}, sort_keys=True))
            return 0
        if args.lark_cli is None or args.node is None or args.credential_home is None or args.scope_file is None:
            raise FeishuError("启动飞书 MCP 需要 Node.js、工具、凭据目录和私有范围文件。")
        if not args.lark_cli.is_absolute() or not args.node.is_absolute() or not args.credential_home.is_absolute():
            raise FeishuError("Node.js、飞书工具和凭据目录必须使用绝对路径。")
        scope = load_scope_file(args.scope_file)
        store = FeishuStore.open(
            args.lark_cli, args.credential_home, scope["profile"], scope["document"],
            scope["expected_document_id"], scope["identity"], scope["create_parent_kind"],
            scope["create_parent_token"], args.hermes_home, args.node,
        )
        if args.probe:
            print(json.dumps(probe_store(store), sort_keys=True))
            return 0
        build_server(store).run(transport="stdio")
    except FeishuError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

from scoped_coding_mcp import CodingStore
from scoped_coding_mcp import build_server as build_coding_server
from scoped_feishu_mcp import FeishuError, FeishuStore, probe_store, save_scope_file, validate_node_runtime
from scoped_feishu_mcp import build_server as build_feishu_server
from scoped_knowledge_mcp import KnowledgeStore, McpContext
from scoped_knowledge_mcp import build_server as build_knowledge_server

EXPECTED_TOOLS = {
    "knowledge": {
        "list_knowledge_notes", "read_knowledge_note", "search_knowledge_notes",
        "create_knowledge_note", "update_knowledge_note", "rollback_knowledge_change",
    },
    "feishu": {"read_allowed_feishu_document", "create_feishu_knowledge_document"},
    "coding": {"inspect_code_project", "prepare_code_change", "inspect_code_change", "apply_code_change", "rollback_code_change"},
}


def tool_names(server) -> set[str]:
    return {tool.name for tool in server._tool_manager.list_tools()}


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_cli(command: list[str], env: dict[str, str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, input=input_text, capture_output=True, text=True, check=False, timeout=30, env=env,
    )


def verify_hermes_stdio(
    hermes: Path,
    node: Path,
    base: Path,
    fixtures: dict[str, Path],
    failures: list[str],
) -> bool:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HERMES_HOME": str(base / "hermes-home"),
    }
    created = run_cli(
        [str(hermes), "profile", "create", "mcptest", "--no-alias", "--no-skills"], env,
    )
    if created.returncode != 0:
        failures.append("Hermes 无法创建隔离 MCP 验收 Profile")
        return False

    script_dir = Path(__file__).resolve().parent
    specifications = {
        "scoped_knowledge": (
            EXPECTED_TOOLS["knowledge"],
            [str(script_dir / "scoped_knowledge_mcp.py"), "--root", str(fixtures["vault"]),
             "--state-dir", str(base / "knowledge-cli-state"), "--write"],
        ),
        "scoped_feishu": (
            EXPECTED_TOOLS["feishu"],
            [str(script_dir / "scoped_feishu_mcp.py"), "--lark-cli", str(fixtures["lark"]),
             "--node", str(node),
             "--credential-home", str(fixtures["lark_home"]),
             "--scope-file", str(fixtures["feishu_scope"])],
        ),
        "scoped_coding": (
            EXPECTED_TOOLS["coding"],
            [str(script_dir / "scoped_coding_mcp.py"), "--project", str(fixtures["project"]),
             "--state-dir", str(base / "coding-cli-state"), "--codex", str(fixtures["codex"]),
             "--timeout", "60"],
        ),
    }
    for name, (expected, arguments) in specifications.items():
        added = run_cli(
            [str(hermes), "-p", "mcptest", "mcp", "add", name,
             "--command", sys.executable, "--connect-timeout", "10", "--args", *arguments],
            env, input_text="y\n",
        )
        added_output = added.stdout + added.stderr
        if added.returncode != 0 or f"({len(expected)}/{len(expected)} tools enabled)" not in added_output:
            failures.append(
                f"Hermes 未能保存 {name} 的完整工具清单"
                f"（exit={added.returncode}，发现工具={('Tools discovered:' in added_output)}，"
                f"保存回执={('tools enabled)' in added_output)}）"
            )
            continue
        tested = run_cli([str(hermes), "-p", "mcptest", "mcp", "test", name], env)
        tested_output = tested.stdout + tested.stderr
        if tested.returncode != 0 or f"Tools discovered: {len(expected)}" not in tested_output:
            failures.append(f"Hermes stdio 重连 {name} 失败")
            continue
        missing = sorted(tool for tool in expected if tool not in tested_output)
        if missing:
            failures.append(f"Hermes stdio 重连 {name} 缺少工具：{', '.join(missing)}")
    return not failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证可选能力 MCP 运行时")
    parser.add_argument("--hermes", type=Path, help="用于 stdio 重连验收的 Hermes CLI 绝对路径")
    parser.add_argument("--node", required=True, type=Path, help="已核验的 Node.js 真实绝对路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    hermes_stdio: bool | None = None
    node_version: str | None = None
    try:
        node, node_version = validate_node_runtime(args.node)
    except FeishuError as exc:
        failures.append(str(exc))
        node = None
    if not hasattr(McpContext, "elicit"):
        failures.append("当前 MCP SDK 缺少 elicitation，不能提供逐次写入确认")
    with tempfile.TemporaryDirectory() as raw_temp:
        base = Path(raw_temp)

        vault = base / "vault"
        vault.mkdir()
        knowledge = KnowledgeStore.open(vault, base / "knowledge-state", writable=True)
        if tool_names(build_knowledge_server(knowledge)) != EXPECTED_TOOLS["knowledge"]:
            failures.append("知识库 MCP 工具清单不匹配")

        lark_home = base / "lark-home"
        lark_home.mkdir()
        lark = base / "lark-cli"
        executable(
            lark,
            "#!/usr/bin/env node\n"
            "const args = process.argv.slice(2);\n"
            "if (args.includes('+fetch')) {\n"
            "  process.stdout.write(JSON.stringify({data:{document_id:'docABC123',revision_id:1,title:'验收',content:'飞书运行时验收'}}));\n"
            "} else if (args.includes('+create')) {\n"
            "  let body = ''; process.stdin.setEncoding('utf8');\n"
            "  process.stdin.on('data', chunk => body += chunk);\n"
            "  process.stdin.on('end', () => {\n"
            "    if (!body) process.exit(3);\n"
            "    process.stdout.write(JSON.stringify({data:{document_id:'newDOC456',url:'https://example.feishu.cn/docx/newDOC456'}}));\n"
            "  });\n"
            "} else { process.exit(2); }\n",
        )
        scope_dir = base / "feishu-scope"
        scope_dir.mkdir(mode=0o700)
        feishu_scope = save_scope_file(scope_dir / "scope.json", {
            "profile": "acceptance-readonly",
            "document": "https://example.feishu.cn/wiki/wikiABC123",
            "expected_document_id": "docABC123",
            "identity": "bot",
            "create_parent_kind": "wiki-node",
            "create_parent_token": "wikiABC123",
        })
        if node is not None:
            feishu = FeishuStore.open(
                lark, lark_home, "acceptance-readonly",
                "https://example.feishu.cn/wiki/wikiABC123", "docABC123",
                "bot", "wiki-node", "wikiABC123", node=node,
            )
            if tool_names(build_feishu_server(feishu)) != EXPECTED_TOOLS["feishu"]:
                failures.append("飞书 MCP 工具清单不匹配")
            try:
                probe_store(feishu)
                if feishu.create_document("验收", "固定目录写入验收").get("status") != "created":
                    failures.append("飞书显式 Node.js 运行时未完成固定目录创建")
            except FeishuError as exc:
                failures.append(f"飞书显式 Node.js 运行时失败：{exc}")

        project = base / "project"
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        (project / "sample.txt").write_text("sample\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "sample.txt"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "initial"], check=True)
        codex = base / "codex"
        executable(
            codex,
            "#!/bin/sh\n"
            "if [ \"$1\" = --help ]; then printf '%s\\n' '--sandbox --ask-for-approval --config workspace-write never'; exit 0; fi\n"
            "if [ \"$1\" = exec ] && [ \"$2\" = --help ]; then printf '%s\\n' '--ephemeral --ignore-user-config --cd'; exit 0; fi\n"
            "exit 0\n",
        )
        coding = CodingStore.open(project, base / "coding-state", codex, 60)
        if tool_names(build_coding_server(coding)) != EXPECTED_TOOLS["coding"]:
            failures.append("编程 MCP 工具清单不匹配")

        if args.hermes is not None and node is not None:
            if not args.hermes.is_absolute() or not args.hermes.is_file():
                failures.append("用于 MCP 验收的 Hermes CLI 必须是存在的绝对路径")
                hermes_stdio = False
            else:
                hermes_stdio = verify_hermes_stdio(
                    args.hermes,
                    node,
                    base,
                    {"vault": vault, "lark": lark,
                     "lark_home": lark_home,
                     "feishu_scope": feishu_scope, "project": project, "codex": codex},
                    failures,
                )

    payload = {
        "result": "FAIL" if failures else "PASS",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "mcp_elicitation": hasattr(McpContext, "elicit"),
        "hermes_stdio": hermes_stdio,
        "node": node_version,
        "servers": sorted(EXPECTED_TOOLS),
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

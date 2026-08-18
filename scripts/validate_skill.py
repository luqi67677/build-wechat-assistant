#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# Windows 控制台默认 GBK/CP1252，直接打印中文结果会 UnicodeEncodeError；
# 统一重配为 UTF-8（带替换兜底），让验证器在任何平台都能输出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

from flow_policy import (
    SKILL_TITLE,
    VERSION,
    load_contract,
    validate_contract,
    validate_documents,
)

REQUIRED_FILES = frozenset(
    {
        "SKILL.md",
        "agents/openai.yaml",
        "assets/SOUL.zh-CN.md",
        "assets/cloud-flow-fixtures.json",
        "references/china-models.md",
        "references/acceptance.md",
        "references/chinese-ux.md",
        "references/cloud-deployment.md",
        "references/flow-contract.json",
        "references/install-skill.md",
        "references/model-routing.md",
        "references/operation-faq.md",
        "references/setup-guide.md",
        "references/security-boundary.md",
        "references/tools.md",
        "references/weixin-setup-zh.md",
        "scripts/flow_policy.py",
        "scripts/apply_chat_safety_baseline.py",
        "scripts/check_cloud_preflight.py",
        "scripts/check_hermes_cli_contract.py",
        "scripts/check_optional_mcp_runtime.py",
        "scripts/isolation_guard.py",
        "scripts/launch_feishu_oauth.py",
        "scripts/launch_trusted_handoff.py",
        "scripts/setup_weixin_direct.py",
        "scripts/resource_ledger_guard.py",
        "scripts/scoped_coding_mcp.py",
        "scripts/scoped_feishu_mcp.py",
        "scripts/scoped_knowledge_mcp.py",
        "scripts/check_pre_qr_safety.py",
        "scripts/check_profile_safety.py",
        "scripts/systemd_env_policy.py",
        "scripts/systemd_env_guard.py",
        "scripts/set_profile_env_key.py",
        "scripts/test_install_acceptance.py",
        "scripts/test_apply_chat_safety_baseline.py",
        "scripts/test_cloud_flows.py",
        "scripts/test_cloud_preflight.py",
        "scripts/test_flow_contracts.py",
        "scripts/test_hermes_cli_contract.py",
        "scripts/test_isolation_guard.py",
        "scripts/test_launch_feishu_oauth.py",
        "scripts/test_launch_trusted_handoff.py",
        "scripts/test_setup_weixin_direct.py",
        "scripts/test_resource_ledger_guard.py",
        "scripts/test_scoped_coding_mcp.py",
        "scripts/test_scoped_feishu_mcp.py",
        "scripts/test_scoped_knowledge_mcp.py",
        "scripts/test_optional_runtime.py",
        "scripts/test_optional_abilities_journey.py",
        "scripts/test_profile_safety.py",
        "scripts/test_pre_qr_safety.py",
        "scripts/test_systemd_env_guard.py",
        "scripts/test_set_profile_env_key.py",
        "scripts/test_release.py",
        "scripts/test_validate_skill.py",
        "scripts/test_source_facts.py",
        "scripts/test_verify_release_package.py",
        "scripts/validate_skill.py",
        "scripts/verify_release_package.py",
    }
)

REPO_LEVEL_FILES = frozenset(
    {
        "README.md",
        "LICENSE",
        "VERSION",
        ".gitignore",
        "CONTRIBUTING.md",
        "SECURITY.md",
    }
)

VERSION_FILE_RE = re.compile(r"V(\d+\.\d+)")


def _is_repo_file(relative: str) -> bool:
    """仓库层文件不属于 Skill 运行文件树，结构校验时忽略。"""
    return (
        relative in REPO_LEVEL_FILES
        or relative.startswith(".github/")
        or relative.startswith(".git/")
        or relative.startswith("tools/")
    )


MARKDOWN_FILES = tuple(sorted(path for path in REQUIRED_FILES if path.endswith(".md")))
FORBIDDEN_PATTERNS = {
    r"(?:\u5c0f\u4e03|\u516d\u4e03|\u7131\u4e03)": "把维护者的私人助手名称写入通用发布包",
    r"10\s*秒无响应": "按计时器替用户决定",
    r"正常使用没问题": "无法证明的账号安全承诺",
    r"(?:几块钱/月|不超过十块钱/月|几十到一百元/年)": "脱离用量的固定费用承诺",
    r"不会交给第三方平台": "错误的数据隐私承诺",
    r"二维码(?:图片)?或\s*URL\s*发给用户": "把短时登录凭据发进聊天",
    r"(?:grep|awk)[^\n]*(?:API_KEY|TOKEN|SECRET|PASSWORD)": "用文本搜索提取秘密值；非秘密策略键只允许输出计数的只读核验",
    r"scp[^\n]*\.env": "复制完整环境秘密",
    r"(?:echo|printf)[^\n]*API_KEY": "在命令中写入 API key",
    r"--api-key\s+\S+": "把 API key 放进命令参数",
    r"\brm\s+(?:-r[fF]?\s+)?(?:~|\$HOME)/\.hermes": "把删除整个 Hermes 数据目录当作普通步骤",
    r"\.task\.json": "可同步的远程命令文件",
    r'"command"\s*:\s*"': "任意命令任务队列",
    r"PasswordAuthentication\s+no": "没有双会话与控制台回退的 SSH 锁定配方",
    r"\b(?:launchctl|systemctl)\s+(?:start|stop|restart|enable|disable)\b": "绕过 Hermes 服务管理命令",
    r"\bhermes\s+cronjob\b": "旧 CLI 命令 hermes cronjob",
    r"gateway\s+setup\s+--text": "不存在的 gateway setup --text",
    r"向对话、普通截图": "把截图当作发送目的地的语病",
    r"`Start automatically on login/boot": "开机自启向导英文引用与真实屏幕文案不符",
    r"几分钟配好": "承诺固定耗时",
    r"搜不了不算搭建完成": "搜索必须通过的矛盾完成门槛",
    r"主模型出问题时[^\n]{0,40}不会突然失联": "fallback 绝对不中断承诺",
    r"先(?:启动|打开)云端[^\n]{0,100}再(?:停止|关闭)本地": "先开云端后停本地的双轮询风险",
    r"(?:短暂|临时)[^\n]{0,20}(?:双轮询|同时轮询)": "允许临时双轮询",
    r'ssh\s+<用户名>@<IP>\s+"hermes\s+gateway': "云端 FAQ 使用错误的用户服务命令",
    r"chat_only_allow_toolsets": "把可选工具误当成默认允许集合",
    r"已安装\s+V\d": "在待安装元数据中虚构已安装状态",
    r"URL可以": "中英文之间缺少空格",
    r"(?-i:API Key)": "API key 术语大小写不一致",
    r"Hermes 官网:": "中文正文使用半角冒号",
    r"tail\s+-\d+\s+~/\.hermes": "Windows 不兼容的固定日志命令",
    r"hermes\s+gateway\s+install\s+--no-start-now\s+--no-start-on-login": "本地错误暂存服务状态机",
    r"hermes\s+-p\s+<Profile>\s+profile\s+show": "profile show 的名称参数位置错误",
}
PRIVATE_IDENTIFIER_RE = re.compile(
    r"(?i)\b(?:xiao" r"qi|yan" r"qi|liu" r"qi|lu" r"qi)\b|"
    r"(?:\u5c0f\u4e03|\u516d\u4e03|\u7131\u4e03)"
)

SOURCE_FACT_RESULT_RE = re.compile(
    r"\(test_source_facts\.WizardSourceFactTests(?:\.[^)]+)?\)"
)


def count_source_fact_results(output: str) -> int:
    return len(SOURCE_FACT_RESULT_RE.findall(output))


REQUIRED_BY_FILE = {
    "SKILL.md": (
        SKILL_TITLE,
        "用户未回复时暂停",
        "搜索是可选能力",
        "三个新会话",
        "从持久记忆移除 ≠",
        "references/security-boundary.md",
        "二维码和登录 URL 属于短时登录凭据",
        "不得调用通用 `gateway setup`",
        "没有测试账号时将外部负向测试列为未验证",
        "连续进行 10 轮",
        "hermes -p <Profile> gateway run",
        "本 Skill 不自动搭桥",
        "模拟通过不能冒充真实购买或真实部署成功",
        "scripts/check_hermes_cli_contract.py",
        "scripts/check_pre_qr_safety.py",
    ),
    "references/model-routing.md": ("✓ 第 3 步已完成", "官方未确认"),
    "references/china-models.md": ("本文件用于第 3 步", "官网未确认"),
    "references/weixin-setup-zh.md": (
        "第 4 步只完成连接配置",
        "gateway 保持未启动",
        "也不通过普通截图分享",
    ),
    "references/cloud-deployment.md": (
        "默认不复制本地 API key",
        "为云端创建独立凭据",
        "同一个 Weixin token 只允许一个轮询实例",
        "command -v systemctl loginctl curl git xz",
        "isolation_guard.py run-service --root <本轮专用Hermes根>",
        "--expected-hermes-root <本轮专用Hermes根>",
        "check_cloud_preflight.py",
        "systemd_env_guard.py",
        "UnsetEnvironment",
    ),
    "references/security-boundary.md": (
        "terminal.cwd` 只决定工具从哪里开始，不是沙箱",
        "hermes -p <Profile> tools list --platform weixin",
        "扫码前静态门禁",
        "扫码后运行时门禁",
        "skills.write_approval",
        "精确只启用 `clarify`",
        "没有任何 MCP 服务器",
    ),
    "references/acceptance.md": (
        "可选能力的独立验收",
        "工具层、模型层、微信层",
        "假 Codex 只能证明执行器编排",
        "不能只看退出码",
    ),
    "references/tools.md": (
        "不执行同步文件中的命令",
        "本 Skill 不创建“同步一个含任意命令的任务文件”",
        "逐任务确认",
        "scripts/scoped_knowledge_mcp.py",
        "scripts/scoped_feishu_mcp.py",
        "scripts/scoped_coding_mcp.py",
        "准备不改原项目",
        "cron pause <job_id>",
    ),
    "assets/SOUL.zh-CN.md": (
        "相关内容可能发送给当前模型或工具提供商",
        "不得承诺“第三方永远看不到”",
    ),
}


def add_failure(failures: list[str], message: str) -> None:
    failures.append(message)


def read_text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def validate_version_file(root: Path, failures: list[str]) -> None:
    """仓库根 VERSION 文件内容必须与 flow_policy.VERSION 一致。"""
    try:
        raw = (root / "VERSION").read_text(encoding="utf-8")
    except OSError:
        add_failure(failures, "VERSION 文件缺失或不可读")
        return
    match = VERSION_FILE_RE.fullmatch(raw.strip())
    if not match:
        add_failure(failures, "VERSION 文件格式必须为 V<主版本.次版本>")
        return
    if match.group(1) != VERSION:
        add_failure(failures, f"VERSION 文件 V{match.group(1)} 与 flow_policy.VERSION {VERSION} 不一致")


def validate_structure(root: Path, failures: list[str]) -> None:
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not _is_repo_file(path.relative_to(root).as_posix())
    }
    missing = sorted(REQUIRED_FILES - actual_files)
    unexpected = sorted(actual_files - REQUIRED_FILES)
    for relative in missing:
        add_failure(failures, f"缺少文件：{relative}")
    for relative in unexpected:
        add_failure(failures, f"Skill 目录含非运行必需文件：{relative}")


def validate_frontmatter(skill: str, failures: list[str]) -> None:
    match = re.match(r"\A---\n(.*?)\n---\n", skill, re.DOTALL)
    if not match:
        add_failure(failures, "SKILL.md frontmatter 无效")
        return
    frontmatter = match.group(1)
    keys = re.findall(r"^([A-Za-z0-9_-]+):", frontmatter, re.MULTILINE)
    if sorted(keys) != ["description", "name"] or len(keys) != 2:
        add_failure(failures, f"frontmatter 必须且只能包含 name、description，实际为：{keys}")
    if not re.search(r"^name:\s*build-wechat-assistant\s*$", frontmatter, re.MULTILINE):
        add_failure(failures, "Skill name 不匹配")
    description = re.search(r"^description:\s*(\S.*)$", frontmatter, re.MULTILINE)
    if not description or len(description.group(1).strip()) < 40:
        add_failure(failures, "description 缺少明确能力与触发场景")


def validate_agent_metadata(text: str, failures: list[str]) -> None:
    values = dict(re.findall(r'^\s{2}([a-z_]+):\s*"([^"]*)"\s*$', text, re.MULTILINE))
    expected_keys = {"display_name", "short_description", "default_prompt"}
    if set(values) != expected_keys:
        add_failure(failures, f"agents/openai.yaml 字段不匹配：{sorted(values)}")
        return
    if values["display_name"] != "微信 AI 助手搭建":
        add_failure(failures, "display_name 不匹配")
    if f"V{VERSION}" not in values["short_description"]:
        add_failure(failures, f"short_description 缺少 V{VERSION}")
    if "已安装" in values["short_description"]:
        add_failure(failures, "short_description 不得在安装前声称已安装")
    if not 25 <= len(values["short_description"]) <= 64:
        add_failure(failures, "short_description 必须为 25–64 个字符")
    if "$build-wechat-assistant" not in values["default_prompt"]:
        add_failure(failures, "default_prompt 必须显式提及 $build-wechat-assistant")


def validate_local_links(root: Path, markdown: str, failures: list[str]) -> None:
    for relative in re.findall(r"\[[^\]]+\]\((?!https?://)([^)#]+)(?:#[^)]+)?\)", markdown):
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            add_failure(failures, f"本地链接越出 Skill 目录：{relative}")
            continue
        if not target.exists():
            add_failure(failures, f"本地链接不存在：{relative}")


def validate_no_private_identifiers(release_text: str, failures: list[str]) -> None:
    if PRIVATE_IDENTIFIER_RE.search(release_text):
        add_failure(failures, "发布文件包含维护者私人助手或账号标识")


def run_flow_tests(root: Path, failures: list[str], hermes: Path | None) -> bool:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if hermes is not None:
        env["BWA_HERMES_EXECUTABLE"] = str(hermes)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(root / "scripts"),
                "-p",
                "test_*.py",
                "-v",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        add_failure(failures, "流程回归执行超时或不可用")
        return False
    combined = f"{result.stdout}\n{result.stderr}"
    skipped_match = re.search(r"skipped=(\d+)", combined)
    skipped_count = int(skipped_match.group(1)) if skipped_match else 0
    source_fact_count = count_source_fact_results(combined)
    source_facts_verified = (
        source_fact_count == 30
        and "源码事实核验未验证" not in combined
        and "无法创建隔离的源码事实测试 Profile" not in combined
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        add_failure(failures, f"流程回归失败：{detail[-1] if detail else '未知错误'}")
    if hermes is not None and not source_facts_verified:
        add_failure(failures, f"指定 Hermes 的源码事实核验数量异常：{source_fact_count}/30")
    if hermes is not None and skipped_count:
        add_failure(failures, f"指定 Hermes 的发布回归存在 {skipped_count} 项 skip")
    return source_facts_verified


def run_hermes_contract(root: Path, hermes: Path, failures: list[str]) -> str | None:
    if not hermes.is_absolute() or not hermes.exists():
        add_failure(failures, "--hermes 必须是存在的绝对路径")
        return None
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "check_hermes_cli_contract.py"),
                "--hermes",
                str(hermes),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except (OSError, subprocess.SubprocessError):
        add_failure(failures, "指定 Hermes 的隔离 CLI 契约执行超时或不可用")
        return None
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if result.returncode != 0 or payload.get("result") != "PASS":
        add_failure(failures, "指定 Hermes 的隔离 CLI 契约未通过")
        return None
    version = payload.get("hermes_version")
    return version if isinstance(version, str) else "unknown"


def run_optional_mcp_runtime(
    root: Path,
    python: Path,
    hermes: Path | None,
    node: Path | None,
    failures: list[str],
) -> str | None:
    if not python.is_absolute() or not python.exists():
        add_failure(failures, "--mcp-python 必须是存在的绝对路径")
        return None
    if node is None:
        add_failure(failures, "完整 MCP 运行时验收还需要 --node 指向真实 Node.js 绝对路径")
        return None
    try:
        command = [
            str(python), str(root / "scripts" / "check_optional_mcp_runtime.py"),
            "--node", str(node),
        ]
        if hermes is not None:
            command.extend(["--hermes", str(hermes)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        add_failure(failures, "可选能力 MCP 运行时检查不可用")
        return None
    if (result.returncode != 0 or payload.get("result") != "PASS"
            or payload.get("mcp_elicitation") is not True
            or (hermes is not None and payload.get("hermes_stdio") is not True)):
        add_failure(failures, "可选能力 MCP 运行时未通过")
        return None
    return f"Python {payload.get('python', 'unknown')} / Node {payload.get('node', 'unknown')}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 build-wechat-assistant Skill")
    parser.add_argument("root", nargs="?", type=Path, help="Skill 根目录；默认使用脚本所在目录")
    parser.add_argument("--hermes", type=Path, help="发布门禁使用的官方干净 Hermes 启动器绝对路径")
    parser.add_argument("--mcp-python", type=Path, help="目标 Hermes 安装中可导入 MCP SDK 的 Python 绝对路径")
    parser.add_argument("--node", type=Path, help="飞书运行时使用的 Node.js 真实绝对路径")
    return parser.parse_args(argv)


def validate_optional_runtime_arguments(
    mcp_python: Path | None,
    node: Path | None,
    failures: list[str],
) -> None:
    if mcp_python is not None and node is None:
        add_failure(failures, "--mcp-python 还需要 --node 才能完成 MCP 运行时验收")
    if node is not None and mcp_python is None:
        add_failure(failures, "--node 只与 --mcp-python 的完整 MCP 运行时验收一起使用")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    hermes = args.hermes.resolve() if args.hermes else None
    # Preserve a venv entrypoint symlink: resolving it can silently switch to
    # the base interpreter and lose the MCP packages installed in that venv.
    mcp_python = args.mcp_python.absolute() if args.mcp_python else None
    node = args.node.absolute() if args.node else None
    failures: list[str] = []
    validate_optional_runtime_arguments(mcp_python, node, failures)
    validate_structure(root, failures)
    validate_version_file(root, failures)
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        return 1

    documents = {relative: read_text(root, relative) for relative in MARKDOWN_FILES}
    skill = documents["SKILL.md"]
    validate_frontmatter(skill, failures)
    validate_agent_metadata(read_text(root, "agents/openai.yaml"), failures)
    contract = load_contract(root)
    failures.extend(validate_contract(contract))
    failures.extend(validate_documents(root, contract))

    if len(skill.splitlines()) >= 500:
        add_failure(failures, "SKILL.md 必须少于 500 行")

    canonical_markdown = "\n".join(documents.values())
    release_text = "\n".join(read_text(root, relative) for relative in sorted(REQUIRED_FILES))
    validate_no_private_identifiers(release_text, failures)
    shell_blocks = "\n".join(re.findall(r"```(?:bash|sh|shell)\n(.*?)```", canonical_markdown, re.DOTALL))
    scan_text = canonical_markdown + "\n" + shell_blocks
    for pattern, label in FORBIDDEN_PATTERNS.items():
        if re.search(pattern, scan_text, re.IGNORECASE):
            add_failure(failures, f"发现{label}")

    for relative, phrases in REQUIRED_BY_FILE.items():
        for phrase in phrases:
            if phrase not in documents[relative]:
                add_failure(failures, f"{relative} 缺少关键契约：{phrase}")

    validate_local_links(root, skill, failures)
    source_facts_verified = run_flow_tests(root, failures, hermes)
    hermes_version = run_hermes_contract(root, hermes, failures) if hermes else None
    mcp_runtime_versions = (
        run_optional_mcp_runtime(root, mcp_python, hermes, node, failures)
        if mcp_python else None
    )

    if failures:
        for item in failures:
            print(f"FAIL {item}")
        return 1

    print(f"PASS build-wechat-assistant V{VERSION}")
    print(f"PASS exact file manifest: {len(REQUIRED_FILES)} files")
    print("PASS frontmatter and agent metadata")
    print("PASS structured safety contract and cross-file invariants")
    print("PASS deterministic contracts and mutation regression tests")
    if hermes_version is not None:
        print(f"PASS isolated Hermes CLI contract: {hermes_version}")
    elif source_facts_verified:
        print("NOTE local Hermes source facts passed; clean release CLI contract UNVERIFIED without --hermes")
    else:
        print("NOTE Hermes source facts and clean release CLI contract UNVERIFIED; rerun with --hermes")
    if mcp_runtime_versions is not None:
        print(f"PASS optional MCP runtime, elicitation, and Hermes stdio reconnect: {mcp_runtime_versions}")
    else:
        print("NOTE optional MCP runtime UNVERIFIED; rerun with --mcp-python and --node from the target environment")
    print("NOTE real installation, QR login, service restart, and Weixin use still require live evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())

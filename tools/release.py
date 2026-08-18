#!/usr/bin/env python3
"""发布 build-wechat-assistant 新版本：改版本号 -> 同步标题日期 -> 提交 -> 打 tag -> 推送。

用法:
    python3 tools/release.py V0.2
    python3 tools/release.py 0.2 "提交说明"

版本号必须形如 V0.2 或 0.2。脚本会同步 SKILL.md 标题、agents/openai.yaml、
flow-contract.json、VERSION 文件以及 references/ 里的版本号路径，并把
SKILL.md 标题与 flow_policy.SKILL_TITLE 里的日期刷新为发版当天（本地日期），
然后 git add、commit、打 tag、push origin main 和新 tag。
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = (".md", ".py", ".json", ".yaml", ".yml")
SKIP_DIRS = {".git", ".github", "tools"}

# 标题形如 `# 2026-08-15 微信 AI 助手搭建 V0.4`；只改写标题行的日期，
# 正文中出现的其他日期不受影响。
SKILL_MD_TITLE_DATE_RE = re.compile(
    r"(?m)^(# )\d{4}-\d{2}-\d{2}( 微信 AI 助手搭建 V\d+\.\d+)$"
)
POLICY_TITLE_DATE_RE = re.compile(
    r'(?m)^(SKILL_TITLE = f"# )\d{4}-\d{2}-\d{2}( 微信 AI 助手搭建 V\{VERSION\}")$'
)


def normalize(raw: str) -> tuple[str, str]:
    match = re.fullmatch(r"V?(\d+\.\d+)", raw.strip())
    if not match:
        raise SystemExit(f"版本号格式错误：{raw}（应为 V0.2 或 0.2）")
    num = match.group(1)
    return num, f"V{num}"


def current_version(root: Path) -> str:
    text = (root / "scripts" / "flow_policy.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("无法从 scripts/flow_policy.py 读取当前 VERSION")
    return match.group(1)


def iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(rel == part or rel.startswith(f"{part}/") for part in SKIP_DIRS):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name == "VERSION":
            files.append(path)
    return sorted(files)


def replace_version(root: Path, num_new: str, v_new: str) -> None:
    num_old = current_version(root)
    v_old = f"V{num_old}"
    v_old_lower = f"v{num_old}"
    # 只替换两位版本号，且后面不跟数字或点，避免误伤三位版本号（如 Hermes v0.20.0）
    # 或裸数字（如 RFC 文档 IP 地址 192.0.2.x / 203.0.113.x）。
    v_pat = re.compile(rf"{re.escape(v_old)}(?![0-9.])")
    v_lower_pat = re.compile(rf"{re.escape(v_old_lower)}(?![0-9.])")
    for path in iter_text_files(root):
        text = path.read_text(encoding="utf-8")
        new_text = v_pat.sub(v_new, text)
        new_text = v_lower_pat.sub(f"v{num_new}", new_text)
        # 精确替换版本号字段，不做任何全局裸数字替换
        if path.name == "flow_policy.py":
            new_text = re.sub(
                r'(^VERSION = ")[^"]+(")', rf"\g<1>{num_new}\g<2>", new_text, flags=re.MULTILINE
            )
        elif path.name == "flow-contract.json":
            new_text = re.sub(
                r'("skill_version": ")[^"]+(")', rf"\g<1>{num_new}\g<2>", new_text
            )
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"  已更新 {path.relative_to(root).as_posix()}")
    (root / "VERSION").write_text(f"{v_new}\n", encoding="utf-8")


def sync_title_date(root: Path, today: str | None = None) -> list[str]:
    """把 SKILL.md 标题与 flow_policy.SKILL_TITLE 的日期同步为发版当天。

    today 形如 YYYY-MM-DD；缺省取本地当天日期。两处标题必须同时找到，
    找不到任何一处就失败关闭，避免发版后验证器两边标题对不上。
    """
    day = today or date.today().isoformat()
    changed: list[str] = []
    targets = (
        (root / "SKILL.md", SKILL_MD_TITLE_DATE_RE),
        (root / "scripts" / "flow_policy.py", POLICY_TITLE_DATE_RE),
    )
    for path, pattern in targets:
        text = path.read_text(encoding="utf-8")
        new_text, count = pattern.subn(rf"\g<1>{day}\g<2>", text, count=1)
        if count != 1:
            raise SystemExit(f"{path.relative_to(root).as_posix()} 找不到可同步日期的版本标题")
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"  已更新 {path.relative_to(root).as_posix()} 标题日期为 {day}")
        changed.append(path.relative_to(root).as_posix())
    return changed


def run(cmd: list[str]) -> None:
    print("  $", " ".join(cmd))
    subprocess.run(cmd, cwd=SKILL_ROOT, check=True)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    num_new, v_new = normalize(sys.argv[1])
    commit_msg = sys.argv[2] if len(sys.argv) > 2 else f"发布 {v_new}"
    num_old = current_version(SKILL_ROOT)
    if num_old == num_new:
        raise SystemExit(f"当前已是 {v_new}，无需重复发布")
    print(f"发布 {v_new}（当前 V{num_old}）")
    replace_version(SKILL_ROOT, num_new, v_new)
    sync_title_date(SKILL_ROOT)
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", commit_msg])
    run(["git", "tag", v_new])
    run(["git", "push", "origin", "main", v_new])
    print(f"已发布 {v_new}")


if __name__ == "__main__":
    main()

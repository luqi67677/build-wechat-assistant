# 在不同 Agent 中安装本 Skill

本文分成“外部接收者”和“本仓库维护者”两条路线。外部用户不需要拥有作者的 Obsidian；维护者继续只保留一个权威实体目录：Obsidian 仓库中的 `自建skill/build-wechat-assistant/`。同一台维护机器上的 Codex、Hermes 及兼容宿主只能创建指向它的目录链接，不维护第二、第三份实体副本。下文命令全部由 Agent 或发布维护者执行，不要求普通用户打开终端；普通用户只选择宿主和目标位置、批准写入，并在系统权限窗口出现时确认。

## 目录

- [1. 先确认宿主范围](#1-先确认宿主范围)
- [2. 外部接收者安装](#2-外部接收者安装)
- [3. 维护者链接前门禁](#3-维护者链接前门禁)
- [4. macOS / Linux 创建链接](#4-macos--linux-创建链接)
- [5. 原生 Windows 创建目录联接](#5-原生-windows-创建目录联接)
- [6. 发现与触发验收](#6-发现与触发验收)
- [7. 验证命令](#7-验证命令)
- [8. Vault 移动后的恢复](#8-vault-移动后的恢复)

## 1. 先确认宿主范围

- **Codex 本地任务**：支持本地 `SKILL.md`，用户级目录 `~/.codex/skills/`，可使用发现目录链接。
- **Kimi Code**：支持本地 Skills，用户级目录 `~/.agents/skills/`，项目级目录 `<项目>/.agents/skills/`。
- **Claude Code**：支持本地 Skills，用户级目录 `~/.claude/skills/`，项目级目录 `<项目>/.claude/skills/`。
- **Hermes**：支持本地 Skills，但发现根目录由当前 `HERMES_HOME`/Profile 决定。
- **普通 ChatGPT 网页或桌面聊天**：不能因为名字叫 ChatGPT 就假定能读取本机 `SKILL.md`。只有当前产品明确提供本地 Agent Skills 和目录链接时才纳入。
- **其他工具**：先查当前官方文档；不支持目录链接的宿主不纳入单一源方案，不退回复制。

## 2. 外部接收者安装

### 2.1 从 GitHub 公开仓库直接安装（推荐给普通用户）

仓库公开后，外部用户最简单的安装方式是把仓库克隆到自己 Agent 的 skills 目录，目录名保持 `build-wechat-assistant`。以下命令全部由 Agent 执行，普通用户只在对话里确认目标宿主：

| Agent | 用户级 skills 目录 | 触发方式 |
|---|---|---|
| Codex | `~/.codex/skills/` | `$build-wechat-assistant` 或自然语言 |
| Kimi Code | `~/.agents/skills/`（项目级 `<项目>/.agents/skills/`） | 自然语言描述目标 |
| Claude Code | `~/.claude/skills/`（项目级 `<项目>/.claude/skills/`） | 自然语言描述目标 |
| Hermes | `<HERMES_HOME>/skills/`（以 `hermes -p <Profile> config path` 实际解析为准） | `/build-wechat-assistant` 或自然语言 |

安装后重新加载宿主，先确认 skills 列表出现 `build-wechat-assistant`，再按第 6 节验收能读到 `references/flow-contract.json`、`assets/SOUL.zh-CN.md` 和脚本。克隆安装的用户升级时由 Agent 在该目录执行 `git pull`；目标目录被手工改动过时先 `git status` 核对，不强制覆盖。

### 2.2 不可变发布包安装（推荐给需要核验完整性的场景）

外部发布物必须是不可变版本集合：`build-wechat-assistant-V<版本>.zip`、ZIP 的 `SHA256SUMS`、列出包内每个相对路径与 SHA-256 的 `FILES.sha256`，以及从同一版本单独导出的 `verify_release_package.py` 验证器。发布前验证者必须在全新临时目录用该验证器不覆盖解压，再运行第 7 节验证；还要确认包内没有 `.env`、auth、token、二维码、会话、记忆、日志、备份、本机绝对路径或真实用户 ID，验证脚本和夹具只能含假数据。ZIP 文件名和 Skill 内四处版本必须一致。

接收者只需在对话中选择宿主和一个独占新建的私有发布父目录，并从发布方受信通道取得 ZIP、`SHA256SUMS`、`FILES.sha256` 和验证器。Agent 先核对验证器本身的 SHA-256 等于 `FILES.sha256` 中 `./scripts/verify_release_package.py` 的值，再把前三个制品放入发布父目录，验证器留在该目录之外。Agent 在父目录内另建一个名称精确为 `skill` 的空私有子目录，并用验证器执行不覆盖解压；它会在任何写入前拒绝绝对路径、`..`、反斜杠路径、大小写/Unicode 冲突、符号链接、异常类型、清单外文件、非空目标和目标范围错位。ZIP 与双清单保留在父目录，不得混入最终 `skill` 文件树。通过后由 Agent 对 `skill` 子目录运行第 7 节验证；只有全部通过才把这个子目录作为最终实体目录放入目标宿主当前官方的 Skill 发现目录。哈希只能证明文件与发布集合一致，不能代替发布者身份签名；当前发布没有签名或公开不可变 tag 时必须明确写“发布者身份未加密验证”，不能宣传为供应链已签名。需要第二个宿主时，再由 Agent 按本机路径为它建立指向这一个实体目录的链接；不要复制第二份。宿主不支持目录链接时，只安装到一个宿主，或者由发布流程生成可追溯的独立制品，不能假装仍是单一实时源。

作者本机的 Obsidian 绝对路径、`~/.codex`、`~/.hermes` 或 Windows `%LOCALAPPDATA%` 示例都不是接收者的固定路径。Agent 必须先读取当前宿主官方文档和实际 Profile 路径，再执行安装；目标已存在时比较并停止，不覆盖。

## 3. 维护者链接前门禁

1. 权威目录必须是绝对路径、真实目录，且包含 `SKILL.md`、`agents/`、`assets/`、`references/` 和 `scripts/`。
2. 先运行本目录验证器；失败时不创建链接。
3. 精确检查目标：不存在、普通目录、符号链接、Windows junction（目录联接）或断链。不得用模糊 glob 删除。
4. 目标是普通目录时，先比较内容。不同则停止让用户决定；相同也不能自动删除，必须取得明确确认后只处理这个目录。
5. 目标是断链时，从 Obsidian 直接打开本文件修复；不要依赖已经无法触发的 Skill 自救。
6. 不链接到临时目录，不创建循环链接，不使用同步软件再复制一份。

## 4. macOS / Linux 创建链接

以下命令只适用于目标不存在的情况。先把占位符替换为已核验的权威绝对路径；任一检查失败立即停止：

```bash
CANONICAL='<Obsidian权威目录绝对路径>'
CODEX_LINK="$HOME/.codex/skills/build-wechat-assistant"
HERMES_LINK='<当前HERMES_HOME>/skills/build-wechat-assistant'

test -d "$CANONICAL"
test -f "$CANONICAL/SKILL.md"
test ! -e "$CODEX_LINK" && test ! -L "$CODEX_LINK"
test ! -e "$HERMES_LINK" && test ! -L "$HERMES_LINK"
mkdir -p "$(dirname "$CODEX_LINK")" "$(dirname "$HERMES_LINK")"
ln -s "$CANONICAL" "$CODEX_LINK"
ln -s "$CANONICAL" "$HERMES_LINK"
```

Hermes 默认 Profile 常见根目录是 `~/.hermes`，但不能硬编码。先用当前 `hermes profile`、默认 Profile 的 `hermes -p default config path` 或目标 Profile 的 `hermes -p <Profile> config path` 确认真实状态根；命名 Profile 要在自己的 `skills/` 下建立链接。

验证：

```bash
realpath "$CODEX_LINK"
realpath "$HERMES_LINK"
```

两个结果必须与 `CANONICAL` 完全一致。

## 5. 原生 Windows 创建目录联接

Windows 默认 Hermes 数据根是 `%LOCALAPPDATA%\hermes`，不是 `~/.hermes`。命名 Profile 仍应先用当前 Hermes 命令确认真实数据根。对目录优先使用 junction，避免依赖 Developer Mode；以下脚本只在两个目标都不存在时创建：

```powershell
$ErrorActionPreference = 'Stop'
$canonical = (Resolve-Path -LiteralPath '<Obsidian权威目录绝对路径>').Path
if (-not (Test-Path -LiteralPath (Join-Path $canonical 'SKILL.md') -PathType Leaf)) {
    throw '权威目录缺少 SKILL.md，已停止。'
}

$hermesHome = [Environment]::GetEnvironmentVariable('HERMES_HOME', 'User')
if ([string]::IsNullOrWhiteSpace($hermesHome)) {
    $hermesHome = Join-Path $env:LOCALAPPDATA 'hermes'
}
$codexLink = Join-Path $env:USERPROFILE '.codex\skills\build-wechat-assistant'
$hermesLink = Join-Path $hermesHome 'skills\build-wechat-assistant'

foreach ($link in @($codexLink, $hermesLink)) {
    if (Test-Path -LiteralPath $link) { throw "目标已存在，先检查且不要覆盖：$link" }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $link) | Out-Null
    New-Item -ItemType Junction -Path $link -Target $canonical | Out-Null
}
```

创建后分别运行 `Get-Item -LiteralPath <目标> | Select-Object FullName, LinkType, Target`，要求 `LinkType` 为 `Junction` 且 `Target` 等于权威目录。失败时报告“发生了什么 + 用户该怎么办”，不改用复制模式。

## 6. 发现与触发验收

1. Codex 重启或新建任务，确认能看到 `build-wechat-assistant`，再用 `$build-wechat-assistant` 触发。
2. Hermes 使用目标 Profile 新开会话，由 Agent 运行 `hermes -p <Profile> skills list --enabled-only`，用户再输入 `/build-wechat-assistant` 或“帮我搭建微信 AI 助手”。
3. 两个宿主都必须读取到 `references/flow-contract.json`、`assets/SOUL.zh-CN.md` 和脚本；只在列表出现名称不算完整通过。
4. Hub 来源才使用 `hermes -p <Profile> skills audit --deep`。该命令只覆盖 Hub 安装；本地链接返回 `No hub-installed skills to audit.` 不代表安全审计通过。

## 7. 验证命令

以下验证命令由 Agent 或发布维护者在权威实体目录或刚解压的外部发布目录运行，不交给普通用户：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_skill.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
```

POSIX 环境必须保留上述禁写字节码变量，否则独立 unittest 会在精确发布树中生成 `__pycache__` 并污染后续清单核验。原生 Windows 使用当前 shell 的等价临时环境变量；不能靠运行后删除缓存来伪造干净发布树。

第一条不带 `--hermes` 时只做结构、流程和当前环境能执行的事实测试；若本机没有 Hermes，结果必须明确显示 `UNVERIFIED`，不能作为发布门禁。发布维护者必须对本次声明支持的官方干净 Hermes 启动器绝对路径运行：

```bash
python3 scripts/validate_skill.py --hermes <官方干净Hermes启动器绝对路径> --mcp-python <该Hermes安装中可导入mcp的Python绝对路径> --node <Node.js真实绝对路径>
```

它会把 Hermes 绝对路径传给源码事实与隔离 Cron 测试，并用保持虚拟环境入口不被解析掉的 MCP Python 建立知识库、飞书和编程三个真实 FastMCP 工具清单，核验 elicitation 逐次确认可用；飞书路径还会实际运行 `node --version` 和无外部输入的 JavaScript 身份探针，核对 `process.release`、`process.versions` 与 `process.execPath`，拒绝把任意可执行文件或只会伪造版本字符串的脚本冒充 Node.js，再通过这份显式 Node.js 路径完成隔离读取和固定目录创建。随后在全新隔离 Profile 中实际执行 `mcp add` 和 `mcp test`，证明三个 stdio 服务都能保存并重连到精确工具清单。发布模式要求全量回归 skip 数精确为零、源码事实 30/30 实际执行；任何错误 Profile 路径、缺 provider 明确认证入口、缺 Weixin、`gateway status --deep` 不能真实执行、Cron 路径穿越未拒绝、MCP 缺确认能力、MCP 无法重连、二维码向导服务提示语义漂移、官方 Weixin 配置键不兼容或额外默认工具都会使发布验证失败。这个通过仍不等于模型、二维码、微信往返、真实飞书写入、真实 Codex 调用或系统服务已经现场验收。

再使用具备 PyYAML 的当前 Python 环境运行 skill-creator 官方 `quick_validate.py`。原生 Windows 不保证命令名是 `python3`；使用 `py`、`python` 或 Hermes venv 中实际可用的解释器。缺依赖时标记未验证并修复验证环境，不能换成关键词自检。

`python3 -m unittest` 会同时运行 `test_source_facts.py`：本机装有 Hermes 时，它核验文档引用的向导英文文案、配置键、环境变量和隐藏参数说明与真实安装一致；未安装时自动跳过并标记未验证，不伪造通过。正式发布记录必须保存带 `--hermes` 的 PASS 与版本号，不能只摘录普通 `PASS build-wechat-assistant`。

**版本同步清单**：修改版本号或标题日期时必须同步五处——`SKILL.md` 标题、`VERSION` 文件、`scripts/flow_policy.py` 的 `VERSION` 与 `SKILL_TITLE`、`references/flow-contract.json` 的 `skill_version`、`agents/openai.yaml` 的 `short_description`。验证器与契约测试强制核对，漏改任何一处即 FAIL。

## 8. Vault 移动后的恢复

Vault 改名、移动或更换用户后，绝对链接会断。此时从 Obsidian 直接打开本文件：重新定位权威目录 → 精确检查旧目标确为断链 → 取得用户确认后只移除该链接对象 → 按对应平台重新创建 → 重跑 realpath/Target、验证器和两个宿主的全新会话触发。不要删除链接所指向的实体目录。

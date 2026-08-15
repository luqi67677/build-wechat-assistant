# Hermes 与微信基础搭建

最后核验：2026-08-15。官方 tag v2026.8.3 对应 Hermes v0.20.0；V0.1 已在该官方干净 tag 的隔离临时根通过 CLI 契约、扫码前工具收缩、Cron 正反路径，以及知识库、飞书、编程三个 MCP 的保存与 stdio 重连检查，没有使用模型秘密、没有生成二维码，也没有安装服务。新安装仍必须对目标机实际 Hermes 绝对路径重跑 `scripts/check_hermes_cli_contract.py`；Windows、Linux、真实模型、真实扫码和开机恢复仍需各自平台证据。执行前同时读取当前官方文档、`hermes --version` 和对应 `--help`。

本文件中的命令与检查项全部由 Agent 在已选择的目标环境执行，不要求普通用户打开终端、复制命令或补占位符。用户只负责批准安装、系统权限确认、官方登录、秘密输入和扫码。

## 目录

- [1. 识别运行环境](#1-识别运行环境)
- [2. 安装 Hermes](#2-安装-hermes)
- [2.5 建立唯一 Profile](#25-建立唯一-profile)
- [3. Weixin iLink 的真实边界](#3-weixin-ilink-的真实边界)
- [4. 后台服务与本地在线](#4-后台服务与本地在线)
- [5. SOUL 的安全修改](#5-soul-的安全修改)
- [6. 诊断顺序](#6-诊断顺序)
- [7. 基础验收](#7-基础验收)

## 官方来源

- 安装：https://hermes-agent.nousresearch.com/docs/getting-started/installation
- 快速开始：https://hermes-agent.nousresearch.com/docs/getting-started/quickstart
- 模型：https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models
- Weixin：https://hermes-agent.nousresearch.com/docs/user-guide/messaging/weixin
- 配置：https://hermes-agent.nousresearch.com/docs/user-guide/configuration
- 定时任务：https://hermes-agent.nousresearch.com/docs/user-guide/features/cron

## 1. 识别运行环境

本 Skill 可能运行在不同 Agent 中：

- 运行在 Hermes：Hermes 已安装，验证版本、模型和网关即可。
- 运行在 Codex 或其他工具：先检查本机是否已有 Hermes，再决定是否安装。
- 运行在云服务器：先确认用户已选择云端路线，再读取 `cloud-deployment.md`。
- 用户说不准碰现有助手、正在已有助手的机器上验收 Skill，或目标根不确定：在任何本机 Hermes 读取前进入受保护验收；不得读取真实根的 `profile list`、`gateway list`、doctor、日志或配置。纯自动化且不使用真实账号时，用 `scripts/isolation_guard.py create-root --purpose local-test` 在操作系统临时目录创建全新私有根；要输入模型密钥、扫码、授权外部账号或跨多轮验收时，用 `--purpose local-persistent` 在隔离器固定的当前用户私有应用数据目录创建持久隔离根。两者的所有 Hermes 命令都经 `isolation_guard.py run` 清除继承的模型/微信秘密并绑定新 `HOME`、`HERMES_HOME` 和共享认证目录；Profile 创建后、首次模型认证前必须通过一次 `check-fresh`。持久根通过后跨轮复用，仍有效的专用凭据不得重复索取；只有用户撤销、凭据失效或主动要求更换时才重新认证。不得用“先备份再覆盖”的方式修改现有助手。重复执行安装会覆盖或更新现有环境时，只验证安装器来源与独立临时安装产物，并明确标记“未在全新电脑实装”。

安装前先按目标系统选择唯一分支，不把 POSIX 命令发给原生 Windows。

### macOS（只支持 Apple Silicon）

```bash
uname -s
uname -m
sw_vers -productVersion
git --version
g++ --version
command -v hermes
```

`uname -s` 必须为 `Darwin`，`uname -m` 必须为 `arm64`，`sw_vers -productVersion` 必须为 macOS 12 或更高。Intel Mac（`x86_64`）或更早系统必须在下载前停止。`git --version` 或 `g++ --version` 失败时，说明缺少 Git 或 Desktop 原生模块编译依赖，再按 Apple 官方方式补齐 Command Line Tools；不能只凭 `command -v` 判定可用。

### 原生 Windows 10/11（PowerShell）

```powershell
$PSVersionTable.PSVersion
(Get-CimInstance Win32_OperatingSystem).Caption
$env:PROCESSOR_ARCHITECTURE
Get-Command hermes -ErrorAction SilentlyContinue
```

只支持 Windows 10/11 的 x86_64 或 ARM64。未安装 Hermes 时不要求用户先装 Git；官方 Desktop/PowerShell 安装器会处理 PortableGit。安装器写入 User PATH 后必须由 Agent 启动新的 PowerShell 进程，再运行 `Get-Command hermes`，不能用仍持有旧 PATH 的 Agent 进程误判安装失败。

### Linux 或 WSL2

```bash
uname -srm
git --version
curl --version
xz --version
command -v hermes
```

只继续支持的 x86_64 或 aarch64 Linux。Git、curl、xz 任一命令失败时，在下载前停止并按当前发行版说明补齐；不要等安装器中途失败。

## 2. 安装 Hermes

macOS 和 Windows 普通用户优先使用 Hermes Desktop 官方安装器。Desktop 安装后必须由 Agent 在新进程环境验证 `hermes --version` 可用，用户不用另开终端；本 Skill 后续全部操作依赖 `hermes` CLI，CLI 不存在或不可用时改用当前官方命令行安装，并把 Desktop 路线标记为未实测。用户明确选择命令行并批准下载安装后，先重新打开上方官方安装页核对当日命令与域名。不要把远程脚本直接通过管道交给 shell：先下载到新建的临时目录，核对来源、脚本内容和本次 SHA-256，再向用户说明将安装到哪里；得到明确确认后才单独执行该临时脚本。官方没有发布可核对的签名或哈希时必须如实说明，不能把自己计算的 SHA-256 当成官方背书。

安装器会处理所需运行时与虚拟环境。不要同时堆叠系统 Python、多个 `pip install`、非官方镜像或 `sudo pip`。

Agent 必须保留安装阶段的脱敏结果分类，而不是只看最后一行。官方安装器优先使用 `uv.lock` 的哈希锁定依赖；若出现锁文件需要更新并自动退回 PyPI 重新解析，功能安装可以继续做核心 CLI 验证，但本轮供应链状态必须记为“未达到锁文件可复现标准”，不能记为严格供应链通过。仓库克隆超时属于网络下载失败；Node/浏览器、`ripgrep`、`ffmpeg` 等可选依赖未完成属于对应能力未验证，不自动等同于 Hermes 核心 CLI 失败。每一类都用中文告诉用户“发生了什么 + 接下来由 Agent 做什么”，停止无进展的盲目重试；不得让用户根据英文日志自行判断，也不得因为核心 CLI 已能启动就把全部可选依赖标成已安装。

安装后由 Agent 在新进程环境或官方安装位置解析出的绝对启动器中检查，不要求用户另开终端；当前 Agent 仍找不到命令时先验证绝对启动器，不重复安装：

```bash
hermes --version
```

原生 Windows 由 Agent 启动新的 PowerShell 进程做等价检查；不要使用 `uname`、`command -v`、`tail` 或假定存在的 `python3`。仍失败时告诉用户“安装已经完成，但当前 Agent 进程还没有读取新的 PATH；我会改用已确认的安装路径继续检查”，而不是让用户打开 PowerShell 或反复安装。安装器本身只用全局 `--version` 验证；Profile 创建后先建立模型调用前安全基线，凭据来源分类并取得用户决定后才可运行带 `-p <Profile>` 的 `doctor`。顶层 `status --deep` 会汇总认证、路径与环境元数据，不得把原始输出捕获进 Agent 日志。

### Hermes 能力闸

不把“最低版本闸”写成容易漂移的单一版本号。以下能力必须逐项从当前 `--help`、官方 Weixin 文档和实际命令确认：

1. `hermes -p <Profile> ...` 能绑定命名 Profile。注意：`-p` 是隐藏全局参数，`hermes --help` 不显示；以 `hermes -p <Profile> config path` 实际解析到目标 Profile 目录为通过标准，不能只查帮助文本。
2. `hermes -p <Profile> gateway run` 能以前台方式临时验收。
3. `scripts/setup_weixin_direct.py plan` 能从目标 Hermes 运行时载入 Weixin 扫码组件；真实扫码时系统能提供不会回传内容的 TTY 二维码窗口。
4. `hermes -p <Profile> tools list --platform weixin` 与启停工具命令存在。
5. `hermes -p <Profile> gateway install --help` 同时显示立即启动和登录/开机启动的正反参数。
6. `hermes -p <Profile> gateway status --deep` 可用。
7. `python3 <Skill绝对路径>/scripts/check_hermes_cli_contract.py --hermes <HERMES绝对路径>` 在全新临时 Hermes 根返回 PASS；它不会读取真实 Profile、模型秘密或微信凭据，也不会安装服务。

任一能力缺失或当前平台语义无法确认时，停止在第 2 步；升级前说明备份、配置和服务重启影响。

官方 v0.20.0 源码与隔离检查还确认：macOS 的 `gateway install` 会写入 `RunAtLoad + KeepAlive` 并会立即加载 launchd 服务，当前 macOS 分支不会处理 `--start-now/--no-start-now` 与 `--start-on-login/--no-start-on-login` 这两个启动参数组。第 4 步因此不得在 macOS 安装服务；只在第 5 步真实验收通过后执行持久安装。Linux systemd 与 Windows 的参数语义仍按目标机实际能力闸验证，不能从 macOS 外推。

已安装时不自动运行 `hermes update`。先说明当前版本、更新内容和影响，再由用户决定。

## 2.5 建立唯一 Profile

Hermes 健康后、任何模型或 Weixin 写入前，必须确定唯一的非 `default` Profile。普通新用户默认创建 `wechatassistant`；若已存在不得复用，必须选择另一个不存在的小写字母数字名称。只有用户明确授权修复/迁移已核验的目标助手时才可复用。严格验收不运行下列真实根列表命令，改用本节后面的独占根流程。不得使用 `--clone` 或 `--clone-all`，因为它们会复制配置、`.env`、SOUL 或其他状态：

```bash
# 普通新用户路线；严格验收不使用本段
hermes profile list
hermes profile create wechatassistant --no-alias --no-skills
hermes profile show <Profile>
hermes -p <Profile> config path
hermes -p <Profile> config env-path
```

`profile show` 的名称是位置参数；把名称只放进全局 `-p` 后会使 v0.20.0 的该子命令缺少必填名称。三个读取必须指向同一个命名 Profile。之后所有模型、工具、网关、日志和服务命令都显式带同一个 `-p <Profile>`。

命名 Profile 只隔离状态目录，不等于凭据隔离。当前 Hermes 可能从全局认证、共享 OAuth 或启动进程环境取得模型凭据；外部 CLI 默认也可能共享真实 HOME。常规用户要复用共享认证时，先以不含值的来源标签说明账单和撤销影响并取得同意。严格隔离测试必须执行：

```bash
python3 <Skill绝对路径>/scripts/isolation_guard.py create-root --purpose local-test --root <本轮全新Hermes根绝对路径>
python3 <Skill绝对路径>/scripts/isolation_guard.py create-root --purpose local-persistent --root <本轮全新Hermes根绝对路径>
python3 <Skill绝对路径>/scripts/isolation_guard.py run --root <本轮全新Hermes根绝对路径> --hermes <HERMES绝对路径> -- profile create <Profile> --no-alias --no-skills
python3 <Skill绝对路径>/scripts/isolation_guard.py check-fresh --root <本轮全新Hermes根绝对路径> --profile <Profile> --hermes <HERMES绝对路径>
```

后续 Hermes 命令继续使用 `isolation_guard.py run ... -- -p <Profile> ...`；扫码前后检查器都传同一 `--expected-hermes-root <本轮全新Hermes根绝对路径>`。`check-fresh` 必须在任何配置写入和模型认证前立即执行，证明 Profile 与根级 sessions/memories、隔离 OS HOME、shared 认证目录均为空，根级 config/.env/auth 不存在，Profile `.env` 私有且没有非空秘密、持久服务定义不存在、没有手工 gateway 进程且路径精确落在该新根。独立根中本轮新建的 OAuth 可以使用，但不能借用生产根的共享认证，也不能以“命名 Profile 中的模型请求成功”证明未借用生产 token。Windows 当前没有该 POSIX 严格门禁的等价实现；受保护助手场景只能在独立虚拟机/独立操作系统账号验收，或把相关项列为未验证，不能降级读取现有根。

后续每条 Hermes 命令都由 Agent 按操作模式生成：普通全新/已授权增量使用同一 Profile，受保护验收使用 `run`，云端交互使用 `run-cloud`，gateway 服务生命周期才使用 `run-service`。云端 `run-cloud` 保留全新服务账号真实 HOME，与最终 systemd gateway 的 HOME 级认证位置一致。云端 API key 由 `launch_trusted_handoff.py` 先等待隔离 runner 的真实 TTY 进入 Hermes 掩码提示，再打开宿主原生隐藏框并自动填标签；用户不看终端、不补第二次回车。OAuth 与微信扫码仍由同一交接器打开受信窗口，微信再绑定 `setup_weixin_direct.py`，不进入通用 `gateway setup`。云端还必须显式绑定远端服务账号和 `root-runuser` / `sudo` / `direct` 之一，`direct` 只接受明确写出且匹配的 SSH 用户。非 TTY 返回 `trusted_tty_required`。模型认证先锁定 provider 并走明确 `auth add` 路由，禁止用通用 `model` 向导猜厂商。Qwen 旧 OAuth 规则继续按当前官方资料失败关闭。所有占位符、路径、根用途和 Profile 都由 Agent 解析核验，不交给用户拼接。

`check-fresh` 通过后、任何 `doctor`、模型认证或真实请求前，建立不在同步盘、Documents、Obsidian 或主目录根的私有专用工作区，运行 `apply_chat_safety_baseline.py` 并紧接着运行 `check_pre_qr_safety.py`。两者必须绑定同一 Hermes 绝对路径、Profile、已批准根与工作区；受保护验收使用 `isolation_guard.py run-checker`。门禁会同时检查 CLI 与 Weixin 精确只启用 `clarify`、两边没有 MCP、推理与记忆注入关闭、gateway 未安装且未运行。模型认证后、首次真实请求前必须再运行同一个门禁；模型向导改变边界时重新应用基线再复验。模型探测成功后还要重新运行安全基线与门禁，收紧 v0.20.0 运行时可能新建的 Profile 缓存权限；这次 PASS 后才进入扫码。

## 3. Weixin iLink 的真实边界

Hermes 的 Weixin 适配器连接腾讯 `ilinkai.weixin.qq.com` 的 iLink Bot API。它是独立机器人身份，不等同于扫码者的普通个人微信号。

安全原则：

- 使用 Hermes 当前官方集成，不使用注入、Hook、协议逆向或来历不明的插件。
- 默认仅私聊；不群发、不自动加人、不高频骚扰。
- 单人基础助手只使用 owner-only `allowlist`，群聊保持 `disabled`。`pairing` 只属于用户明确要求的多人扩展，必须另做批准、撤销和陌生账号负向验收，不能满足单人基础完成条件。
- 不能保证封号风险为零；平台规则、服务策略和滥用行为都可能改变风险。
- 普通微信群事件经常不会投递给 iLink 机器人，不能用高风险方式绕过。

扫码前先执行 `security-boundary.md` 的静态门禁：创建专用工作区、设置 `terminal.cwd`，并把 Weixin 收缩到仅聊天档。扫码前没有 Weixin 会话，不能把本地 CLI、oneshot 或工具清单冒充越界、审批和提示注入的运行时证据；五项运行时负向测试必须在 SOUL 写入、gateway 临时启动并创建新微信会话后完成。

微信标准操作是按上述模式路由生成的 `setup_weixin_direct.py run`；不得以 `-p <Profile> gateway setup` 代替。受保护验收不能退回裸 Hermes，云端必须绑定 `run-cloud` 语义。默认不向用户展示命令；末级回退也必须是已完成全部占位替换的唯一原子命令。

第 4 步不安装本地后台服务。先写入并核验 SOUL，再在独立终端运行 `hermes -p <Profile> gateway run` 做临时验收；普通本地路线全部通过后用同一 Profile 的 `gateway stop` 正常停止前台进程，再运行 `hermes -p <Profile> gateway install --force --start-now --start-on-login` 建立持久服务。受保护验收只允许 `isolation_guard.py run ... -- -p <Profile> gateway stop` 停止临时 gateway，不安装持久服务；前台重启往返通过仍只证明“聊天可用”。这样不会依赖 Ctrl+C 的进程组传播，也不会依赖 macOS 忽略 `--no-start-*` 或 Windows 双否时根本不安装服务的错误语义。

官方干净 v0.20.0 的 Weixin 二维码向导需要真实 TTY，且菜单仍为英文。优先使用系统自带终端，不要求用户安装第三方终端；每次页面变化都按 `weixin-setup-zh.md` 在对话中给出当前屏幕的中文意思、安全推荐和操作。受保护/云端 runner 会在二维码生成前机械拒绝非 TTY，不能为了继续而退回裸命令。二维码与短时 URL 只显示在用户控制的终端或官方受保护界面，不复制到聊天。云端只有一部手机且没有第二块可信屏幕时，在购买服务器前停止；当前未验证同手机打开短时 URL 能完成扫码授权。没有真实 TTY 或受保护界面时也停止，不能用 Word 或普通文本框代替。

选择 Weixin 后扫码并确认。二维码无法渲染时只在不回传 stdout 的用户控制终端使用向导给出的短时 URL。明确缺少 messaging 依赖时，先按当前操作模式运行同一 Profile 的 `doctor` 并按当前官方安装器的修复路径处理；不使用固定的 `~/.hermes` 路径，也不让原生 Windows 执行 POSIX 命令。

只有出现配对请求时才处理：

```bash
hermes -p <Profile> pairing list
hermes -p <Profile> pairing approve --help
hermes -p <Profile> pairing approve <平台名> <code>
```

平台名以当前 `hermes -p <Profile> pairing approve --help` 实际列出为准；v0.20.0 的帮助仍未列出 `weixin`，当前版本不接受该平台名时改用 allowlist，不猜命令。

## 4. 后台服务与本地在线

本地验收与持久运行分成两个阶段：

```bash
# SOUL 写好后，在独立终端临时验收
hermes -p <Profile> gateway run

# 全部验收通过，停止前台 gateway 后再启用持久服务
hermes -p <Profile> gateway install --force --start-now --start-on-login
hermes -p <Profile> gateway status --deep
```

之后才使用同一 Profile 的 `gateway start|stop|restart|status` 管理。macOS 和 Windows 的自动恢复点是“目标用户登录后”，不是停留在系统登录界面时；Linux 已启用的服务才可按开机恢复验收。云端按 `cloud-deployment.md` 使用非 root 服务账号自己的用户级 systemd unit + linger；所有 Hermes 命令在该账号真实登录会话运行，管理员只单独启用 linger，不能用 sudo/root 解析 Profile。不要直接操作 launchd/systemd 文件；唯一例外是云端文档要求由当前版本 `systemd_env_guard.py install` 安装的固定 `UnsetEnvironment=` drop-in，仍不得手写或覆盖冲突文件。电脑关机或睡眠时，本地 gateway 不在线；不要擅自全局关闭睡眠。

## 5. SOUL 的安全修改

1. 用 `hermes -p <Profile> config path` 确认正确 Hermes home。
2. 只读取目标 `SOUL.md`，不顺手读取 `.env`。
3. 全新 Profile 直接新建，不读取或备份其他助手；只有已授权增量目标才可读取已有文件，备份范围、私有位置和恢复方法先由用户确认。
4. 使用 `assets/SOUL.zh-CN.md` 生成最小差异，不写入秘密。
5. 修改后必须启动新会话；重启网关只会重新加载程序，不能替换已经存在的微信旧会话人格。全新搭建应在第一条微信消息之前写入 SOUL；已有旧会话时按当前官方 `/new` 流程切换，不能静默删除历史。

微信当前可能显示英文确认卡片 `Confirm /new`。Agent 必须当屏解释，不让用户猜：`Approve Once` 表示“只确认这一次新建会话”，是普通验收的推荐选择，对应文字回复 `/approve`；`Always Approve` 表示“以后永久不再询问”，对应 `/always`，本 Skill 不推荐；`Cancel` 表示“保留当前会话”，对应 `/cancel`。这一步会丢弃当前助手会话中的上下文，但不会删除微信聊天记录、模型凭据、知识库或代码项目。只让用户完成当前一个动作；不得把 `/approve` 与后续工具写入审批混成永久授权。

如果确认卡片出现后 gateway 被受控重启或意外断开，旧确认立即失效。Agent 恢复并核对连接后，必须明确告诉用户“刚才没有完成新建会话”，重新发起一次 `/new`；不得让用户对旧卡片继续回复，也不得把 `Gateway shutting down` 误报为 `/new` 已成功。

## 6. 诊断顺序

只有目标 Profile 已绑定、安全基线通过、凭据来源已分类且用户同意可能访问 provider 后，才把 `doctor` 用作诊断。以下命令不整段回传；每次只运行解决当前故障所需的一项，并只摘取脱敏结论：

```bash
hermes -p <Profile> doctor
hermes -p <Profile> auth status <provider>
hermes -p <Profile> gateway status --deep
hermes -p <Profile> pairing list
hermes -p <Profile> logs errors
hermes -p <Profile> logs --since 10m
```

只提取与当前故障相关的日志行并脱敏。对用户先输出中文解释，再给必要命令；不要读取或回显完整 `.env`、token、API key、Cookie 或服务器密码。

## 7. 基础验收

逐项记录证据：

| 项目 | 通过标准 |
|---|---|
| Hermes | version、CLI 能力闸和模型调用前安全门禁无阻断问题；运行过 doctor 时只记录脱敏结论 |
| 主模型 | 本地中文对话真实返回 |
| 视觉模型 | 若配置，真实图片分析成功 |
| Weixin | deep status 显示连接，微信私聊真实往返 |
| 人格 | 本地和微信回复都符合 SOUL |
| 访问控制 | 许可名单非空且只有主人、群聊关闭；第二账号不可用时外部拒绝测试标记未验证 |
| 执行边界 | 扫码前静态门禁和扫码后 Weixin 运行时负向测试均通过 |
| 持久运行 | 只有服务绑定同一 Profile、gateway 受控重启后再次真实往返才通过 |
| 开机恢复 | 只有真实机器重启后再次真实往返才通过；未做时标记未验证 |
| 发布级质量 | 连续 10 轮、长消息、快速双消息与安全故障注入另行记录，不阻断第一次聊天可用 |
| 中文体验 | 进度与错误均以用户能理解的中文说明 |

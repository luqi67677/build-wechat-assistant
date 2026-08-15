# 云端 24 小时在线

开始基础搭建前就读取本文件，用它完成第一步“选择运行位置”。只有用户明确选择云端后才能购买或部署；选择云端后，Hermes、模型、微信和人格直接在云端完成一次，本地不再先搭一套。购买、实名、费用和服务条款必须由用户本人确认。

云端的只读检查、安装、SSH、文件传输、配置、服务启停和验证全部由 Agent 执行。用户不输入 SSH 或服务器命令；只有云平台登录、购买/实名确认、管理员密码提示、模型官方登录和微信扫码需要用户本人操作。

## 目录

- [1. 先判断是否需要云端](#1-先判断是否需要云端)
- [2. 用户已有任意服务器](#2-用户已有任意服务器)
- [2.5 云端模型凭据](#25-云端模型凭据)
- [3. 安全前提](#3-安全前提)
- [4. 全新云端部署与已有助手迁移](#4-全新云端部署与已有助手迁移)
- [5. 云端知识库](#5-云端知识库)
- [6. 验收与回退](#6-验收与回退)

若用户已经有一个真实可用的本地助手、后来才决定迁移云端，才进入迁移路线。迁移不是新用户基础流程：只迁移确认过的非秘密配置，模型与微信是否必须重新认证以当前 Hermes 官方能力和安全边界为准，并在受控切换点保证只有一个网关轮询。不得把迁移需要的重新认证当成所有新用户都要经历的重复步骤。

## 1. 先判断是否需要云端

第一问只有一个决定：“你是否必须在电脑关机后仍能使用助手？”未明确需要时推荐本地，不查询云价。明确需要云端后再问是否已有服务器：已有服务器直接进入第 2 节，跳过购买比较；没有服务器才继续本节的扫码设备门禁和当日价格比较。

先做扫码设备门禁：云端终端负责显示二维码，微信手机负责扫描。当前 Weixin 实现会输出用于扫码的 liteapp URL，但本 Skill 对“同手机打开短时 URL 完成授权”标记为未验证，不能据此承诺单设备可行。用户只有一部手机且没有第二块可信屏幕（电脑、平板或另一部受信设备）时，必须在购买服务器前停止；先解决显示二维码的设备条件，避免付款后才卡住。

没有第二块可信屏幕时给用户两条明确出路，不把流程堵死：

1. **临时借用任一受信设备**（自己的平板或电脑、家人受信设备）只用于显示二维码；二维码是短时登录凭据，借用设备不保存、不拍照、不转发，扫码完成后即可归还。只需借用几分钟，不需要在借用设备上安装任何东西。
2. **先走本地路线体验**，之后确实需要全天在线时，再按本文件“已有助手迁移”流程上云。

两条都不可行时才暂停云端路线；不得以“先买了再说”绕过该门禁。

只做当前路线必要的只读检测，不读取凭据内容：

- 只有用户明确迁移一位已知本地助手并授权读取该目标时，才检查该目标本地 gateway 是否在运行、是否随开机启动。全新云端或受保护验收不得读取本地 Hermes/gateway；云端决策不依赖它。
- 是否安装 `aliyun`、`tccli` 等云厂商 CLI，是否存在已登录状态；只报告“检测到/未检测到”，不输出配置文件或 token。
- 用户明确提供的服务器是否能连接；未获授权不扫描 SSH 配置、局域网或公网资产。

只有用户明确需要云端且没有服务器时，才展示下面的当日对比表：

| 方案 | 最低可核实价格 | 特点 | 限制与风险 | 适合谁 | 官方介绍 | 官方价格/购买 |
|---|---:|---|---|---|---|---|
| 本地电脑开机在线 | ¥0 服务器费 | 不迁移、最省钱 | 关机或睡眠即离线 | 先体验、非全天使用 | 当前电脑系统说明 | 不涉及购买 |
| 阿里云轻量应用服务器 | 现场查官网 | 套餐化、中文控制台 | 首购与续费可能不同 | 中国大陆用户、小白 | [产品页](https://www.aliyun.com/product/swas) | [计费说明](https://help.aliyun.com/zh/simple-application-server/product-overview/billable-items) |
| 腾讯云轻量应用服务器 | 现场查官网 | 套餐化、入门规格清晰 | 地区、流量和促销资格需核对 | 中国大陆用户、小白 | [产品页](https://cloud.tencent.com/product/lighthouse) | [计费说明](https://cloud.tencent.com/document/product/1207/73452/) / [购买页](https://buy.cloud.tencent.com/lighthouse) |
| 华为云 Flexus L | 现场查官网 | 轻量套餐、可视化控制台 | 免费试用有资格和库存限制 | 已有华为云账号的用户 | [产品页](https://www.huaweicloud.com/product/flexus-l.html) | [价格计算器](https://www.huaweicloud.com/pricing.html) |

表格里的价格必须在当前会话重新读取官方页面后填写。下单前给用户一张完整订单核对卡：厂商、地区、受支持 Linux 镜像、架构、CPU/内存、系统盘、购买时长、活动资格、首购价、续费规则、退款规则、出站 HTTPS、管理入口，以及“无需新增公网入站端口”。任一字段未知就停在结算页前，不付款。官方产品页只显示“点击购买获取最新价格”时，如实写“购买页实时结算，公开页无固定最低价”；不得用开发者社区文章、广告或搜索摘要冒充官方结算价。试用价、秒杀价、新用户价、正常刊例价和续费价分开写。

对 Hermes 轻量常驻，优先比较 Linux 2 核 2 GB 及以上候选规格；这只是入门候选，不是性能保证，最终以云端 `hermes -p <Profile> doctor`、真实微信往返和重启恢复测试为准。

完成对比后只给一个推荐。若用户重视省钱且尚未要求全天在线，推荐继续本地；若明确要求 24 小时在线，通常在阿里云与腾讯云中选择当日总成本更低、续费规则更清楚的一家。说明主要取舍，然后才问用户选哪一个。用户已有可用服务器时不展示购买比较，直接推荐复用并先验证现有服务器。

向小白说明时固定展示两条入口，不把两种情况混在一起：

> **有服务器**：先复用安全连接；若没有现成连接，再收集服务器地址或域名、SSH 端口、登录用户名和认证方式。密码、私钥和验证码不发给 Agent，只在终端隐藏输入、当前工具原生秘密输入框或云平台官方登录框中录入。连接成功后由 Agent 自动检测系统和剩余资源，再说明改动并部署。
>
> **没有服务器**：先展示本地电脑、阿里云、腾讯云和华为云的当日官方价格、配置、续费规则及购买链接并给出一个推荐。用户本人购买和付款；实例创建完成后，回到上面的“有服务器”安全连接流程，仍不把密码发进聊天。


**微信单连警告：同一个 Weixin token 只允许一个轮询实例。迁移到云端时，本地与云端不能同时使用同一 token；必须在受控切换点停止旧实例。第一步就告知用户。**


服务器登录密码与模型 API key 使用同一安全输入原则：优先复用 SSH Agent、系统密钥链、已登录的云平台网页终端或当前工具原生秘密输入框；其次使用真实终端的隐藏密码提示。任何方式都不得把秘密拼进命令参数、工具参数、脚本、聊天或普通文档。当前环境没有可确认安全的输入通道时暂停，帮助用户打开云平台官方网页终端或配置 SSH 密钥，不能为了继续流程降低安全标准。

## 2. 用户已有任意服务器

用户说“我有服务器”时，云厂商名称不是前置条件。先明确纠正概念：**不是安装服务器，也不是再买一台服务器，而是把 Hermes 和这位微信助手部署到用户已经拥有的服务器上。** 已跳过购买，只判断现有服务器是否适合安全运行 Hermes。

第一条回复必须使用这个结构，不能直接问系统版本：

> 当前进度：[第 1/5 步] 选择运行位置｜检测到你已有服务器，云端路线已跳过购买。
>
> 接下来不是“安装服务器”，而是在你已有的服务器上部署 Hermes。新用户流程是：安全连接 → 自动检测环境 → 展示改动并确认 → 直接在云端配置 Hermes、模型、微信和人格 → 启动与重启实测。只有已经存在本地助手时才增加“受控切换”。现在先检查有没有可复用的安全连接，你不用提供密码或密钥。

先检测当前工具是否已经具有用户授权的 SSH 会话、SSH 别名或远程执行能力，不读取 SSH 私钥内容，不扫描主目录、局域网或公网。没有现成连接时，一次只问一个问题：

1. 首先自动检测当前 Agent 是否已有用户授权的远程连接。已有连接时直接做只读预检，不让用户回答本可自动识别的系统版本。
2. 没有现成连接时，先问：“你现在通常怎样打开这台服务器？”并给出三个中文选项：`云服务商网页终端（小白推荐）`、`电脑上已有 SSH 连接或别名`、`不知道`。不能只丢出“SSH”这个术语，也不能先问 IP、系统版本或云厂商。
3. 选择网页终端时，先帮助用户打开原服务器管理平台的“远程登录/网页终端”，不要求安装新终端；当前 Agent 能控制该页面时直接执行只读预检，不能控制时只让用户在网页终端完成一次必要操作，并逐屏解释。
4. 选择已有 SSH 时，优先复用用户已经配置好的 SSH 别名或当前安全连接。没有可复用连接时，建立连接需要四类信息：`服务器公网地址或域名`、`SSH 端口（常见默认值为 22）`、`登录用户名`、`认证方式（密钥、密码或云平台临时登录）`。这是连接资料，不是“服务器版本”；系统与版本在连接成功后自动检测。
5. 服务器地址、端口和用户名不是登录密码，但属于用户的基础设施信息。优先让用户在系统 SSH 配置、宿主应用的本地连接界面或云平台网页终端中录入，避免进入聊天记录；当前工具确实只有聊天入口时，先说明暴露范围并取得同意后才接收地址、端口和用户名。密码、私钥和验证码不发给 Agent，Cookie 和完整带秘密的连接串也永远不得发到聊天框或写入普通文档。
6. 用户使用密钥认证时，只选择本机已经存在的私钥文件或由系统密钥链/SSH Agent 提供，不读取、不复制、不上传私钥内容；使用密码时只在终端隐藏输入或云平台官方登录框中录入。若宿主工具提供经过确认的原生秘密输入框，也可以使用，但不得让密码进入对话记录、工具参数或日志。
7. 用户不知道时，先引导其在购买服务器的网站或管理后台找到“实例详情”和“远程登录/连接”按钮；实例详情通常能看到公网地址，远程登录页能提供推荐的用户名和认证方式。仍找不到就暂停，不猜入口、不扫描资产，也不自动跳回购买流程。
8. 只有建立安全连接后才自动识别系统与版本并运行只读预检；连接失败就停止并解释，不自动购买新服务器。

只读预检由 Agent 执行：

```bash
uname -srm
cat /etc/os-release
id
nproc
free -h
df -h /
command -v systemctl loginctl curl git xz
```

Linux 安装器还需要 `xz`（Debian/Ubuntu 包名通常为 `xz-utils`），因此实际预检必须是：

```bash
command -v systemctl loginctl curl git xz
test -d /run/systemd/system
systemctl is-system-running
```

`command -v systemctl` 只证明命令存在。`/run/systemd/system` 必须存在，`systemctl is-system-running` 必须表明 systemd 正在运行（`running` 或经核验不阻断服务管理的 `degraded`）。用 `id -u` 选择管理员分支：当前已经是 root 时，root 不要求安装 sudo，管理员动作直接运行；当前是非 root 管理员时才要求 `command -v sudo` 并由用户在终端完成授权。两种分支都只把管理员权限用于创建服务账号、系统依赖和启用 linger，不能用 root/SUDO_USER 运行 Hermes。

此处只做管理员只读基础预检，不能运行尚不存在的发布包脚本，也不能假装服务账号、linger 或模型主机已经就绪。用户确认部署后，必须按第 4 节顺序先创建服务账号并启用 linger、传输并校验发布包、选择模型官方主机，再从服务账号登录会话运行包内 `check_cloud_preflight.py`；任一前提缺失都停止。

时区核验：`date +%Z`（或 `timedatectl`）读取服务器时区。新购云服务器常默认 UTC，与用户本地时区不一致时，助手报时、早安简报和所有定时推送都会按错误时区运行。向用户说明影响，得到同意后再按管理员分支调整：root 直接运行 `timedatectl set-timezone Asia/Shanghai`，非 root 管理员才使用 `sudo timedatectl set-timezone Asia/Shanghai`；不擅自修改。

向用户只展示系统、架构、CPU、内存、可用磁盘、网络是否可用、当前账号是否为 root、管理员分支是否可用、systemd 是否可用，以及“适合进入部署确认/需要先修复/不支持”的中文结论。不得显示 IP、主机名、用户名、SSH 配置或环境变量；基础预检通过不等于服务账号深度预检通过。

通过后推荐复用现有服务器，并说明没有推荐重新购买的原因。用户确认部署后才进入下一节。若服务器承载现有业务，先说明预计改动、端口、磁盘和服务名，避免覆盖已有服务。

实际安装只能在用户确认后的第 4 节进行；不能在基础预检阶段先安装 Hermes。


## 2.5 云端模型凭据

默认不复制本地 API key，也不从 `.env` 提取或传输秘密。复制同一把 key 会扩大泄露面、混合本地与云端账单，并让单独撤销变困难。

安全顺序：

1. 在模型厂商官方控制台为云端创建独立凭据；厂商支持时设置用途标签、费用上限、最小权限和独立撤销。
2. 只在云端 Hermes 官方隐藏输入、宿主的原生秘密输入框或厂商 OAuth 页面录入。
3. 运行最短真实调用验证，再检查日志和对话没有出现秘密。
4. 记录凭据用途和撤销入口，不记录值。

用户明确要求复用旧 key 时，先说明风险；本 Skill 仍不提供读取 `.env`、临时明文文件、`scp` 或命令管道迁移秘密的配方。当前工具没有受保护的端到端秘密迁移能力时，停止并改为云端重新录入。


## 3. 安全前提

- 推荐当前 Hermes 官方支持的 Linux 发行版。
- 初次登录使用终端隐藏输入，不让用户把密码发进聊天。
- 创建非 root 的 Hermes 运行账号，授予最小必要权限。
- 不直接复制整份本机 `.env`。全新云端路线直接在云端完成一次模型认证和微信扫码；已有助手迁移时按当前官方能力重新认证或使用经过验证的安全迁移方式。
- 不在一次操作中同时修改 SSH 端口、关闭密码登录并重启 sshd。
- 做 SSH 加固前，先验证密钥登录、第二条会话和云控制台回退。
- Weixin 使用出站长轮询，不需要 webhook（回调地址）、WebSocket 入站或新公网端口。不得为“让微信连进来”开放安全组、防火墙或路由器入站端口。
- 默认不启用 Hermes Dashboard。确需使用时只绑定回环地址或可信私网并使用当前官方认证；当前只有共享凭据、没有逐用户权限和多因素认证的 Dashboard 不得直接暴露公网。
- 云镜像、整机快照、迁移包和故障包创建前必须审查范围，排除或单独加密 `.env`、auth、Weixin 凭据目录、会话和敏感日志；服务器“私有”不等于备份自动安全。

## 4. 全新云端部署与已有助手迁移

本节所有云端用户服务命令以 `flow-contract.json` 的 `cloud_service.commands` 为唯一事实来源；修改流程时先改契约，再同步本节与 `operation-faq.md`，验证器会强制核对一致性。除单独标出的 `loginctl enable-linger` 管理动作外，所有 Hermes、Profile、模型、微信、检查器和 gateway 命令都必须在非 root 服务账号的真实登录会话中执行；不能让管理员的 HOME、SUDO_USER 或 root 环境代替目标账号解析 Profile。

云端受信终端交接器必须显式接收远端服务账号和账号切换方式，不能从 SSH 别名、当前 HOME 或目录名猜测。root SSH 入口使用 `root-runuser`，非 root 管理员入口使用 `sudo`；只有 SSH 目标明确写成同一个服务账号时才允许 `direct`。三种方式均由交接器校验并把最终 `run-cloud` 绑定到服务账号真实登录身份；参数缺失、服务账号为 root、直接登录用户名不匹配或值含注入字符时失败关闭，不得退回管理员身份执行。

在承载其他内容的已有服务器做小号测试时，任何服务器写入前先用 `scripts/resource_ledger_guard.py` 在用户控制、权限为仅当前用户可访问的受保护本地目录创建机器可校验的**测试资源计划**；父目录不私有时脚本直接拒绝。先把阿里云控制台中唯一实例标签做 SHA-256，再通过已经确认的只读连接取得该机 `host-binding` 哈希；两个哈希、计划中的全新非 root 服务账号 HOME、Profile、全新 Hermes 状态根、user unit、专用工作区和 V0.1 发布目录写入计划。IP、主机名、用户名、路径和哈希只留在受保护记录与目标服务器，不写进 Agent 评测、聊天或公开文档。`create-plan` 必须发生在创建服务账号之前；随后只允许创建计划中的全新服务账号。把计划文件作为该账号 HOME 内的第一份状态文件传入后，用同一份已校验脚本的 `activate-plan` 核对真实 machine-id/root-device 绑定、操作系统账号 HOME、UID、计划路径和 unit 均无冲突。再运行 `check-prewrite`；三项全部 PASS 前不得上传发布包、安装 Hermes、输入凭据或扫码。脚本的 `preview-cleanup` 只预览且没有删除原语；任何删除仍需用户逐项确认。

首次台账门禁通过前不把引导脚本落盘。通过已授权 SSH 的标准输入或等价的受保护临时执行通道运行本地已校验的 `resource_ledger_guard.py`，远端只创建计划规定的 ledger 文件；不使用 shell 历史、聊天粘贴、临时脚本文件或未登记下载路径。不能提供不落盘执行通道时停止，不得为了启动台账先在服务器留下一个台账无法记录和清理的脚本。发布目录已创建并用同一不落盘通道立即 `record-created` 后，才上传 ZIP；发布包逐文件验真后，后续改用其中已记录的脚本。目标冲突、归属未知、服务账号已存在或无法证明全新时停止，不借用既有生产助手的账号、Profile、unit、HOME、凭据或目录。

```bash
python3 <已校验本地Skill绝对路径>/scripts/resource_ledger_guard.py create-plan --ledger <受保护本地计划绝对路径> --service-home <计划中的全新服务账号HOME> --profile <Profile> --hermes-root <本轮专用Hermes根> --workspace <专用工作区> --release-dir <V0.1发布目录> --instance-label-sha256 <控制台实例标签哈希> --host-binding-sha256 <只读主机绑定哈希>
# 以下两条由 Agent 把本地已校验脚本经受保护标准输入交给目标机；“-”不是文件路径
python3 - activate-plan --ledger <服务账号HOME内台账绝对路径>
python3 - check-prewrite --ledger <服务账号HOME内台账绝对路径>
```

1. 固定第 2 节的管理员只读基础预检结果；结果变化或用户尚未确认部署时停止。
2. 用户看完已生成的测试资源计划、预计改动、端口、磁盘和服务名并明确确认后，只创建计划中的全新非 root Hermes 运行账号；已有账号一律不能用于共享服务器小号测试。用 `id -u` 分支启用 linger：root 直接运行 `loginctl enable-linger <非root账号>`；非 root 管理员运行 `sudo loginctl enable-linger <非root账号>`。随后进入该服务账号的真实登录会话，先激活并通过资源台账 `check-prewrite`，再确认 `systemctl --user show-environment` 可用且 `loginctl show-user <非root账号> -p Linger` 为 `Linger=yes`。用户级 manager、linger 或台账不能确认时停止。
3. 把本次 V0.1 的 ZIP、`SHA256SUMS` 和 `FILES.sha256` 通过已授权的 SSH/SFTP 或云平台文件通道传给服务账号；远端先独占创建全新固定发布父目录 `~/.local/share/build-wechat-assistant/v0.1`，立即运行 `resource_ledger_guard.py record-created --resource release_dir` 冻结其 device/inode，目录中此时只能有这三个私有普通文件。随后在父目录内独占创建名称精确为 `skill` 的空私有子目录。不要让 Agent 临时拼接解压命令。通过上文资源台账使用的同一种受保护标准输入通道，把本地已校验的 `scripts/verify_release_package.py` 直接交给远端 `python3 - extract --zip <ZIP绝对路径> --sha256sums <SHA256SUMS绝对路径> --files-manifest <FILES.sha256绝对路径> --target-dir <V0.1发布目录>/skill` 执行，不先把验证器另存为未登记文件。脚本会在任何解压前核对 ZIP 哈希、清单与每个成员哈希，拒绝绝对路径、`..`、反斜杠路径、重复/大小写冲突、符号链接、非普通文件、加密项、异常体积、非空目标和目标范围错位；只在 `skill` 子目录中用独占创建方式不覆盖解压，再逐文件复核。ZIP 与双清单必须留在发布父目录，不能混入最终 Skill 文件树；随后对 `<V0.1发布目录>/skill` 运行完整验证。任一项失败保留现场并停止，不自动清理或换用更宽松解压器。哈希只证明传输内容一致，没有签名或公开 tag 时不得宣传发布者身份已验证。
4. 让用户选择准备使用的模型厂商，只记录该厂商已核验的官方 API 主机名，不输入任何 key。此时发布包、服务账号、linger 和模型主机都已存在，才能在服务账号登录会话运行 `python3 ~/.local/share/build-wechat-assistant/v0.1/scripts/check_cloud_preflight.py --model-host <已选模型官方主机名>`。脚本只输出布尔值，检查 Linux/架构、systemd 用户级 manager、manager 全局环境中不存在任何当前或未来 `WEIXIN_*`、`GATEWAY_ALLOW_ALL_USERS`、模型 `*_API_KEY` / `*_TOKEN` / `*_SECRET` / `*_PASSWORD` 或 `HERMES_SHARED_AUTH_DIR` 回退，另检查 linger、非 root 身份、CPU/内存/磁盘、时区，以及 Hermes 官方站、iLink 和模型主机的出站 TLS；不会输出环境值、用户名、主机名、IP、路径或 DNS 结果。失败时说明“发生了什么 + 下一步怎么做”，不得安装、输入凭据或扫码。
5. 深度预检通过后才按当前官方安装页处理两个分支：需要浏览器自动化时，由管理员一次性安装 Playwright 的 Chromium 系统依赖，再由非 root 服务账号安装 Hermes；不需要浏览器时使用官方 `--skip-browser` 路线，且 Weixin 的仅聊天档保持 `browser` disabled。安装后只运行全局的 `hermes --version`，不要在 Profile 建立前读取 default 的 doctor。启动器通常位于 `~/.local/bin`；必须在服务账号登录 shell 中运行 `command -v hermes`，记录已核验的非空绝对路径，不能假设管理员或 sudo 的 PATH 能找到它。
6. 在全新服务账号中使用台账计划的一个不存在的专用 Hermes 状态根；该根必须是这个服务账号真实 HOME 的直属子目录，不能放进其他账号、系统目录、既有 Hermes 根或软链接父目录。运行 `isolation_guard.py create-root --purpose cloud-service --root <本轮专用Hermes根>` 后立即执行 `resource_ledger_guard.py record-created --resource hermes_root`。`run-cloud` 与 `run-service` 都只接受标记用途为 `cloud-service` 且仍绑定该真实 HOME 的根。独占创建专用工作区后同样立即记录 `workspace`。再由 `run-cloud` 执行 `profile create <Profile> --no-alias --no-skills`，创建成功后立即记录 `profile_dir`。每个目录都必须在写入后续内容前冻结 device/inode；未记录的新出现路径会让部分部署清理失败关闭。不得读取其他账号或真实根的 `profile list` / `gateway list`，不得复用或 clone。随后立即在任何配置写入和模型认证前运行 `check-fresh`；只有路径精确位于新根、Profile 与根级 sessions/memories 均为空、shared 目录和服务账号 HOME 的已知模型认证存储没有旧凭据、auth 不存在、`.env` 私有且没有非空秘密、持久服务定义不存在且没有手工 gateway 进程才继续。接着用 `run-checker --checker apply_chat_safety_baseline.py` 绑定同一 Profile、Hermes、expected root 和已记录工作区，动态把 CLI 与 Weixin 都精确收缩为只启用 `clarify`；再用 `run-checker --checker check_pre_qr_safety.py` 复验。云端 checker 会按 `cloud-service` 根自动保留同一个服务账号 HOME。两项 PASS 前不得认证模型。配置、模型和扫码命令由 `isolation_guard.py run-cloud --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- ...` 启动；它清除继承秘密但保留最终 systemd gateway 使用的真实 HOME，避免 Qwen `~/.qwen` 等 HOME 级 OAuth 在认证和运行时错位。安装、启动、停止、重启、状态和卸载用户服务则只能用同脚本的 `run-service`。两种通道都必须显式带同一 `-p <Profile>`，不得混用；普通 `run` 只接受本地 `local-test` 根，在云端直接失败。全新云端路线直接在此 Profile 配置。共享服务器小号测试不得走“已有助手迁移”。
7. 打开任何模型登录、OAuth 或 API key 录入前，先运行资源台账 `mark-authorization --kind model --state creation_started`，再从当前 `auth add --help` 与官方资料锁定唯一 provider 和认证类型。禁止运行可能自动进入 Nous Portal 等未选择厂商的通用 `model` 向导。Agent 在用户电脑上调用 `launch_trusted_handoff.py --mode cloud`，同时传入已核验的远端服务账号与 `root-runuser` / `sudo` / `direct` 之一。OAuth 使用明确的 `auth add <provider> --type oauth` 并打开受信窗口；API key 使用 `auth add <provider> --type api_key --label <Profile>`，但交接器先建立最终以该服务账号执行 `run-cloud` 的真实 TTY并观察到掩码提示，才打开宿主原生隐藏框。用户只粘贴一次 Key，不输入 SSH、路径、启动命令或标签。禁止先收集 Key 再管道到 SSH；交接器不回传原始命令、路径或子进程输出，只在未检测到回显且存在保存回执时返回 `SAVED`。提示缺失、输出超限、回显、取消或保存回执缺失都失败关闭。不能代开时先解释原因，用户明确继续后才允许一条原子命令回退。`run-cloud` 不继承登录前的模型密钥或共享 OAuth，但保留新服务账号真实 HOME，并机械检查三路 TTY；非 TTY 返回 `trusted_tty_required`。Qwen 旧 OAuth 规则继续按当前官方资料失败关闭。认证后把授权状态转为 `created`，重跑安全门禁，再由 Agent 非交互写入并验证明确的 provider 与模型；若边界漂移则重新应用基线。模型中文探测成功后必须通过 `run-checker` 再运行一次 `apply_chat_safety_baseline.py`，只收紧本轮 Profile 内新建的运行时缓存，然后再运行 `check_pre_qr_safety.py`；两项 PASS 才进入微信扫码。凭据来源或权限不能证明时停止。认证中断必须由用户在官方控制台确认结果后闭合台账，不能自动当作未创建。
8. 打开二维码前第三次运行同一安全门禁；只有 CLI 与 Weixin 精确只启用 `clarify`、没有 MCP、记忆与推理注入关闭、专用工作区私有且 gateway 停止时才继续。先标记 Weixin 授权开始，再由 Agent 用 `launch_trusted_handoff.py --mode cloud --kind weixin-setup` 主动打开绑定 `run-cloud` 与 `setup_weixin_direct.py` 的临时 SSH 二维码窗口。用户不输入命令、不选择平台或权限，只扫码并在手机确认。runner 在二维码生成前机械拒绝非 TTY，交接器不回传二维码、URL 或输出；直达助手自动写入主人 allowlist、关闭群聊并保持服务未安装、未启动。若出现全平台菜单，Agent 终止当前精确交互进程并重走直达助手，不能让用户选择 `Done` 或按键补救。扫码后保持 gateway 停止并运行启动前检查，只有许可名单、home、allow-all、官方端点、配置覆盖、敏感存储权限和停止状态全部通过才把授权标记为 `created`。中断或取消必须由用户在微信官方界面确认结果，不能把未知当未创建。
9. 以非 root 账号确认 `run-cloud` 中的 `-p <Profile> --version`、`-p <Profile> doctor` 和登录 shell 的 `command -v hermes` 均成功，记录非秘密的 Hermes 绝对路径。需要浏览器时验证 Playwright 依赖；不需要时确认使用 `--skip-browser` 且 Weixin 浏览器工具关闭。再通过真实 HOME 服务通道查看当前安装参数：

```bash
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway install --help
```

10. 云端统一使用服务账号自己的 systemd 用户级服务并由 linger 保持注销后运行；`<HERMES绝对路径>` 必须替换为该服务账号登录 shell 实际解析的非空绝对路径。linger 已在第 2 步启用，此处只复核：

```bash
loginctl show-user <非root账号> -p Linger
```

输出必须确认 `Linger=yes`。回到服务账号的真实登录会话，先安装但不启动：

```bash
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway install --no-start-now --no-start-on-login
```

安装后必须确认用户服务 stopped + disabled，并读取 Hermes 生成的服务定义和 `systemctl --user show` 的有效属性，验证 `HERMES_HOME` 指向该账号的 `<Profile>` 目录、`ExecStart` 精确包含同一个 `--profile <Profile> gateway run`。这是 unit 内部调用 `hermes_cli.main` 的长参数，不得机械替换成用户命令里的隐藏全局 `-p`。用户级 unit 不应写 `User=`；实际身份由登录账号决定。随后由服务账号运行以下版本化防线；这是唯一允许的 systemd drop-in 修改，不手写 unit 或复制网上片段：

```bash
python3 <V0.1发布目录>/scripts/systemd_env_guard.py install --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根>
python3 <V0.1发布目录>/scripts/systemd_env_guard.py check-prestart --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根> --expect-enabled disabled
```

脚本拒绝 systemd user manager、unit `Environment=`、任意 `EnvironmentFile=` 或初始进程环境中的 Weixin 覆盖、模型 `*_API_KEY` / `*_TOKEN` / `*_SECRET` / `*_PASSWORD`、`HERMES_SHARED_AUTH_DIR`、`CODEX_HOME`、AWS/Google 凭据文件和 XDG 配置目录回退；manager、unit 的可选 HOME 覆盖与实际进程的 `HOME` 都必须精确等于操作系统账号数据库中的服务账号 HOME。它为当前 Hermes 已知的微信与模型秘密键写入明确 `UnsetEnvironment=`。它只输出布尔值，不输出环境内容；已存在不同 drop-in、unit 不是当前账号所有的普通文件、daemon-reload 失败或任一布尔项失败时停止且不覆盖。`check-prestart` 必须在每次 start、restart 和持久化转换前重跑，不能只在初装时跑一次；它还扫描同一服务 UID 的所有 `/proc/*/cmdline`，启动前必须得到 `same_service_user_has_no_gateway_process=true`。

drop-in 安装并核验后，运行 `resource_ledger_guard.py seal-deployed --ledger <服务账号HOME内台账绝对路径>`；它先重验 host/UID/操作系统账号 HOME 与计划状态，再把 Hermes 根、Profile、工作区和发布目录的 device/inode 与已加载 unit 冻结到台账。绑定漂移、任一目录不是当前账号私有普通目录或 unit 未加载都在写入封存状态前停止。此后清理前若路径被替换，即使文字路径相同也失败关闭。

11. 写入并核验人格、再次通过 `check-prestart` 后分路线启动：
   - **全新云端路线**：本地没有待切换的助手，执行 flow contract 中的 `run-service ... -- -p <Profile> gateway start`；不得制造一个虚假的“停止本地网关”步骤。
   - **已有助手迁移**：先准备云端但保持未启动，核验配置与人工回退；告诉用户将有一次短暂中断；先停止本机 gateway 并确认已停止，再启动云端服务。任何时刻都不能让本地和云端使用同一微信凭据同时轮询。
启动完成后立即运行 `python3 <V0.1发布目录>/scripts/systemd_env_guard.py check-runtime --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根> --expect-enabled disabled`。它只输出布尔值：核对暂存服务此时必须 active + disabled、unit 的有效 `ExecStart`、Profile、`HERMES_HOME`，再读取 MainPID、`/proc/<MainPID>/cmdline` 与 `/proc/<MainPID>/environ`，确认实际进程仍绑定同一个 Profile、没有从 manager/unit 继承微信或模型秘密，并且 `target_is_only_gateway_for_service_user=true`。发现同 UID 第二个前台 gateway、其他 unit 或无法完整扫描时失败；失败后立即通过 `run-service` 停止同一 Profile 服务并标记当前不可用。它不能观察其他系统账号或其他主机，因此测试微信身份还必须是本轮新授权且人工确认未在别处轮询。这个运行时检查不读取 Profile `.env` 的秘密值，也不能替代真实微信往返。

12. 在同一服务账号登录会话使用统一的用户服务命令管理云端。已停止服务再次 start 前运行 `check-prestart`，它要求 inactive；正在运行的服务 restart 前运行 `check-prerestart`，它要求 active。两者都必须用 `--expect-enabled enabled|disabled` 声明此阶段真实自启状态，命令成功后立即以相同预期执行 `check-runtime`。初次暂存验收用 `disabled`，完成第 13 步持久化后用 `enabled`；预期与事实不一致即停止：

```bash
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway start
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway stop
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway restart
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway status --deep
```

持久化后的普通重启顺序示例：带 `--expected-hermes-root <本轮专用Hermes根>` 的 `check-prerestart ... --expect-enabled enabled` → flow contract 中的 `run-service ... gateway restart` → 带同一 expected root 的 `check-runtime ... --expect-enabled enabled`。不得用 prestart 检查一个仍 active 的服务，也不得把 enabled/disabled 状态留给脚本猜测。

在新的 Weixin 会话完成身份、真实任务、扫码后执行边界和访问控制配置证据验证。全新云端路线失败时停止云端并修复；迁移路线失败时必须先停止云端并确认停止，再恢复并验证本机 gateway，不得让两端同时作为回退运行。
13. 云端收发和稳定性验收通过后，先停止暂存服务并确认停止。强制重写为 enabled，但用 `--no-start-now` 保持 stopped；重写成功后必须检查这个新 unit，再显式启动：

```bash
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway install --force --no-start-now --start-on-login
python3 <V0.1发布目录>/scripts/systemd_env_guard.py check-prestart --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根> --expect-enabled enabled
python3 <V0.1发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway start
python3 <V0.1发布目录>/scripts/systemd_env_guard.py check-runtime --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根> --expect-enabled enabled
```

不得使用 `--start-now` 合并这四步，否则检查到的是重写前旧 unit 或启动后的既成进程。转换后再次检查 unit 的 HERMES_HOME、Profile、有效环境、linger 与 enabled/active 状态。重启前后各读取一次 `/proc/sys/kernel/random/boot_id`，只记录“是否改变”这一布尔值，不把标识符带出服务器；只有值确实改变，才能算真实重启。重启后先重跑 `check-runtime`，再从服务账号登录会话运行同一 Profile 的深度状态并完成新的微信真实任务。迁移路线全部通过后保持本机 gateway 停止，但保留本机配置以便人工回退。

## 5. 云端知识库

本机 Obsidian 路径不会自动出现在云服务器。要在云端访问知识库，必须选择并验证同步方案，例如用户已有的 Obsidian Sync、受控 Git 仓库或其他端到端同步；不得假设 `scp` 一次后会持续同步。

飞书等在线知识库可在云端重新连接 MCP/OAuth，但权限必须保持最小化。

## 6. 验收与回退

### 自动化模拟验收

用户要求测试 Skill、判断能否开源，或本文件发生流程修改时，先运行：

```bash
python3 scripts/test_cloud_flows.py
```

脚本使用 `assets/cloud-flow-fixtures.json` 中的假订单、假报价和 RFC 5737 保留 IP，覆盖阿里云、腾讯云、华为云及“已有服务器跳过购买”四条路径。它不得访问厂商 API、不得连接 SSH、不得使用真实凭据或产生真实订单。

模拟通过只证明以下流程规则在夹具中完整：安装前路线选择、购买页信息、用户本人付款边界、服务器只读预检、非 root 运行、全新云端只配置一次、迁移重新认证边界、用户服务 + linger、模拟重启状态、模拟微信往返状态、单实例轮询及回退顺序。它不证明厂商当前页面没有变化，不证明真实服务器已经部署成功，也不能满足“持久运行已验证”或“开机恢复已验证”；真实使用时仍需重新读取官网并完成真实重启与微信往返。

通过标准：

- 重启服务器后 gateway 能自动恢复。
- 微信私聊真实往返成功。
- 全新云端路线没有重复的本地实例；迁移路线的本机 gateway 已停止，云端无重复实例。
- 服务日志与磁盘没有明显异常。
- 用户知道如何暂停服务和查看费用。

全新云端路线失败时只回退新云端服务；迁移路线失败时保持本机方案可恢复，不删除本机配置。不要为了“必须上线”而降低 SSH、凭据或微信安全边界。

### 小号测试结束后的精确清理

先重新读取并机器核验测试资源台账。按顺序：停止并核验测试 gateway → 用同一 Profile 卸载测试 unit → 对 `created` 的微信与模型授权分别在官方界面撤销，再用 `mark-authorization ... revoked_user_confirmed` 记录“用户已确认撤销”（这不是提供商回执）；`creation_started` 必须由用户核对官方界面后转成 `created` 或 `absent_user_confirmed`，从未开始的授权才保持 `not_created` → 运行 `resource_ledger_guard.py preview-cleanup --ledger <台账>`。只有 host/UID/操作系统账号 HOME 绑定、已记录路径的 device/inode 未漂移、未记录路径仍不存在、unit 明确已卸载且明确为 inactive、同一服务账号 `/proc` 全量扫描完成且没有任何 gateway、所有授权结果均已闭合，脚本才 PASS。这样即使部署中途失败也只能清理此前已立即记录的目录；未知状态不是 stopped，脚本不会删除任何文件。

取得用户对预览中每一项的明确确认后，管理员才可使用精确、无 glob 的目标逐项处理本轮新建的发布目录、专用工作区、专用 Hermes 根和全新服务账号相关资源；不能用宽泛递归命令，也不能碰既有生产助手、其他 Profile、其他账号或整个服务器的 Hermes 根。服务账号和 linger 只有在台账证明本轮新建且管理员再次确认没有其他 user service 时才可处理。任一目标被锁定、清理失败、类型/归属变化或撤权状态不明确时停止，不换更强删除方式。删除资源目录后、删除服务账号前运行 `resource_ledger_guard.py verify-cleanup --ledger <台账>`，逐项证明精确路径不存在、unit 仍未安装且撤销确认仍在；最后由管理员单独处理全新服务账号/linger，并把无法机器证明的外部撤权或剩余项记为未清理。这才可写“测试内容已清理”。

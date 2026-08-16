# 运行 FAQ

> 助手装好后你可能想知道的事。

下列命令由 Agent 根据安装时保存的非秘密台账和当前实际状态生成并执行；必须先把所有 `<...>` 占位符替换为已核验值，不能把模板直接交给小白。普通本地已授权助手使用带同一 Profile 的 Hermes 命令；云端使用 `run-cloud` 做交互配置、使用 `run-service` 管理 gateway 生命周期。受保护验收不把 FAQ 当成读取或操作现有助手的授权。

## 怎么知道助手在不在？

先通过已授权的本地终端或云端安全连接检查服务，再用微信真实消息确认：

- 本地：`hermes -p <Profile> gateway status --deep`
- 云端服务账号登录会话：`python3 <V0.4发布目录>/scripts/isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway status --deep`

只看服务状态不等于微信链路可用；只看微信回复也不能证明重启恢复正常。

## 怎么关掉 / 再打开？

- 本地：`hermes -p <Profile> gateway stop` / `hermes -p <Profile> gateway start`
- 云端服务账号登录会话：在部署时已授权的安全连接中，使用 `isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway stop|start`；必须分别执行明确的 `stop` 或 `start`，不能把竖线照抄进命令。

云端重启同样使用 `isolation_guard.py run-service ... -- -p <Profile> gateway restart`。命令必须在非 root 服务账号真实登录会话执行；管理员权限只用于系统依赖、服务账号与 `loginctl enable-linger`：root 直接执行，非 root 管理员才使用 sudo。发布目录、专用根、启动器和 Profile 四个占位符必须来自部署台账与实际核验结果，不能照抄。持久化服务重新 start 前先运行当前发布包的 `check-prestart --profile <Profile> --hermes <HERMES绝对路径> --expected-hermes-root <本轮专用Hermes根> --expect-enabled enabled`；仍在运行的服务 restart 前改用 `check-prerestart` 和同样参数。成功后立即运行带同一 expected root 的 `check-runtime`，防止 active/enabled 状态、systemd manager 环境或错误 unit/进程绕过门禁。

## 怎么安全换模型？

先记录旧路由，不覆盖旧凭据；在目标环境的隔离会话测试新模型后，再用主人 Weixin 会话的 `/model <新模型> --session` 做会话级切换。真实微信任务通过后才持久化。失败就恢复旧路由。不承诺绝对零中断，也不把 fallback 配好等同于故障切换已验证。

## 怎么撤销一个知识库？

先停止访问，再移除实际的路径、同步共享或 OAuth 授权，重载受影响组件并创建新会话。最后读取原目标必须被拒绝。撤权不会自动删除旧会话、日志、同步缓存或提供商已有记录；这些要按对应系统另行删除。

## 微信扫码过期了怎么办？

只有深度状态、日志或当前官方文档表明登录会话已过期时，才重新运行本 Skill 的 `setup_weixin_direct.py` 扫码流程；不得让用户进入通用 `gateway setup` 菜单。先备份非秘密配置并说明重新扫码可能更新账号凭据；不承诺固定过期周期或其他配置完全不受影响。

## 要花多少钱？

不提供脱离用量的固定月费。现场核对并分别列出：模型输入/输出/缓存计价、视觉或工具费、定时任务频率、云服务器首购与续费、地区与税费。只有拿到本次真实用量后才做区间估算，并写明核验日期和计费单位。

## 怎么省 token？

- 聊得太久记得说“**开个新会话**”——历史越长，每次消耗通常越多
- 不需要识图时只用便宜主模型聊天

## 怎么卸载？

1. 本地运行 `hermes -p <Profile> gateway stop`；云端在服务账号登录会话运行 `isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway stop`
2. 本地运行 `hermes -p <Profile> gateway uninstall`；云端先用同一 `run-service` 通道执行 `-p <Profile> gateway uninstall --help` 核对当前参数，再执行 `-p <Profile> gateway uninstall`，未核实前不猜命令
3. 先让用户逐项选择要保留的非秘密文件，只导出已审查的目标人格或配置；记忆、会话、`.env`、auth 和整个 Profile 默认不导出，不要把删除整个 `~/.hermes/` 当作普通卸载步骤
4. 用户确认不再使用后，在微信官方界面取消对应 iLink 授权

如果这是承载其他业务服务器上的小号测试，普通“卸载”不等于“测试内容已清理”。必须回到部署前由 `resource_ledger_guard.py` 创建并激活、并在每个目录独占创建后立即执行 `record-created` 的测试资源台账：先停止并卸载精确 unit；`created` 的测试模型与 Weixin 授权要在官方界面撤销并记录用户确认，`creation_started` 必须人工核对后闭合成 `created` 或 `absent_user_confirmed`，只有从未进入授权流程的项目保持 `not_created`；再运行只读 `preview-cleanup`。只有 host/UID/操作系统账号 HOME、已记录 device/inode 未漂移、未记录路径仍不存在、unit 明确 inactive 且已卸载、同一服务账号 `/proc` 扫描完整且没有任何 gateway、授权状态匹配，才允许向用户展示逐项清理预览；未知状态一律失败，脚本本身绝不删除。用户逐项确认后才处理本轮新建的精确 Profile/专用 Hermes 根/工作区/发布目录/日志/缓存/会话，禁止 glob、禁止碰既有生产助手、其他 Profile、其他账号或服务器上的其他 Hermes 根。清理后、删除全新服务账号前运行 `verify-cleanup`；服务账号和 linger 只有在台账证明本轮计划要求新建且没有其他 user service 时才可处理。失败、文件锁定、归属或 inode 变化、撤权状态未知时停止，不扩大范围或换更强删除方式，剩余项明确登记未清理。

## 换服务器怎么迁移？

**不要复制整份 `.env`、auth、微信 token、会话或全量备份。**正确顺序：

1. 在新服务器安装并验证 Hermes，创建独立状态 Profile；它不自动保证凭据隔离。
2. 只迁移用户确认的非秘密文件；复制前检查 `SOUL.md` 等文件是否真的不含秘密。
3. 在模型厂商控制台为新服务器创建独立、可撤销的凭据，并通过云端隐藏输入录入；不从旧 `.env` 提取或传输 API key。
4. 按当前 Hermes 官方能力重新完成或安全恢复 Weixin 认证；不能凭记忆断言 token 可搬或一定不可搬。
5. 云端人格、服务和回退均准备好后，在受控切换点停止旧 gateway，再启动新 gateway；同一个 Weixin token 不能有两个轮询实例。
6. 真实往返、访问控制和重启恢复全部通过后才结束迁移；失败时先停止新实例，再恢复旧实例。

## 出问题了看哪里？

- `hermes -p <Profile> doctor` — 健康检查
- `hermes -p <Profile> gateway status --deep` — 本地网关详情
- `hermes -p <Profile> logs gateway -n 100` — 跨平台查看最近网关日志
- 云端服务账号登录会话仍使用 `isolation_guard.py run-service --root <本轮专用Hermes根> --hermes <HERMES绝对路径> -- -p <Profile> gateway status --deep`；只摘取与故障相关且已脱敏的日志行

# 参与贡献

感谢你改进 `build-wechat-assistant`。这个项目面向不懂技术的普通用户，首要原则是：流程必须真实可执行，失败时不破坏现有助手，也不把模拟结果说成真实成功。

## 开始前

1. 先阅读 `SKILL.md` 的交互铁律、停止规则和按步骤读取说明。
2. 只修改与问题直接相关的文件；不要顺手重构安全执行器。
3. 不提交任何 API key、token、二维码、Cookie、`.env`、会话、记忆、日志、真实用户 ID、私人文档或个人绝对路径。
4. 不向 Skill 文件树（`SKILL.md`、`scripts/`、`references/`、`assets/`、`agents/`）添加 README、CHANGELOG 或发布材料；这些属于仓库根或 `.github/`。

## 本地验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_skill.py .
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
```

发布维护者还必须使用本次声明支持的干净 Hermes 启动器、其 MCP Python 和目标环境中的真实 Node.js：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_skill.py . \
  --hermes <干净Hermes启动器绝对路径> \
  --mcp-python <Hermes环境中的Python绝对路径> \
  --node <Node.js真实绝对路径>
```

不带 `--hermes` 的 CI 只证明结构与隔离逻辑，不代表模型、二维码、微信送达、飞书写入、真实 Codex 或系统服务通过。

## Pull Request 要求

- 说明用户遇到的真实问题和最小修复。
- 列出实际运行的测试、成功输出和未验证层。
- 新能力至少覆盖一个成功流程、一个错误流程和一个关键边界。
- 涉及权限或外部写入时，说明授权点、最小范围、回滚和失败恢复。
- 文案要用普通中文说明“正在做什么、为什么、用户只做什么、成功后看到什么”。

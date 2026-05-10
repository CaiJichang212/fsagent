# AGENTS.md

本文件适用于 `harnessagents/fsagent/` 目录及其子目录。后续自动化编码代理在本项目内工作时，应优先遵守这些约束。

## 语言与沟通

- 默认使用中文回答问题。
- 说明变更时保持简洁，优先给出已完成的文件、验证命令和未完成风险。
- 遇到用户未明确要求的重构、格式化大改或破坏性操作时，先说明原因并征得确认。

## 项目定位

- 本项目是 Deep Agents 的 Fast/Plan 双模式运行时扩展。
- 代码独立放在 `harnessagents/fsagent/`，不要为了本项目需求直接修改 Deep Agents 原 SDK 源码，除非用户明确要求。
- 后端入口包括：
  - API：`fsagent.api.server:main`
  - 开发启动器：`fsagent.dev:main`
- 前端位于 `frontend/`，使用 Vite、React 和 TypeScript。

## 目录约定

- `fsagent/api/`：FastAPI schema、server 和 session service。
- `fsagent/runtime/`：Fast/Plan runtime、planner、executor、MCP、模型配置和状态定义。
- `fsagent/tests/`：Python 单元测试。
- `frontend/src/`：前端应用代码。
- `frontend/tests/`：前端脚本级测试。
- `scripts/start-dev.sh`：一键启动 API 与前端开发服务器。
- `model_config.json`：模型候选项、thinking 能力和采样参数。

## Python 开发

- 需要 Python `>=3.11,<4.0`。
- 使用 `uv` 管理依赖和命令运行。
- 遵守 `pyproject.toml` 中的 Ruff 配置：行宽 120，lint 规则为 `ALL` 加项目例外。
- 新增或修改公共函数时，保持现有类型标注风格。
- 优先沿用现有模块边界；不要把 API、runtime、frontend 的职责混在一起。
- 修改 runtime 行为时，优先补充或更新 `fsagent/tests/` 中对应测试。

常用验证命令：

```bash
uv run --group test pytest fsagent/tests -q
uv run --group test ruff check fsagent
uv run --group test ruff format fsagent --diff
```

如果当前工作目录在 monorepo 根目录，使用：

```bash
uv run --project harnessagents/fsagent --group test pytest harnessagents/fsagent/fsagent/tests -q
```

## 前端开发

- 前端代码位于 `frontend/`。
- 修改前端后，至少运行可用的构建或相关测试。
- 常用命令：

```bash
cd frontend
npm run build
```

- 本地开发优先使用根目录脚本：

```bash
./scripts/start-dev.sh
```

默认地址：

- API：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

## 配置与安全

- 不要提交真实 `.env`、API Key、令牌或本地私密配置。
- `.env.example` 只保留示例值。
- 默认模型配置来自 `.env` 和 `model_config.json`。
- MCP 配置可能启动本地进程。只有在用户明确要求或确认信任当前项目配置时，才启用项目 stdio MCP server。
- API 中对应字段为 `trustProjectMcp`。

## 测试策略

- 修复 bug 时，应优先添加能复现问题的测试，再修复实现。
- 修改 API schema、session 状态或 runtime 输出时，检查 `fsagent/tests/test_api.py`、`test_graph.py`、`test_planner.py`、`test_executor.py` 等相关测试。
- 修改开发启动器时，检查 `fsagent/tests/test_dev.py`。
- 修改模型配置逻辑时，检查 `fsagent/tests/test_model_config.py`。
- 如果无法运行完整测试，最终回复中必须说明未运行的命令和原因。

## 文档维护

- 更新行为、命令或端口时，同步检查 `README.md`。
- README 中的命令应同时考虑两种位置：
  - 当前目录为 `harnessagents/fsagent/`
  - 当前目录为 monorepo 根目录
- 文档示例避免包含真实密钥或用户私有路径，除非路径是本项目固定路径说明。

## Git 与工作区

- 工作区可能已有用户改动；不要回滚未由自己创建的改动。
- 不要运行 `git reset --hard`、`git checkout -- <file>`、大范围删除等破坏性命令，除非用户明确要求。
- 提交、分支、推送或 PR 仅在用户明确要求时执行。

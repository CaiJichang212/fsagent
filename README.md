# fsagent

`fsagent` 是 Deep Agents 的 Fast/Plan 双模式运行时扩展，提供命令行、FastAPI 服务和 React 前端，用于在同一套 LangGraph runtime 上运行快速问答或可审核计划执行流程。

## 功能概览

- **Fast 模式**：直接调用 Deep Agents 风格 agent，适合一次性快速任务。
- **Plan 模式**：先生成 todo 计划，等待用户审核后再逐项执行，并输出执行摘要。
- **计划审核**：支持批准、编辑、重试和取消计划。
- **HTTP API + SSE**：前端可通过普通请求或事件流获取 session、timeline、todo 和执行日志。
- **模型配置**：通过 `.env` 和 `model_config.json` 管理模型、thinking 开关和采样参数。
- **MCP 工具加载**：默认安全关闭；只有显式启用并信任项目 MCP 时才会加载 stdio MCP server。

## 目录结构

```text
.
├── fsagent/
│   ├── cli.py                 # CLI 入口：fsagent
│   ├── dev.py                 # API + 前端开发启动器：fsagent-dev
│   ├── api/                   # FastAPI schema、server、session service
│   ├── runtime/               # Fast/Plan runtime、planner、executor、MCP、模型配置
│   └── tests/                 # Python 单元测试
├── frontend/                  # Vite + React + TypeScript 前端
├── scripts/start-dev.sh       # 一键启动后端和前端
├── model_config.json          # 前端可选模型和采样配置
├── pyproject.toml             # Python 包、脚本和测试依赖配置
└── mcp.json                   # MCP server 配置示例/项目配置
```

## 环境要求

- Python `>=3.11,<4.0`
- `uv`
- Node.js 和 npm

本项目位于 monorepo 的 `harnessagents/fsagent/` 目录，`pyproject.toml` 中的 `deepagents` 与 `deepagents-cli` 依赖指向相邻的 `../../libs/` 源码。

## 安装

在项目目录中安装后端依赖：

```bash
uv sync
```

如果当前目录在 monorepo 根目录：

```bash
uv sync --project harnessagents/fsagent
```

安装前端依赖：

```bash
cd frontend
npm install
```

## 配置

复制示例配置并填写真实密钥：

```bash
cp .env.example .env
```

`.env` 支持以下变量：

```bash
MODEL=Qwen/Qwen3.5-27B
BASE_URL=https://api-inference.modelscope.cn/v1
API_KEY=xxx
AVAILABLE_MODELS_JSON=model_config.json
```

说明：

- `MODEL`：默认模型名称，可被 CLI 参数或 API 请求覆盖。
- `BASE_URL`：OpenAI-compatible 模型服务地址。
- `API_KEY`：模型服务密钥；不要提交真实值。
- `AVAILABLE_MODELS_JSON`：模型目录配置，默认读取项目根目录的 `model_config.json`。

## 运行方式

### 一键开发启动

推荐使用根目录脚本同时启动 API 与前端：

```bash
./scripts/start-dev.sh
```

默认地址：

- API：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

可通过启动器参数覆盖地址或跳过检查：

```bash
uv run fsagent-dev --host 127.0.0.1 --api-port 8000 --frontend-port 5173
uv run fsagent-dev --skip-deps
uv run fsagent-dev --auto-kill
```

### 单独启动 API

```bash
uv run fsagent-api
```

等价开发命令：

```bash
uv run uvicorn fsagent.api.server:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

### 单独启动前端

```bash
cd frontend
npm run dev
```

前端 Vite 代理会把 `/api` 转发到 `FSAGENT_API_TARGET`，未设置时默认代理到 `http://127.0.0.1:8000`。

## CLI 使用

CLI 入口为 `fsagent`，消息必须以 `/fast` 或 `/plan` 开头：

```bash
uv run fsagent "/fast 总结当前项目结构"
uv run fsagent "/plan 为这个项目生成测试策略"
```

常用参数：

```bash
uv run fsagent "/fast 你的任务" --model Qwen/Qwen3.5-35B-A3B
uv run fsagent "/fast 你的任务" --env-file .env
uv run fsagent "/plan 你的任务" --mcp-config mcp.json
uv run fsagent "/plan 你的任务" --no-mcp
uv run fsagent "/plan 你的任务" --mcp-config mcp.json --trust-project-mcp
```

如果当前目录在 monorepo 根目录：

```bash
uv run --project harnessagents/fsagent fsagent "/fast 总结当前项目结构"
```

Plan 模式在 CLI 中会输出待审核计划；当前 CLI 尚未实现 resume 审核流程，完整审核体验请使用 API/前端。

## API

FastAPI 应用标题为 `fsagent API`。主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/model-config` | 获取前端模型选择配置 |
| `POST` | `/api/runs` | 创建并执行 Fast/Plan run |
| `POST` | `/api/runs/stream` | 创建 run，并通过 SSE 返回 session 快照 |
| `GET` | `/api/runs/{sessionId}` | 获取已有 session |
| `POST` | `/api/runs/{sessionId}/review` | 审核 Plan run |
| `POST` | `/api/runs/{sessionId}/review/stream` | 审核 Plan run，并通过 SSE 返回增量快照 |

创建 run 的请求示例：

```json
{
  "mode": "plan",
  "message": "为项目补充 README",
  "model": "Qwen/Qwen3.5-27B",
  "thinking": false,
  "mcpEnabled": false,
  "trustProjectMcp": false,
  "mcpConfigPath": "mcp.json"
}
```

Plan 审核请求示例：

```json
{
  "action": "approve"
}
```

可选 `action`：

- `approve`：批准计划并开始执行。
- `edit`：提交修改后的 `todos` 和可选 `planMeta`。
- `retry`：带 `feedback` 重新生成计划。
- `cancel`：取消 session，可带 `reason`。

## Runtime 工作流

```text
用户输入
  ├── Fast 模式 -> fast_runner -> final_response
  └── Plan 模式 -> planner -> plan_review -> executor -> formatter -> final_response
```

Plan 模式的最终报告包含：

- `Result`
- `Execution Summary`
- `Plan Status`
- `Artifacts And Evidence`

## MCP 安全说明

MCP 默认不加载。API 请求中需要设置：

```json
{
  "mcpEnabled": true,
  "mcpConfigPath": "mcp.json"
}
```

对于 stdio MCP server，还必须显式信任项目配置：

```json
{
  "trustProjectMcp": true
}
```

CLI 中对应参数为：

```bash
--mcp-config mcp.json --trust-project-mcp
```

不要在未确认 MCP 配置可信时启用项目 stdio MCP server。

## 日志

后端日志使用 JSON Lines 格式，覆盖 HTTP 请求、session 生命周期、Plan 审核、planner/executor 进度和异常 traceback。可通过环境变量调整日志级别和输出位置：

```bash
FSAGENT_LOG_LEVEL=DEBUG FSAGENT_LOG_FILE=logs/fsagent-api.jsonl ./scripts/start-dev.sh
```

未设置 `FSAGENT_LOG_FILE` 时，日志输出到 stdout。

## 测试与质量检查

在项目目录运行：

```bash
uv run --group test pytest fsagent/tests -q
uv run --group test ruff check fsagent
uv run --group test ruff format fsagent --diff
```

前端构建：

```bash
cd frontend
npm run build
```

如果当前目录在 monorepo 根目录，可使用：

```bash
uv run --project harnessagents/fsagent --group test pytest harnessagents/fsagent/fsagent/tests -q
```

## 开发约定

- 后端公共入口：
  - CLI：`fsagent.cli:main`
  - API：`fsagent.api.server:main`
  - 开发启动器：`fsagent.dev:main`
- Runtime 行为变更优先补充 `fsagent/tests/` 中的对应测试。
- API schema、session 状态或 runtime 输出变化时，重点检查 `test_api.py`、`test_graph.py`、`test_planner.py`、`test_executor.py`。
- CLI 参数或输出变化时，重点检查 `test_cli.py`。
- 开发启动器变化时，重点检查 `test_dev.py`。
- 模型配置逻辑变化时，重点检查 `test_model_config.py`。
- 不要提交真实 `.env`、API Key、令牌或本地私密配置。

# fsagent

`fsagent` 是 Deep Agents 的 Fast/Plan 双模式运行时扩展，提供 FastAPI 服务和 React 前端，用于在同一套 LangGraph runtime 上运行快速问答或可审核计划执行流程。

## 功能概览

- **Fast 模式**：直接调用 Deep Agents 风格 agent，适合一次性快速任务。
- **Plan 模式**：先生成 todo 计划，等待用户审核后再逐项执行，并输出执行摘要。
- **通用审核模型**：计划审核兼容旧 `/review` 端点；工具、偏离计划和 MCP 审核共用 `pendingReview` / `reviews` contract。
- **Session Store**：默认使用内存 store，提供 JSONL store 作为本地恢复适配；服务重启后可查询 snapshot，恢复执行仍需要可用 runtime checkpoint。
- **工具策略**：内置 `dev-default`、`locked-down`、`ci-eval` profile，高风险工具进入审核，未知工具默认拒绝。
- **验证与证据报告**：Todo、执行日志、证据、产物、工具调用和 verification 都有稳定 ID，最终报告包含验证结果或跳过原因。
- **HTTP API + SSE**：前端可通过普通请求或事件流获取 session、timeline、todo、review、tool call、evidence 和执行日志。
- **模型配置**：通过 `.env` 和 `model_config.json` 管理模型、thinking 开关和采样参数。
- **MCP 工具加载**：默认安全关闭；只有显式启用并信任项目 MCP 时才会加载 stdio MCP server。
- **Slash 模式路由**：`/fast ...` 和 `/plan ...` 可在接入层显式选择运行模式，解析逻辑独立在 `fsagent.slash_router`。

## 目录结构

```text
.
├── fsagent/
│   ├── dev.py                 # API + 前端开发启动器：fsagent-dev
│   ├── slash_router.py        # /fast 和 /plan 显式模式路由
│   ├── api/                   # FastAPI schema、server、session service
│   ├── runtime/               # Fast/Plan runtime、planner、executor、MCP、模型配置
│   └── tests/                 # Python 单元测试
├── frontend/                  # Vite + React + TypeScript 前端和脚本级测试
├── docs/                      # 设计说明、执行计划和测试记录
├── scripts/start-dev.sh       # 一键启动后端和前端
├── model_config.json          # 前端可选模型和采样配置
├── pyproject.toml             # Python 包、脚本和测试依赖配置
└── mcp.json                   # 本地 MCP 配置，默认被 .gitignore 忽略
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
FSAGENT_SESSION_STORE_PATH=logs/fsagent-sessions.jsonl
```

说明：

- `MODEL`：默认模型名称，可被 API 请求覆盖。
- `BASE_URL`：OpenAI-compatible 模型服务地址。
- `API_KEY`：模型服务密钥；不要提交真实值。
- `AVAILABLE_MODELS_JSON`：模型目录配置，默认读取项目根目录的 `model_config.json`。
- `FSAGENT_SESSION_STORE_PATH`：可选。设置后 API 使用 JSONL session store 保存 snapshot；未设置时使用内存 store。

日志相关变量：

- `FSAGENT_LOG_LEVEL`：日志级别，未设置时后端默认为 `INFO`；`./scripts/start-dev.sh` 默认设为 `DEBUG`。
- `FSAGENT_LOG_FILE`：可选 JSONL 日志路径；启动脚本默认写入 `logs/fsagent-api.jsonl`。

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

## Slash 模式路由

`fsagent.slash_router.parse_slash_mode` 用于把上层输入显式路由到运行模式：

```text
/fast 快速检查当前改动
/plan 为 README 更新制定并执行计划
```

解析规则：

- 只接受 `/fast ` 或 `/plan ` 前缀，且前缀后必须有正文。
- 返回 `mode` 和去掉前缀后的 `content`。
- 不符合格式时抛出 `ValueError("Please start your request with /fast or /plan.")`。

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
| `POST` | `/api/runs/{sessionId}/reviews/{reviewId}/decision` | 提交通用 review 决策 |
| `POST` | `/api/runs/{sessionId}/reviews/{reviewId}/decision/stream` | 提交通用 review 决策，并通过 SSE 返回增量快照 |

创建 run 的请求示例：

```json
{
  "mode": "plan",
  "message": "为项目补充 README",
  "model": "Qwen/Qwen3.5-27B",
  "thinking": false,
  "mcpEnabled": false,
  "trustProjectMcp": false,
  "mcpConfigPath": "mcp.json",
  "toolPolicyProfile": "dev-default"
}
```

`toolPolicyProfile` 可选值为 `dev-default`、`locked-down`、`ci-eval`。省略或传入未知值时，runtime 会回退到 `dev-default`。

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

通用 review 决策请求示例：

```json
{
  "reviewId": "review-001",
  "action": "approve"
}
```

Plan review 支持 `approve`、`edit`、`retry`、`cancel`；tool review 支持 `approve`、`modify`、`deny`、`cancel`；deviation review 支持 `approve`、`replan`、`cancel`；MCP review 支持 `approve`、`deny`、`cancel`。旧 `/review` 端点仍作为 plan-review shorthand 保留。

## Runtime 工作流

```text
用户输入
  ├── Fast 模式 -> fast_runner -> final_response
  └── Plan 模式 -> planner -> plan_review -> executor -> verifier -> formatter -> final_response
```

Plan 模式的最终报告包含：

- `Result`
- `Execution Summary`
- `Plan Status`
- `Artifacts And Evidence`
- `Evidence`
- `Verification`

`completed` 只应在 verification 通过，或记录了明确的 skipped/manual verification reason 后出现。verification 失败时 session 可进入 `needs_revision`。

## Session 与持久化

API snapshot 包含 `pendingReview`、`reviews`、`toolCalls`、`artifacts`、`evidence` 和 `verification`。未设置 `FSAGENT_SESSION_STORE_PATH` 时，`InMemorySessionStore` 保持默认行为；设置该路径后，API 使用 `JsonlSessionStore` 本地保存和重载 session snapshot。注意：JSONL store 只保存 API snapshot 和 resume metadata，不保存 LangGraph runtime/checkpoint 本体；如果服务重启后缺少 runtime checkpoint，`GET /api/runs/{sessionId}` 仍可返回 snapshot，但 review/resume 会返回 `409 Runtime checkpoint missing for session.`。

## 默认工具策略

默认 profile 为 `dev-default`：

| 工具类型 | 风险 | 默认行为 |
| --- | --- | --- |
| `ls`、`glob`、`grep`、`read_file` | low | 允许并记录摘要 |
| `write_file`、`edit_file` | medium | 仅在 executor 阶段允许 |
| `execute`、`bash`、`shell` | high | 进入 tool review |
| `task` | medium | executor 阶段允许并记录摘要 |
| MCP 工具 | medium/high | MCP 启用后按 metadata 和 profile 处理 |
| Unknown tools | high | 默认拒绝 |

`locked-down` 只直接允许只读工具，其余写入、执行、MCP、subtask 工具需要 review 或被拒绝；`ci-eval` 只允许确定性的只读工具，拒绝有副作用的工具。

## MCP 安全说明

MCP 默认不加载。`mcp.json` 是本地配置文件，默认被 `.gitignore` 忽略，可能包含本地命令、私有 server 或密钥；不要提交包含真实 token 的 MCP 配置。API 请求中需要设置：

```json
{
  "mcpEnabled": true,
  "mcpConfigPath": "mcp.json"
}
```

HTTP、streamable HTTP 和 SSE server 会按配置加载；对于会启动本地进程的 stdio MCP server，还必须显式信任项目配置：

```json
{
  "trustProjectMcp": true
}
```

不要在未确认 MCP 配置可信时启用项目 stdio MCP server。

## 日志

后端日志使用 JSON Lines 格式，覆盖 HTTP 请求、session 生命周期、Fast/Plan agent 模型轮次、工具调用摘要、Plan 审核、planner/executor 进度和异常 traceback。可通过环境变量调整日志级别和输出位置：

```bash
FSAGENT_LOG_LEVEL=DEBUG FSAGENT_LOG_FILE=logs/fsagent-api.jsonl ./scripts/start-dev.sh
```

未设置 `FSAGENT_LOG_FILE` 时，日志输出到 stdout。`./scripts/start-dev.sh` 默认导出 `FSAGENT_LOG_LEVEL=DEBUG` 和 `FSAGENT_LOG_FILE=logs/fsagent-api.jsonl`，调用时显式传入的同名环境变量优先。

细粒度 agent 事件统一使用 `agent.*` 前缀，并通过 `agent_mode` 与 `phase` 区分 Fast、Plan planner 和 Plan executor。Plan 模式中，planner 在审核前只生成计划草案并写入 `write_todos`，不加载普通工具或 MCP 执行工具；用户批准后才由 executor 调用执行工具。例如：

```json
{"event":"agent.tool.completed","agent_mode":"plan","phase":"executor","tool_name":"read_file","duration_ms":18.4,"result_size_chars":2048}
{"event":"agent.tool.completed","agent_mode":"plan","phase":"planner","tool_name":"write_todos","duration_ms":3.1,"result_size_chars":128}
```

默认只记录摘要和元数据，包括工具名、参数 key、输入/输出长度、耗时和错误类型；不会记录完整 prompt、工具参数值或工具结果正文。常用排障命令：

```bash
tail -f logs/fsagent-api.jsonl
rg '"event":"agent.tool.failed"|"event":"agent.model.failed"' logs/fsagent-api.jsonl
rg '"session_id":"<session-id>"' logs/fsagent-api.jsonl
```

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

前端脚本级测试：

```bash
cd frontend
node --test tests/*.test.mjs
npm exec -- playwright test --config playwright.controlled-replay.config.mjs
```

如果当前目录在 monorepo 根目录，可使用：

```bash
uv run --project harnessagents/fsagent --group test pytest harnessagents/fsagent/fsagent/tests -q
```

## 开发约定

- 后端公共入口：
  - API：`fsagent.api.server:main`
  - 开发启动器：`fsagent.dev:main`
- Runtime 行为变更优先补充 `fsagent/tests/` 中的对应测试。
- API schema、session 状态或 runtime 输出变化时，重点检查 `test_api.py`、`test_graph.py`、`test_planner.py`、`test_executor.py`。
- 开发启动器变化时，重点检查 `test_dev.py`。
- 模型配置逻辑变化时，重点检查 `test_model_config.py`。
- Slash 模式路由变化时，重点检查 `test_slash_router.py`。
- 前端 review、timeline、markdown、controlled replay 行为变化时，重点检查 `frontend/tests/` 中对应脚本。
- 不要提交真实 `.env`、API Key、令牌或本地私密配置。

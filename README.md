# fsagent

`fsagent` 是 Deep Agents 的 Fast/Plan 双模式运行时扩展，提供 FastAPI 服务与 React 前端，在同一套 LangGraph runtime 上支持快速问答和可审核的计划执行流程。

## 核心能力

- `fast` 模式：直接执行单次任务，适合快速问答、仓库检查等一次性请求。
- `plan` 模式：先生成 todo 计划，等待用户审核后再执行，并产出最终报告。
- 通用 review 模型：plan、tool、deviation、MCP 共用 `pendingReview` / `reviews` 数据结构。
- 会话快照：支持内存 store 和 JSONL store；服务重启后可恢复 API snapshot。
- 工具策略：内置 `dev-default`、`locked-down`、`ci-eval` 三套 profile。
- 验证记录：Plan 执行可附带验证命令，结果写入 `verification` 并进入最终报告。
- HTTP + SSE：创建 run、审核 review、读取 session 都可走普通请求或流式接口。
- 显式模式路由：`/fast ...` 与 `/plan ...` 由 `fsagent.slash_router` 统一解析。

## 目录结构

```text
.
├── fsagent/
│   ├── api/                   # FastAPI schema、server、session service
│   ├── runtime/               # Fast/Plan runtime、planner、executor、policy、MCP、verification
│   ├── tests/                 # Python 单元测试
│   ├── dev.py                 # API + 前端开发启动器：fsagent-dev
│   └── slash_router.py        # /fast 和 /plan 模式路由
├── frontend/                  # Vite + React + TypeScript 前端
├── docs/                      # 设计说明和测试记录
├── scripts/start-dev.sh       # 一键启动脚本
├── model_config.json          # 前端可选模型目录
├── pyproject.toml             # Python 包、脚本和测试依赖
└── mcp.json                   # 本地 MCP 配置，默认被 .gitignore 忽略
```

## 环境要求

- Python `>=3.11,<4.0`
- `uv`
- Node.js 与 npm

本项目位于 monorepo 的 `harnessagents/fsagent/` 目录，`pyproject.toml` 中的 `deepagents` 与 `deepagents-cli` 依赖指向相邻的 `../../libs/` 源码目录。

## 安装

在 `harnessagents/fsagent/` 目录执行：

```bash
uv sync
cd frontend
npm install
```

如果当前目录在 monorepo 根目录：

```bash
uv sync --project harnessagents/fsagent
cd harnessagents/fsagent/frontend
npm install
```

## 配置

复制示例配置：

```bash
cp .env.example .env
```

常用环境变量：

```bash
MODEL=Qwen/Qwen3.5-27B
BASE_URL=https://api-inference.modelscope.cn/v1
API_KEY=xxx
AVAILABLE_MODELS_JSON=model_config.json
FSAGENT_SESSION_STORE_PATH=logs/fsagent-sessions.jsonl
FSAGENT_CHECKPOINTER_PATH=logs/fsagent-checkpoints.sqlite
FSAGENT_LOG_LEVEL=INFO
FSAGENT_LOG_FILE=logs/fsagent-api.jsonl
```

说明：

- `MODEL`、`BASE_URL`、`API_KEY`：默认模型与 OpenAI-compatible 服务配置，均可被 API 请求覆盖。
- `AVAILABLE_MODELS_JSON`：模型目录配置，默认读取项目根目录的 `model_config.json`。
- `FSAGENT_SESSION_STORE_PATH`：设置后启用 `JsonlSessionStore`；未设置时使用 `InMemorySessionStore`。
- `FSAGENT_CHECKPOINTER_PATH`：当前只作为 checkpoint reference 被记录，不会自动启用完整的 LangGraph 持久化恢复。
- `FSAGENT_LOG_LEVEL`、`FSAGENT_LOG_FILE`：控制 JSONL 日志级别与输出位置。

## 运行

### 一键开发启动

推荐使用根目录脚本：

```bash
./scripts/start-dev.sh
```

该脚本会默认设置：

- `FSAGENT_LOG_LEVEL=DEBUG`
- `FSAGENT_LOG_FILE=logs/fsagent-api.jsonl`
- `uv run fsagent-dev --auto-kill`

默认地址：

- API：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

### 使用启动器

```bash
uv run fsagent-dev --host 127.0.0.1 --api-port 8000 --frontend-port 5173
uv run fsagent-dev --skip-deps
uv run fsagent-dev --auto-kill
```

`fsagent-dev` 会先检查端口占用和前后端依赖，再分别启动：

- API：`uv run uvicorn fsagent.api.server:app --reload --no-access-log`
- Frontend：`npm run dev`

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

前端会把 `/api` 代理到 `FSAGENT_API_TARGET`；未设置时默认代理到 `http://127.0.0.1:8000`。

## Slash 模式路由

`fsagent.slash_router.parse_slash_mode` 只接受带正文的 `/fast ...` 或 `/plan ...`：

```text
/fast 快速检查当前改动
/plan 为 README 更新制定并执行计划
```

不符合格式时会抛出：

```text
ValueError("Please start your request with /fast or /plan.")
```

## API 概览

FastAPI 应用标题为 `fsagent API`，主要接口如下：

| 方法   | 路径                                                       | 说明                                   |
| ------ | ---------------------------------------------------------- | -------------------------------------- |
| `GET`  | `/api/health`                                              | 健康检查                               |
| `GET`  | `/api/model-config`                                        | 读取前端模型目录                       |
| `POST` | `/api/runs`                                                | 创建并执行 Fast/Plan run               |
| `POST` | `/api/runs/stream`                                         | 创建 run，并通过 SSE 推送 session 快照 |
| `GET`  | `/api/runs/{sessionId}`                                    | 获取已有 session                       |
| `POST` | `/api/runs/{sessionId}/review`                             | 处理 plan review                       |
| `POST` | `/api/runs/{sessionId}/review/stream`                      | 流式处理 plan review                   |
| `POST` | `/api/runs/{sessionId}/reviews/{reviewId}/decision`        | 处理通用 review 决策                   |
| `POST` | `/api/runs/{sessionId}/reviews/{reviewId}/decision/stream` | 流式处理通用 review 决策               |

创建 run 示例：

```json
{
  "mode": "plan",
  "message": "为项目补充 README",
  "model": "Qwen/Qwen3.5-27B",
  "thinking": false,
  "mcpEnabled": false,
  "trustProjectMcp": false,
  "mcpConfigPath": "mcp.json",
  "profile": "dev-default",
  "backendProfile": "ephemeral",
  "permissionProfile": "workspace-edit",
  "toolPolicyProfile": "dev-default"
}
```

Plan review 示例：

```json
{
  "action": "approve"
}
```

通用 review decision 示例：

```json
{
  "reviewId": "review-001",
  "action": "approve"
}
```

说明：

- `ReviewRequest.action` 支持 `approve`、`edit`、`retry`、`cancel`。
- `ReviewDecisionRequest.action` 支持 `approve`、`edit`、`retry`、`cancel`、`modify`、`deny`、`replan`、`respond`。
- 如果 session snapshot 仍在，但运行时 checkpoint 已缺失，review/resume 接口会返回 `409 Runtime checkpoint missing for session.`。

## Runtime 工作流

```text
用户输入
  ├── Fast 模式 -> fast_runner -> final_response
  └── Plan 模式 -> planner -> plan_review -> executor -> verifier -> formatter -> final_response
```

Plan 模式下：

- planner 只生成计划草案，不执行普通工具或 MCP 执行工具。
- executor 在计划获批后执行 todo，并记录 `toolCalls`、`artifacts`、`evidence`、`executionLog`。
- verifier 负责执行允许的验证命令并生成 `verification` 记录。
- formatter 产出最终报告，通常包含 `Result`、`Execution Summary`、`Plan Status`、`Artifacts And Evidence`、`Evidence`、`Verification`。

## Review 与 Session

`SessionResponse` 会暴露完整前端快照，包括：

- `todos`
- `planMeta`
- `executionLog`
- `timeline`
- `pendingReview`
- `reviews`
- `toolCalls`
- `artifacts`
- `evidence`
- `verification`
- `finalResponse`

Review 类型包括：

- `plan_review`：审核或修改计划。
- `tool_review`：高风险工具调用审批。
- `deviation_review`：执行阶段需要偏离已批准计划时触发。
- `mcp_review`：启用或调用 MCP server 时的审核。

`edit` 计划时会保留原始 `planMeta.verification` 建议字段。

## Profile 与工具策略

### Runtime 组装参数

创建 run 时可传入：

- `profile`
- `backendProfile`
- `permissionProfile`
- `toolPolicyProfile`

支持值：

- `profile`：`dev-default`、`locked-down`、`ci-eval`
- `backendProfile`：`ephemeral`、`workspace-readonly`、`workspace-edit`、`sandbox-exec`、`store-backed`
- `permissionProfile`：`workspace-readonly`、`workspace-edit`、`locked-down`、`ci-eval`
- `toolPolicyProfile`：`dev-default`、`locked-down`、`ci-eval`

说明：

- 未知 profile 会被直接拒绝，不会静默回退。
- `workspace-readonly`、`locked-down`、`ci-eval` 会通过文件系统权限拒绝写入。
- `sandbox-exec` 与 `store-backed` 当前仅完成参数解析，会发出 `runtime.profile_warning` timeline 事件，但还没有完整生产运行时支持。

### 默认工具策略

`dev-default` 的行为如下：

| 工具类型   | 示例                              | 默认行为             |
| ---------- | --------------------------------- | -------------------- |
| 只读工具   | `ls`、`glob`、`grep`、`read_file` | 允许                 |
| 写入工具   | `write_file`、`edit_file`         | 仅 executor 阶段允许 |
| 执行工具   | `execute`、`bash`、`shell`        | 进入 review          |
| 子任务工具 | `task`                            | 仅 executor 阶段允许 |
| 未知工具   | -                                 | 默认拒绝             |

补充说明：

- `locked-down` 只直接放行只读工具，其余大多要求 review 或被拒绝。
- `ci-eval` 只允许确定性的只读工具，拒绝有副作用工具。
- MCP 工具在启用后仍会按 metadata 风险和 profile 继续判定。

## 持久化与恢复

当前持久化能力分为两层：

- session store：保存 API snapshot 和 resume metadata。
- checkpoint reference：记录 checkpoint 路径引用。

当前实现限制：

- `FSAGENT_SESSION_STORE_PATH` 启用的 JSONL store 可以在服务重启后恢复 session snapshot。
- JSONL store 不保存 LangGraph runtime/checkpoint 本体。
- `FSAGENT_CHECKPOINTER_PATH` 目前不会实例化持久化 saver，只记录引用信息。
- 因此，服务重启后 `GET /api/runs/{sessionId}` 仍可能成功，但 review/resume 依然可能因缺失 runtime checkpoint 而失败。

## Verification

Plan 模式支持执行白名单内的验证命令，当前允许的前缀为：

```text
uv run --group test pytest
uv run --group test ruff check
uv run --group test ruff format
npm run build
```

行为说明：

- 非白名单命令不会执行，而是记录为 `skipped`，原因是 `Verification command is not allowlisted.`。
- 单条命令默认超时为 120 秒。
- 输出只保留摘要，不保存完整日志正文。
- verification 失败时，session 可进入 `needs_revision`，而不是直接标记为 `completed`。

## MCP 安全说明

MCP 默认关闭。启用时至少需要：

```json
{
  "mcpEnabled": true,
  "mcpConfigPath": "mcp.json"
}
```

如果配置中包含会启动本地进程的 stdio MCP server，还必须显式信任项目配置：

```json
{
  "trustProjectMcp": true
}
```

注意：

- `mcp.json` 默认被 `.gitignore` 忽略，可能包含本地命令、私有 server 或密钥。
- 未确认配置可信前，不要开启项目级 stdio MCP server。

## 日志

后端日志为 JSON Lines 格式，覆盖 HTTP 请求、session 生命周期、agent 轮次、工具调用摘要、review 流程和异常信息。

示例：

```bash
FSAGENT_LOG_LEVEL=DEBUG FSAGENT_LOG_FILE=logs/fsagent-api.jsonl ./scripts/start-dev.sh
```

说明：

- 未设置 `FSAGENT_LOG_FILE` 时，日志输出到 stdout。
- `agent.*` 事件会通过 `agent_mode` 与 `phase` 区分 `fast_runner`、`planner`、`executor`。
- 默认只记录摘要与元数据，不记录完整 prompt、工具参数正文或工具返回正文。

## 测试与质量检查

在 `harnessagents/fsagent/` 目录运行：

```bash
uv run --group test pytest fsagent/tests -q
uv run --group test ruff check fsagent
uv run --group test ruff format fsagent --diff
cd frontend
npm run build
node --test tests/*.test.mjs
npm exec -- playwright test --config playwright.controlled-replay.config.mjs
```

如果当前目录在 monorepo 根目录，可使用：

```bash
uv run --project harnessagents/fsagent --group test pytest harnessagents/fsagent/fsagent/tests -q
```

## 开发约定

- API 入口：`fsagent.api.server:main`
- 开发启动器入口：`fsagent.dev:main`
- 变更 runtime 行为时，优先补充 `fsagent/tests/` 对应测试。
- 变更 API schema、session 状态或 runtime 输出时，优先检查 `test_api.py`、`test_graph.py`、`test_planner.py`、`test_executor.py`。
- 变更开发启动器时，优先检查 `test_dev.py`。
- 变更模型配置逻辑时，优先检查 `test_model_config.py`。
- 变更 slash 模式路由时，优先检查 `test_slash_router.py`。
- 不要提交真实 `.env`、API Key、令牌或本地私密配置。

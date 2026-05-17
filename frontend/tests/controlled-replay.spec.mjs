import { test, expect } from "@playwright/test";

import {
  installControlledReplay,
  makeSession,
} from "./helpers/controlled-replay.mjs";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
  });
});

test("timeline lifecycle entries collapse repeated start and completion events into status rows", async ({ page }) => {
  await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          message: payload.message,
          status: "completed",
          finalResponse: "完成。",
          timeline: [
            {
              id: "timeline-collapse-001",
              kind: "run.started",
              message: "runtime 启动 · model=Qwen/Qwen3.5-27B",
              at: "2026-05-11T09:00:00.000Z",
            },
            {
              id: "timeline-collapse-002",
              kind: "agent.model.started",
              message: "fast_runner 模型调用开始",
              at: "2026-05-11T09:00:01.000Z",
              fields: { turn_index: 1 },
            },
            {
              id: "timeline-collapse-003",
              kind: "agent.model.completed",
              message: "fast_runner 模型调用完成",
              at: "2026-05-11T09:00:06.000Z",
              fields: { turn_index: 1 },
            },
            {
              id: "timeline-collapse-004",
              kind: "interrupt.tool_review",
              message: "等待用户审核",
              at: "2026-05-11T09:00:07.000Z",
            },
            {
              id: "timeline-collapse-005",
              kind: "agent.tool.started",
              message: "fast_runner 调用工具 trends-hub_get-weibo-trending",
              at: "2026-05-11T09:00:08.000Z",
              correlationId: "tool-1",
              fields: { tool_call_id: "tool-1" },
            },
            {
              id: "timeline-collapse-006",
              kind: "agent.tool.completed",
              message: "fast_runner 工具 trends-hub_get-weibo-trending 调用完成",
              at: "2026-05-11T09:00:11.000Z",
              correlationId: "tool-1",
              fields: { tool_call_id: "tool-1" },
            },
            {
              id: "timeline-collapse-007",
              kind: "run.completed",
              message: "运行完成",
              at: "2026-05-11T09:00:12.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview() {
      throw new Error("should not decide review in timeline collapse test");
    },
  });

  await page.goto("/");
  await page.getByPlaceholder("向 fsagent 发送消息（Fast 模式：最多一轮工具调用）").fill("验证运行日志归并。");
  await page.getByRole("button", { name: "提交 Fast 任务" }).click();

  await expect(page.getByText("runtime · model=Qwen/Qwen3.5-27B")).toBeVisible();
  await expect(page.getByText("fast_runner 模型调用", { exact: true })).toBeVisible();
  await expect(page.getByText("fast_runner 工具调用 trends-hub_get-weibo-trending", { exact: true })).toBeVisible();
  await expect(page.getByText("等待审核", { exact: true })).toBeVisible();
  await expect(page.getByText("5.0s")).toBeVisible();
  await expect(page.getByText("3.0s")).toBeVisible();
  await expect(page.getByText("2 条事件")).toHaveCount(3);
  await expect(page.getByText("fast_runner 模型调用开始")).toHaveCount(0);
  await expect(page.getByText("fast_runner 模型调用完成")).toHaveCount(0);
});

test("timeline folds tool review audit events into one visible row per tool", async ({ page }) => {
  await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          message: payload.message,
          status: "completed",
          finalResponse: "完成。",
          timeline: [
            {
              id: "timeline-tool-review-001",
              kind: "tool.policy_decision",
              message: "Tool policy review: execute",
              at: "2026-05-11T09:00:01.000Z",
              fields: { tool_call_id: "call-execute-001", policyDecision: "review" },
            },
            {
              id: "timeline-tool-review-002",
              kind: "interrupt.tool_review",
              message: "等待用户审核",
              at: "2026-05-11T09:00:02.000Z",
              fields: { tool_call_id: "call-execute-001", review_id: "review-001" },
            },
            {
              id: "timeline-tool-review-003",
              kind: "tool_review.approve",
              message: "用户批准 tool_review",
              at: "2026-05-11T09:00:03.000Z",
              fields: { tool_call_id: "call-execute-001", review_id: "review-001" },
            },
            {
              id: "timeline-tool-review-004",
              kind: "tool.policy_decision",
              message: "Tool policy review: execute",
              at: "2026-05-11T09:00:04.000Z",
              fields: { tool_call_id: "call-execute-001", policyDecision: "review" },
            },
            {
              id: "timeline-tool-review-005",
              kind: "agent.tool.started",
              message: "executor 调用工具 execute",
              at: "2026-05-11T09:00:05.000Z",
              fields: { tool_call_id: "call-execute-001" },
            },
            {
              id: "timeline-tool-review-006",
              kind: "agent.tool.completed",
              message: "executor 工具 execute 调用完成",
              at: "2026-05-11T09:00:08.000Z",
              fields: { tool_call_id: "call-execute-001" },
            },
          ],
        }),
      ];
    },
    async decideReview() {
      throw new Error("should not decide review in timeline audit folding test");
    },
  });

  await page.goto("/");
  await page.getByPlaceholder("向 fsagent 发送消息（Fast 模式：最多一轮工具调用）").fill("验证工具审批日志归并。");
  await page.getByRole("button", { name: "提交 Fast 任务" }).click();

  await expect(page.getByText("executor 工具调用 execute", { exact: true })).toHaveCount(1);
  await expect(page.getByText("Tool policy review: execute")).toHaveCount(0);
  await expect(page.getByText("用户批准 tool_review")).toHaveCount(0);
  await expect(page.getByText("等待用户审核")).toHaveCount(0);
  await expect(page.getByText("6 条事件")).toHaveCount(1);
});

test("FAST-04: fast 高风险工具进入 tool review", async ({ page }) => {
  const review = {
    id: "review-fast-tool-001",
    kind: "tool_review",
    status: "pending",
    risk: "high",
    subject: {
      actionRequests: [
        {
          id: "call-execute-001",
          name: "execute",
          description: "Run command.",
          args: { command: "uv run --group test pytest fsagent/tests -q" },
        },
      ],
    },
    proposedInputSummary: "execute: uv run --group test pytest fsagent/tests -q",
    allowedActions: ["approve", "modify", "deny", "cancel"],
    createdAt: "2026-05-11T09:00:01.000Z",
    updatedAt: "2026-05-11T09:00:01.000Z",
  };
  const requests = await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          message: payload.message,
          status: "awaiting_tool_review",
          pendingReview: review,
          reviews: [review],
          toolCalls: [
            {
              id: "call-execute-001",
              name: "execute",
              risk: "high",
              status: "review_required",
              inputSummary: "execute: uv run --group test pytest fsagent/tests -q",
              reviewId: review.id,
            },
          ],
          timeline: [
            {
              id: "timeline-001",
              kind: "interrupt.tool_review",
              message: "等待工具审批",
              at: "2026-05-11T09:00:01.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview() {
      throw new Error("should not decide review in FAST-04");
    },
  });

  await page.goto("/");
  await page.getByPlaceholder("向 fsagent 发送消息（Fast 模式：最多一轮工具调用）").fill("在项目根目录执行测试并告诉我失败摘要。");
  await page.getByRole("button", { name: "提交 Fast 任务" }).click();

  await expect(page.getByRole("main").getByText("工具审批", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "批准" })).toBeVisible();
  await expect(page.getByRole("button", { name: "逐项修改" })).toBeVisible();
  await expect(page.getByRole("button", { name: "拒绝" })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消" })).toBeVisible();
  await expect(page.getByText("execute", { exact: true })).toBeVisible();
  await expect(page.getByText('"command": "uv run --group test pytest fsagent/tests -q"')).toBeVisible();

  expect(requests).toHaveLength(1);
  expect(requests[0].payload.mode).toBe("fast");
});

test("FAST-05: fast 批准 tool review 后恢复完成", async ({ page }) => {
  const review = {
    id: "review-fast-tool-002",
    kind: "tool_review",
    status: "pending",
    risk: "high",
    subject: {
      actionRequests: [
        {
          id: "call-execute-002",
          name: "execute",
          description: "Run command.",
          args: { command: "uv run --group test pytest fsagent/tests/test_api.py -q" },
        },
      ],
    },
    proposedInputSummary: "execute: uv run --group test pytest fsagent/tests/test_api.py -q",
    allowedActions: ["approve", "modify", "deny", "cancel"],
    createdAt: "2026-05-11T09:10:01.000Z",
    updatedAt: "2026-05-11T09:10:01.000Z",
  };
  const requests = await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          message: payload.message,
          status: "awaiting_tool_review",
          pendingReview: review,
          reviews: [review],
          timeline: [
            {
              id: "timeline-fast-approve-001",
              kind: "interrupt.tool_review",
              message: "等待工具审批",
              at: "2026-05-11T09:10:01.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview(payload) {
      return [
        makeSession({
          message: "在项目根目录执行测试并告诉我失败摘要。",
          status: "completed",
          finalResponse: "## 测试摘要\n\n- `fsagent/tests/test_api.py` 全部通过",
          pendingReview: null,
          reviews: [
            {
              ...review,
              status: "approved",
              decision: { action: payload.action, reviewId: payload.reviewId },
            },
          ],
          timeline: [
            {
              id: "timeline-fast-approve-001",
              kind: "interrupt.tool_review",
              message: "等待工具审批",
              at: "2026-05-11T09:10:01.000Z",
            },
            {
              id: "timeline-fast-approve-002",
              kind: "tool_review.approve",
              message: "用户批准 tool_review",
              at: "2026-05-11T09:10:02.000Z",
            },
            {
              id: "timeline-fast-approve-003",
              kind: "run.completed",
              message: "运行完成",
              at: "2026-05-11T09:10:03.000Z",
            },
          ],
        }),
      ];
    },
  });

  await page.goto("/");
  await page.getByPlaceholder("向 fsagent 发送消息（Fast 模式：最多一轮工具调用）").fill("在项目根目录执行测试并告诉我失败摘要。");
  await page.getByRole("button", { name: "提交 Fast 任务" }).click();
  await page.getByRole("button", { name: "批准" }).click();

  await expect(page.getByText("● 已完成")).toBeVisible();
  await expect(page.getByText("测试摘要")).toBeVisible();
  await expect(page.getByText("tool_review.approve")).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[1].payload.action).toBe("approve");
  expect(requests[1].payload.reviewId).toBe(review.id);
});

test("PLAN: 批准计划后即使 executionLog 为空也显示执行进度", async ({ page }) => {
  const review = {
    id: "review-plan-approve-001",
    kind: "plan_review",
    status: "pending",
    risk: "low",
    subject: {},
    proposedInputSummary: "Review the plan above. Approve to execute all items.",
    allowedActions: ["approve", "edit", "retry", "cancel"],
    createdAt: "2026-05-11T09:30:01.000Z",
    updatedAt: "2026-05-11T09:30:01.000Z",
  };
  const todos = [
    { id: "todo-001", content: "测试 docs-langchain 工具", status: "pending" },
    { id: "todo-002", content: "测试 hf-mcp 工具", status: "pending" },
  ];
  const requests = await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          mode: "plan",
          message: payload.message,
          status: "awaiting_plan_review",
          todos,
          planMeta: { goal: "测试 MCP 工具" },
          pendingReview: review,
          reviews: [review],
          timeline: [
            {
              id: "timeline-plan-approve-001",
              kind: "interrupt.plan_review",
              message: "等待计划审核",
              at: "2026-05-11T09:30:01.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview() {
      return [
        makeSession({
          mode: "plan",
          message: "测试两个 MCP 工具。",
          status: "executing",
          todos,
          executionLog: [],
          planMeta: { goal: "测试 MCP 工具" },
          pendingReview: null,
          reviews: [{ ...review, status: "approved", decision: { action: "approve" } }],
          timeline: [
            {
              id: "timeline-plan-approve-001",
              kind: "interrupt.plan_review",
              message: "等待计划审核",
              at: "2026-05-11T09:30:01.000Z",
            },
            {
              id: "timeline-plan-approve-002",
              kind: "plan_review.approve",
              message: "用户批准 plan_review",
              at: "2026-05-11T09:30:02.000Z",
            },
          ],
        }),
      ];
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await page.getByPlaceholder("描述要规划的复杂任务（Plan 模式：先生成计划再确认执行）").fill("测试两个 MCP 工具。");
  await page.getByRole("button", { name: "提交 Plan 任务" }).click();
  await page.getByRole("button", { name: "批准并执行" }).click();

  await expect(page.getByText("执行进度")).toBeVisible();
  await expect(page.getByText("计划审核", { exact: true })).toHaveCount(0);
  await expect(page.getByText("共 2 项")).toBeVisible();
  await expect(page.getByText("测试 docs-langchain 工具")).toBeVisible();
  await expect(page.getByText("测试 hf-mcp 工具")).toBeVisible();
  expect(requests[1].payload.action).toBe("approve");
});

test("PLAN-09: plan 拒绝 tool review 后进入 needs_revision", async ({ page }) => {
  const review = {
    id: "review-plan-tool-001",
    kind: "tool_review",
    status: "pending",
    risk: "high",
    subject: {
      actionRequests: [
        {
          id: "call-shell-001",
          name: "shell",
          description: "Run shell command.",
          args: { command: "pytest -q" },
        },
      ],
    },
    proposedInputSummary: "shell: pytest -q",
    allowedActions: ["approve", "modify", "deny", "cancel"],
    createdAt: "2026-05-11T10:00:01.000Z",
    updatedAt: "2026-05-11T10:00:01.000Z",
  };
  const requests = await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          mode: "plan",
          message: payload.message,
          status: "awaiting_tool_review",
          todos: [{ id: "todo-001", content: "运行测试并汇总失败原因", status: "in_progress" }],
          executionLog: [{ id: "log-001", todoId: "todo-001", content: "运行测试并汇总失败原因", status: "in_progress" }],
          pendingReview: review,
          reviews: [review],
          timeline: [
            {
              id: "timeline-plan-deny-001",
              kind: "interrupt.tool_review",
              message: "等待工具审批",
              at: "2026-05-11T10:00:01.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview(payload) {
      return [
        makeSession({
          mode: "plan",
          message: "运行测试并汇总失败原因，执行任何命令前都先让我确认。",
          status: "needs_revision",
          todos: [{ id: "todo-001", content: "运行测试并汇总失败原因", status: "failed", failureReason: payload.reason }],
          executionLog: [{ id: "log-001", todoId: "todo-001", content: "运行测试并汇总失败原因", status: "failed", error: payload.reason }],
          pendingReview: null,
          reviews: [
            {
              ...review,
              status: "denied",
              decision: { action: payload.action, reason: payload.reason },
            },
          ],
          finalResponse: "## Result\n已停止执行。\n\n## Verification\n- verification-001: skipped - No verification command was provided.",
          verification: [
            {
              id: "verification-001",
              status: "skipped",
              reason: "No verification command was provided.",
            },
          ],
          timeline: [
            {
              id: "timeline-plan-deny-001",
              kind: "interrupt.tool_review",
              message: "等待工具审批",
              at: "2026-05-11T10:00:01.000Z",
            },
            {
              id: "timeline-plan-deny-002",
              kind: "tool_review.deny",
              message: "用户拒绝 tool_review",
              at: "2026-05-11T10:00:02.000Z",
            },
            {
              id: "timeline-plan-deny-003",
              kind: "run.needs_revision",
              message: "运行需要修订",
              at: "2026-05-11T10:00:03.000Z",
            },
          ],
        }),
      ];
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await page.getByPlaceholder("描述要规划的复杂任务（Plan 模式：先生成计划再确认执行）").fill("运行测试并汇总失败原因，执行任何命令前都先让我确认。");
  await page.getByRole("button", { name: "提交 Plan 任务" }).click();
  await page.getByPlaceholder("拒绝、回应或重新规划时可填写原因。").fill("不要执行 shell，只给出建议。");
  await page.getByRole("button", { name: "拒绝" }).click();

  await expect(page.getByText("需修订")).toBeVisible();
  await expect(page.getByText("tool_review.deny")).toBeVisible();
  await expect(page.getByText("不要执行 shell，只给出建议。")).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[0].payload.mode).toBe("plan");
  expect(requests[1].payload.action).toBe("deny");
});

test("PLAN-11: verification failed 渲染为 needs_revision", async ({ page }) => {
  await installControlledReplay(page, {
    async createRun(payload) {
      return [
        makeSession({
          mode: "plan",
          message: payload.message,
          status: "needs_revision",
          todos: [{ id: "todo-001", content: "运行测试并修复失败", status: "completed" }],
          executionLog: [{ id: "log-001", todoId: "todo-001", content: "运行测试并修复失败", status: "completed", result: "已执行 pytest 并收集失败摘要。" }],
          finalResponse: "## Result\n需要修订。\n\n## Verification\n- verification-001: failed - pytest -q",
          verification: [
            {
              id: "verification-001",
              command: "pytest -q",
              status: "failed",
              exitCode: 1,
              stderrSummary: "2 failed",
            },
          ],
          timeline: [
            {
              id: "timeline-plan-verify-001",
              kind: "verification.failed",
              message: "验证失败",
              at: "2026-05-11T10:20:01.000Z",
            },
            {
              id: "timeline-plan-verify-002",
              kind: "run.needs_revision",
              message: "运行需要修订",
              at: "2026-05-11T10:20:02.000Z",
            },
          ],
        }),
      ];
    },
    async decideReview() {
      throw new Error("should not decide review in PLAN-11");
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await page.getByPlaceholder("描述要规划的复杂任务（Plan 模式：先生成计划再确认执行）").fill("运行测试并修复失败。");
  await page.getByRole("button", { name: "提交 Plan 任务" }).click();

  await expect(page.getByText("需修订")).toBeVisible();
  await expect(page.getByText("最终报告")).toBeVisible();
  await expect(page.getByText("verification-001: failed - pytest -q")).toBeVisible();
  await expect(page.getByText("已执行 pytest 并收集失败摘要。")).toBeVisible();
});

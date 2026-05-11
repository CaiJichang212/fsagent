const DEFAULT_MODEL_CONFIG = {
  models: [
    {
      name: "Qwen/Qwen3.5-27B",
      displayName: "Qwen3.5-27B",
      supportsThinking: true,
      defaultThinkingEnabled: false,
      thinkingSwitchType: "toggle",
      sampling: {},
    },
  ],
};

export function sessionFrame(session) {
  return `event: session\ndata: ${JSON.stringify(session)}\n\n`;
}

export function sessionStream(...sessions) {
  return sessions.map(sessionFrame).join("");
}

export function makeSession(overrides = {}) {
  const base = {
    sessionId: "session-001",
    threadId: "thread-001",
    mode: "fast",
    status: "running",
    message: "placeholder",
    model: "Qwen/Qwen3.5-27B",
    thinking: false,
    mcpEnabled: false,
    trustProjectMcp: false,
    todos: [],
    executionLog: [],
    timeline: [
      {
        id: "timeline-001",
        kind: "session.created",
        message: "创建会话",
        at: "2026-05-11T09:00:00.000Z",
      },
    ],
    pendingReview: null,
    reviews: [],
    toolCalls: [],
    artifacts: [],
    evidence: [],
    verification: [],
    createdAt: "2026-05-11T09:00:00.000Z",
    updatedAt: "2026-05-11T09:00:00.000Z",
  };
  return { ...base, ...overrides };
}

export async function installControlledReplay(page, handlers) {
  const requests = [];

  await page.route("**/api/model-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(handlers.modelConfig ?? DEFAULT_MODEL_CONFIG),
    });
  });

  await page.route("**/api/runs/stream", async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    requests.push({ kind: "createRun", payload });
    const sessions = await handlers.createRun(payload);
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sessionStream(...sessions),
    });
  });

  await page.route(/\/api\/runs\/[^/]+\/reviews\/[^/]+\/decision\/stream$/, async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    requests.push({ kind: "reviewDecision", payload, url: route.request().url() });
    const sessions = await handlers.decideReview(payload, route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sessionStream(...sessions),
    });
  });

  return requests;
}

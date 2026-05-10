import { FsAgentSession, TimelineEvent, TodoItem } from "./types";

const uid = () => Math.random().toString(36).slice(2, 10);
const now = () => new Date().toISOString();

export function newEvent(kind: TimelineEvent["kind"], message: string): TimelineEvent {
  return { id: uid(), kind, message, at: now() };
}

export function mockGeneratePlan(task: string): { todos: TodoItem[]; planMeta: NonNullable<FsAgentSession["planMeta"]> } {
  const goal = task.length > 80 ? task.slice(0, 80) + "…" : task;
  return {
    planMeta: {
      goal,
      assumptions: ["项目使用 Python", "运行环境可访问网络与 MCP server"],
      final_output_format: "markdown",
    },
    todos: [
      { content: "分析项目目录结构与入口文件", status: "pending" },
      { content: "识别核心模块与依赖关系", status: "pending" },
      { content: "梳理 Fast / Plan 双模式运行流程", status: "pending" },
      { content: "整理 HITL 中断与 resume 协议", status: "pending" },
      { content: "汇总优化建议并输出 Markdown 报告", status: "pending" },
    ],
  };
}

export function mockExecuteResult(content: string): { ok: boolean; result: string; error?: string } {
  if (/HITL|resume/i.test(content) && Math.random() < 0.25) {
    return { ok: false, result: "", error: "工具 search_docs 调用超时（mock）" };
  }
  return {
    ok: true,
    result: `已完成「${content}」。生成了相关分析片段并写入证据池（mock）。`,
  };
}

export function buildFinalReport(session: FsAgentSession): string {
  const completed = session.executionLog.filter((e) => e.status === "completed").length;
  const failed = session.executionLog.filter((e) => e.status === "failed").length;
  const total = session.executionLog.length;

  const lines: string[] = [];
  lines.push(`# Result\n`);
  lines.push(
    `根据计划「${session.planMeta?.goal ?? session.message}」，已完成 ${completed}/${total} 项任务${
      failed ? `，其中 ${failed} 项失败` : ""
    }。`,
  );
  lines.push(`\n# Execution Summary\n`);
  session.executionLog.forEach((e, i) => {
    const mark = e.status === "completed" ? "- [x]" : e.status === "failed" ? "- [!]" : "- [ ]";
    lines.push(`${mark} ${i + 1}. ${e.content}`);
    if (e.result) lines.push(`    - 结果：${e.result}`);
    if (e.error) lines.push(`    - 错误：${e.error}`);
  });
  lines.push(`\n# Plan Status\n`);
  lines.push(`- 目标：${session.planMeta?.goal ?? "-"}`);
  lines.push(`- 假设：${(session.planMeta?.assumptions ?? []).join("；") || "-"}`);
  lines.push(`- 输出格式：${session.planMeta?.final_output_format ?? "markdown"}`);
  lines.push(`\n# Artifacts And Evidence\n`);
  lines.push(`- 模型：${session.model}`);
  lines.push(`- MCP：${session.mcpEnabled ? "启用" : "禁用"}`);
  lines.push(`- Thread ID：\`${session.threadId}\``);
  return lines.join("\n");
}

export function emptySession(mode: FsAgentSession["mode"]): FsAgentSession {
  return {
    sessionId: uid(),
    threadId: uid(),
    mode,
    status: "idle",
    message: "",
    model: "Qwen/Qwen3.5-27B",
    thinking: false,
    mcpEnabled: false,
    trustProjectMcp: false,
    todos: [],
    executionLog: [],
    timeline: [],
    createdAt: now(),
    updatedAt: now(),
  };
}

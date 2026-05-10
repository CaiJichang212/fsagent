export type RuntimeMode = "fast" | "plan";

export type SessionStatus =
  | "idle"
  | "running"
  | "planning"
  | "awaiting_plan_review"
  | "editing_plan"
  | "retrying_plan"
  | "executing"
  | "awaiting_tool_review"
  | "completed"
  | "cancelled"
  | "failed";

export type TodoStatus = "pending" | "in_progress" | "completed" | "failed";
export type ExecutionStatus = TodoStatus | "skipped";

export interface TodoItem {
  content: string;
  status: TodoStatus;
}

export interface PlanMeta {
  goal: string;
  assumptions?: string[];
  final_output_format?: string;
}

export interface ExecutionLogEntry {
  content: string;
  status: ExecutionStatus;
  result?: string | null;
  error?: string | null;
}

export interface TimelineEvent {
  id: string;
  kind:
    | "session.created"
    | "run.started"
    | "agent.started"
    | "agent.model.started"
    | "agent.model.completed"
    | "agent.model.failed"
    | "agent.tool.started"
    | "agent.tool.completed"
    | "agent.tool.failed"
    | "agent.tools.disabled"
    | "agent.completed"
    | "planner.started"
    | "planner.capability_warning"
    | "planner.completed"
    | "interrupt.plan_review"
    | "plan.approved"
    | "plan.edited"
    | "plan.retrying"
    | "plan.cancelled"
    | "todo.started"
    | "todo.completed"
    | "todo.failed"
    | "interrupt.tool_review"
    | "run.completed"
    | "run.failed";
  message: string;
  at: string;
}

export interface FsAgentSession {
  sessionId: string;
  threadId: string;
  mode: RuntimeMode;
  status: SessionStatus;
  message: string;
  model: string;
  thinking: boolean;
  mcpEnabled: boolean;
  trustProjectMcp: boolean;
  todos: TodoItem[];
  planMeta?: PlanMeta;
  executionLog: ExecutionLogEntry[];
  timeline: TimelineEvent[];
  finalResponse?: string;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ModelConfigItem {
  name: string;
  displayName: string;
  supportsThinking: boolean;
  defaultThinkingEnabled: boolean;
  thinkingSwitchType: "toggle" | "fixed" | "none";
  sampling: Record<string, Record<string, number>>;
}

export interface ModelConfigResponse {
  models: ModelConfigItem[];
}

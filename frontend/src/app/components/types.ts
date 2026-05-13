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
  | "verifying"
  | "needs_revision"
  | "completed"
  | "cancelled"
  | "failed";

export type RiskLevel = "low" | "medium" | "high" | "critical";
export type ReviewKind = "plan_review" | "tool_review" | "deviation_review" | "mcp_review";
export type ReviewStatus = "pending" | "approved" | "modified" | "denied" | "cancelled" | "expired";
export type ReviewAction = "approve" | "edit" | "retry" | "cancel" | "modify" | "deny" | "replan" | "respond";
export type TodoStatus = "pending" | "in_progress" | "completed" | "failed" | "blocked";
export type ExecutionStatus = TodoStatus | "skipped";
export type VerificationStatus = "passed" | "failed" | "skipped" | "manual";
export type TimelineEventKind =
  | "session.created"
  | "run.started"
  | "run.completed"
  | "run.needs_revision"
  | "run.cancelled"
  | "run.failed"
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
  | "interrupt.tool_review"
  | "plan.approved"
  | "plan.edited"
  | "plan.retrying"
  | "plan.cancelled"
  | "tool.policy_decision"
  | "tool_review.approve"
  | "tool_review.modify"
  | "tool_review.deny"
  | "tool_review.cancel"
  | "deviation_review.approve"
  | "deviation_review.replan"
  | "deviation_review.cancel"
  | "mcp_review.approve"
  | "mcp_review.deny"
  | "mcp_review.cancel"
  | "todo.started"
  | "todo.completed"
  | "todo.failed"
  | "verification.failed";

export interface TodoItem {
  id?: string | null;
  content: string;
  status: TodoStatus;
  risk?: RiskLevel;
  dependsOn?: string[];
  evidenceIds?: string[];
  verificationIds?: string[];
  failureReason?: string | null;
}

export interface PlanMeta {
  goal: string;
  assumptions?: string[];
  final_output_format?: string;
}

export interface ExecutionLogEntry {
  id?: string | null;
  todoId?: string | null;
  content: string;
  status: ExecutionStatus;
  result?: string | null;
  error?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  toolCallIds?: string[];
  artifactIds?: string[];
  verificationIds?: string[];
}

export interface TimelineEvent {
  id: string;
  kind: TimelineEventKind | (string & {});
  message: string;
  at: string;
  phase?: string | null;
  severity?: "info" | "warning" | "error";
  correlationId?: string | null;
  fields?: Record<string, unknown>;
}

export interface ToolCallRecord {
  id: string;
  todoId?: string | null;
  name: string;
  risk: RiskLevel;
  status: string;
  inputSummary?: string | null;
  outputSummary?: string | null;
  reviewId?: string | null;
  durationMs?: number | null;
}

export interface ArtifactRecord {
  id: string;
  kind: string;
  path?: string | null;
  summary: string;
  size?: number | null;
  sha256?: string | null;
  redactionStatus: string;
  createdAt?: string | null;
}

export interface EvidenceRecord {
  id: string;
  todoId?: string | null;
  toolCallId?: string | null;
  artifactId?: string | null;
  summary: string;
  source: "tool" | "runtime" | "verifier" | "user";
  createdAt?: string | null;
}

export interface VerificationRecord {
  id: string;
  todoId?: string | null;
  command?: string | null;
  status: VerificationStatus;
  exitCode?: number | null;
  stdoutSummary?: string | null;
  stderrSummary?: string | null;
  artifactId?: string | null;
  reason?: string | null;
  createdAt?: string | null;
}

export interface ReviewRecord {
  id: string;
  kind: ReviewKind;
  status: ReviewStatus;
  risk: RiskLevel;
  subject: Record<string, unknown>;
  proposedInputSummary?: string | null;
  allowedActions: ReviewAction[];
  decision?: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
  expiresAt?: string | null;
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
  pendingReview?: ReviewRecord | null;
  reviews: ReviewRecord[];
  toolCalls: ToolCallRecord[];
  artifacts: ArtifactRecord[];
  evidence: EvidenceRecord[];
  verification: VerificationRecord[];
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

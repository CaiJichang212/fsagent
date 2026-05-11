import { useMemo, useState } from "react";
import { AlertTriangle, Check, MessageSquare, Pencil, RefreshCcw, ShieldAlert, X } from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";
import { PlanReviewPanel } from "./plan-review-panel";
import { PlanMeta, ReviewAction, ReviewRecord, SessionStatus, TodoItem } from "./types";

interface ReviewDecisionPayload {
  action: ReviewAction;
  editedSubject?: Record<string, unknown>;
  feedback?: string;
  reason?: string;
}

interface Props {
  review: ReviewRecord;
  todos: TodoItem[];
  planMeta?: PlanMeta;
  onDecision: (review: ReviewRecord, payload: ReviewDecisionPayload, pendingStatus: SessionStatus) => void;
}

const REVIEW_LABEL: Record<ReviewRecord["kind"], string> = {
  plan_review: "计划审核",
  tool_review: "工具审批",
  deviation_review: "偏离审批",
  mcp_review: "MCP 审批",
};

type ToolDecisionType = "approve" | "edit" | "reject" | "respond";

interface ActionRequest {
  id?: string;
  name: string;
  description?: string;
  args: Record<string, unknown>;
}

interface DraftToolDecision {
  type: ToolDecisionType;
  argsText: string;
  message: string;
}

export function ReviewGate({ review, todos, planMeta, onDecision }: Props) {
  if (review.kind === "plan_review" && planMeta) {
    return (
      <PlanReviewPanel
        todos={todos}
        planMeta={planMeta}
        instructions={review.proposedInputSummary || "Review the plan above. Approve to execute all items."}
        allowedActions={review.allowedActions.filter(isPlanAction)}
        onApprove={() => onDecision(review, { action: "approve" }, "executing")}
        onEdit={(editedTodos, editedPlanMeta) =>
          onDecision(
            review,
            {
              action: "edit",
              editedSubject: {
                todos: editedTodos,
                planMeta: editedPlanMeta,
              },
            },
            "editing_plan",
          )
        }
        onRetry={(feedback) => onDecision(review, { action: "retry", feedback }, "retrying_plan")}
        onCancel={(reason) => onDecision(review, { action: "cancel", reason }, "cancelled")}
      />
    );
  }
  return <GenericReviewGate review={review} onDecision={onDecision} />;
}

function GenericReviewGate({
  review,
  onDecision,
}: {
  review: ReviewRecord;
  onDecision: Props["onDecision"];
}) {
  const [feedback, setFeedback] = useState("");
  const [modifying, setModifying] = useState(false);
  const [draftDecisions, setDraftDecisions] = useState<DraftToolDecision[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const actionRequests = useMemo(() => readActionRequests(review.subject), [review.subject]);
  const reviewConfigs = useMemo(() => readReviewConfigs(review.subject), [review.subject]);
  const can = (action: ReviewAction) => review.allowedActions.includes(action);
  const allowedDecisionTypes = (index: number) => {
    const allowed = reviewConfigs[index]?.allowedDecisions ?? [];
    return allowed.length > 0 ? allowed : ["approve", "edit", "reject"];
  };

  const submit = (action: ReviewAction, pendingStatus: SessionStatus = "executing") => {
    onDecision(
      review,
      {
        action,
        feedback: feedback.trim() || undefined,
        reason: feedback.trim() || undefined,
      },
      pendingStatus,
    );
    setFeedback("");
  };

  const startModify = () => {
    setDraftDecisions(
      actionRequests.map((request, index) => ({
        type: defaultDecisionType(allowedDecisionTypes(index)),
        argsText: formatArgs(request.args),
        message: feedback.trim(),
      })),
    );
    setValidationError(null);
    setModifying(true);
  };

  const updateDraftDecision = (index: number, patch: Partial<DraftToolDecision>) => {
    setDraftDecisions((current) =>
      current.map((decision, decisionIndex) => (decisionIndex === index ? { ...decision, ...patch } : decision)),
    );
  };

  const submitModify = () => {
    if (actionRequests.length === 0) return;
    if (draftDecisions.length !== actionRequests.length) {
      setValidationError("每个工具调用都需要一个审核决定。");
      return;
    }
    try {
      const decisions = draftDecisions.map((decision, index) =>
        buildToolDecision(actionRequests[index], decision, feedback.trim()),
      );
      onDecision(
        review,
        {
          action: "modify",
          editedSubject: {
            decisions,
          },
          feedback: feedback.trim() || undefined,
          reason: feedback.trim() || undefined,
        },
        "executing",
      );
      setFeedback("");
      setModifying(false);
      setValidationError(null);
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : "工具参数不是有效 JSON。");
    }
  };

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-start gap-3">
        <div className="w-8 h-8 rounded-lg bg-amber-500/15 text-amber-500 grid place-items-center shrink-0">
          <ShieldAlert className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span>{REVIEW_LABEL[review.kind]}</span>
            <Badge variant="outline" className="text-xs">{review.kind}</Badge>
            <Badge variant="secondary" className="text-xs">{review.risk}</Badge>
          </div>
          {review.proposedInputSummary && (
            <p className="text-sm text-muted-foreground mt-1 break-words">{review.proposedInputSummary}</p>
          )}
        </div>
      </div>

      <div className="px-5 py-4 space-y-4">
        {actionRequests.length > 0 ? (
          <div className="space-y-2">
            <Label className="text-sm">待审核工具调用</Label>
            <div className="space-y-2">
              {actionRequests.map((request, index) => (
                <div key={request.id ?? `${request.name}-${index}`} className="rounded-md border border-border bg-background/50 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="text-xs">{request.name || "tool"}</Badge>
                    {request.description && <span className="text-sm text-muted-foreground">{request.description}</span>}
                  </div>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-xs">
                    {formatArgs(request.args)}
                  </pre>
                  {modifying && draftDecisions[index] && (
                    <div className="mt-3 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Label className="text-xs text-muted-foreground">决定</Label>
                        <select
                          value={draftDecisions[index].type}
                          onChange={(event) =>
                            updateDraftDecision(index, { type: event.target.value as ToolDecisionType })
                          }
                          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                        >
                          {allowedDecisionTypes(index).map((decision) => (
                            <option key={decision} value={decision}>
                              {toolDecisionLabel(decision)}
                            </option>
                          ))}
                        </select>
                      </div>
                      {draftDecisions[index].type === "edit" && (
                        <Textarea
                          value={draftDecisions[index].argsText}
                          onChange={(event) => updateDraftDecision(index, { argsText: event.target.value })}
                          rows={6}
                          className="font-mono text-xs"
                        />
                      )}
                      {(draftDecisions[index].type === "reject" || draftDecisions[index].type === "respond") && (
                        <Textarea
                          value={draftDecisions[index].message}
                          onChange={(event) => updateDraftDecision(index, { message: event.target.value })}
                          rows={2}
                          placeholder="本项说明"
                        />
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-2 rounded-md border border-border bg-background/50 px-3 py-2 text-sm text-muted-foreground">
            <AlertTriangle className="w-4 h-4 mt-0.5 text-amber-500 shrink-0" />
            <span>当前审核没有结构化工具调用；请根据摘要选择处理动作。</span>
          </div>
        )}

        {(can("deny") || can("cancel") || can("respond") || can("replan")) && (
          <div className="space-y-2">
            <Label className="text-sm">说明</Label>
            <Textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              rows={3}
              placeholder="拒绝、回应或重新规划时可填写原因。"
            />
          </div>
        )}

        {modifying && validationError && <div className="text-sm text-destructive">{validationError}</div>}
      </div>

      <div className="px-5 py-3 border-t border-border bg-muted/30 flex flex-wrap items-center gap-2 justify-end">
        {modifying ? (
          <>
            <Button variant="ghost" onClick={() => setModifying(false)}>
              放弃修改
            </Button>
            <Button onClick={submitModify} className="gap-1.5">
              <Pencil className="w-4 h-4" />
              提交修改
            </Button>
          </>
        ) : (
          <>
            {can("cancel") && (
              <Button variant="ghost" onClick={() => submit("cancel", "cancelled")} className="gap-1.5">
                <X className="w-4 h-4" />
                取消
              </Button>
            )}
            {can("deny") && (
              <Button variant="outline" onClick={() => submit("deny", "needs_revision")} className="gap-1.5">
                <X className="w-4 h-4" />
                拒绝
              </Button>
            )}
            {can("replan") && (
              <Button variant="outline" onClick={() => submit("replan", "retrying_plan")} className="gap-1.5">
                <RefreshCcw className="w-4 h-4" />
                重新规划
              </Button>
            )}
            {can("respond") && (
              <Button variant="outline" onClick={() => submit("respond")} className="gap-1.5">
                <MessageSquare className="w-4 h-4" />
                回应
              </Button>
            )}
            {can("modify") && actionRequests.length > 0 && (
              <Button variant="outline" onClick={startModify} className="gap-1.5">
                <Pencil className="w-4 h-4" />
                逐项修改
              </Button>
            )}
            {can("approve") && (
              <Button onClick={() => submit("approve")} className="gap-1.5">
                <Check className="w-4 h-4" />
                批准
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function isPlanAction(action: ReviewAction): action is "approve" | "edit" | "retry" | "cancel" {
  return action === "approve" || action === "edit" || action === "retry" || action === "cancel";
}

function readActionRequests(subject: Record<string, unknown>): ActionRequest[] {
  const raw = subject.actionRequests;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((item) => ({
      id: typeof item.id === "string" ? item.id : undefined,
      name: typeof item.name === "string" ? item.name : "",
      description: typeof item.description === "string" ? item.description : undefined,
      args:
        typeof item.args === "object" && item.args !== null && !Array.isArray(item.args)
          ? (item.args as Record<string, unknown>)
          : {},
    }));
}

function readReviewConfigs(subject: Record<string, unknown>) {
  const raw = subject.reviewConfigs;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((item) => ({
      allowedDecisions: readAllowedDecisions(item.allowedDecisions),
    }));
}

function readAllowedDecisions(value: unknown): ToolDecisionType[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is ToolDecisionType =>
    item === "approve" || item === "edit" || item === "reject" || item === "respond",
  );
}

function defaultDecisionType(allowedDecisions: ToolDecisionType[]): ToolDecisionType {
  if (allowedDecisions.includes("edit")) return "edit";
  if (allowedDecisions.includes("approve")) return "approve";
  if (allowedDecisions.includes("reject")) return "reject";
  return "respond";
}

function buildToolDecision(request: ActionRequest, decision: DraftToolDecision, fallbackMessage: string) {
  if (decision.type === "edit") {
    const args = JSON.parse(decision.argsText) as unknown;
    if (typeof args !== "object" || args === null || Array.isArray(args)) {
      throw new Error("工具参数必须是 JSON object。");
    }
    return {
      type: "edit",
      edited_action: {
        name: request.name,
        args,
      },
      ...(decision.message.trim() || fallbackMessage ? { message: decision.message.trim() || fallbackMessage } : {}),
    };
  }
  if (decision.type === "reject" || decision.type === "respond") {
    const message = decision.message.trim() || fallbackMessage;
    if (decision.type === "respond" && !message) {
      throw new Error("回应决定必须填写说明。");
    }
    return {
      type: decision.type,
      ...(message ? { message } : {}),
    };
  }
  return { type: "approve" };
}

function toolDecisionLabel(decision: ToolDecisionType) {
  return {
    approve: "批准",
    edit: "修改",
    reject: "拒绝",
    respond: "回应",
  }[decision];
}

function formatArgs(args: unknown) {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

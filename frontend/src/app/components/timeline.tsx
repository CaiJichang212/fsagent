import {
  AlertTriangle,
  Ban,
  Check,
  CircleDot,
  Cog,
  FileCheck2,
  Hammer,
  LoaderCircle,
  MessageSquare,
  ListChecks,
  Pause,
  Play,
  RefreshCcw,
  Sparkles,
  X,
} from "lucide-react";
import { TimelineEvent } from "./types";
import { cn } from "./ui/utils";

interface Props {
  events: TimelineEvent[];
}

type TimelineStatus = "running" | "completed" | "failed" | "waiting" | "cancelled" | "warning" | "info";

interface TimelineItem {
  id: string;
  title: string;
  status: TimelineStatus;
  startedAt: string;
  endedAt?: string;
  kind: string;
  count: number;
}

const ICONS: Record<string, React.ReactNode> = {
  "session.created": <Sparkles className="w-3.5 h-3.5" />,
  "run.started": <Play className="w-3.5 h-3.5" />,
  "run.needs_revision": <AlertTriangle className="w-3.5 h-3.5" />,
  "run.cancelled": <X className="w-3.5 h-3.5" />,
  "agent.started": <Play className="w-3.5 h-3.5" />,
  "agent.model.started": <MessageSquare className="w-3.5 h-3.5" />,
  "agent.model.completed": <Check className="w-3.5 h-3.5" />,
  "agent.model.failed": <AlertTriangle className="w-3.5 h-3.5" />,
  "agent.tool.started": <Hammer className="w-3.5 h-3.5" />,
  "agent.tool.completed": <Check className="w-3.5 h-3.5" />,
  "agent.tool.failed": <AlertTriangle className="w-3.5 h-3.5" />,
  "agent.tools.disabled": <Ban className="w-3.5 h-3.5" />,
  "agent.completed": <Check className="w-3.5 h-3.5" />,
  "planner.started": <Cog className="w-3.5 h-3.5" />,
  "planner.capability_warning": <AlertTriangle className="w-3.5 h-3.5" />,
  "planner.completed": <ListChecks className="w-3.5 h-3.5" />,
  "interrupt.plan_review": <Pause className="w-3.5 h-3.5" />,
  "plan.approved": <Check className="w-3.5 h-3.5" />,
  "plan.edited": <FileCheck2 className="w-3.5 h-3.5" />,
  "plan.retrying": <RefreshCcw className="w-3.5 h-3.5" />,
  "plan.cancelled": <X className="w-3.5 h-3.5" />,
  "todo.started": <CircleDot className="w-3.5 h-3.5" />,
  "todo.completed": <Check className="w-3.5 h-3.5" />,
  "todo.failed": <AlertTriangle className="w-3.5 h-3.5" />,
  "interrupt.tool_review": <Hammer className="w-3.5 h-3.5" />,
  "tool.policy_decision": <Hammer className="w-3.5 h-3.5" />,
  "tool_review.approve": <Check className="w-3.5 h-3.5" />,
  "tool_review.modify": <FileCheck2 className="w-3.5 h-3.5" />,
  "tool_review.deny": <X className="w-3.5 h-3.5" />,
  "tool_review.cancel": <X className="w-3.5 h-3.5" />,
  "deviation_review.approve": <Check className="w-3.5 h-3.5" />,
  "deviation_review.replan": <RefreshCcw className="w-3.5 h-3.5" />,
  "deviation_review.cancel": <X className="w-3.5 h-3.5" />,
  "mcp_review.approve": <Check className="w-3.5 h-3.5" />,
  "mcp_review.deny": <X className="w-3.5 h-3.5" />,
  "mcp_review.cancel": <X className="w-3.5 h-3.5" />,
  "verification.failed": <AlertTriangle className="w-3.5 h-3.5" />,
  "run.completed": <Check className="w-3.5 h-3.5" />,
  "run.failed": <AlertTriangle className="w-3.5 h-3.5" />,
};

const TONE: Partial<Record<string, string>> = {
  "interrupt.plan_review": "text-amber-500 bg-amber-500/15",
  "planner.capability_warning": "text-amber-500 bg-amber-500/15",
  "plan.cancelled": "text-destructive bg-destructive/15",
  "agent.model.failed": "text-destructive bg-destructive/15",
  "agent.tool.failed": "text-destructive bg-destructive/15",
  "agent.tools.disabled": "text-muted-foreground bg-muted",
  "agent.completed": "text-emerald-500 bg-emerald-500/15",
  "todo.failed": "text-destructive bg-destructive/15",
  "run.failed": "text-destructive bg-destructive/15",
  "plan.approved": "text-emerald-500 bg-emerald-500/15",
  "todo.completed": "text-emerald-500 bg-emerald-500/15",
  "run.completed": "text-emerald-500 bg-emerald-500/15",
};

const STATUS_TONE: Record<TimelineStatus, string> = {
  running: "text-sky-500 bg-sky-500/15 ring-1 ring-sky-500/25",
  completed: "text-emerald-500 bg-emerald-500/15",
  failed: "text-destructive bg-destructive/15",
  waiting: "text-amber-500 bg-amber-500/15",
  cancelled: "text-muted-foreground bg-muted",
  warning: "text-amber-500 bg-amber-500/15",
  info: "text-secondary-foreground bg-secondary",
};

const STATUS_LABEL: Record<TimelineStatus, string> = {
  running: "运行中",
  completed: "成功",
  failed: "失败",
  waiting: "等待审核",
  cancelled: "已取消",
  warning: "提醒",
  info: "记录",
};

const LIFECYCLE_EVENTS: Record<string, { group: string; status: TimelineStatus }> = {
  "run.started": { group: "run", status: "running" },
  "run.completed": { group: "run", status: "completed" },
  "run.failed": { group: "run", status: "failed" },
  "run.needs_revision": { group: "run", status: "warning" },
  "run.cancelled": { group: "run", status: "cancelled" },
  "agent.started": { group: "agent", status: "running" },
  "agent.completed": { group: "agent", status: "completed" },
  "agent.model.started": { group: "agent.model", status: "running" },
  "agent.model.completed": { group: "agent.model", status: "completed" },
  "agent.model.failed": { group: "agent.model", status: "failed" },
  "agent.tool.started": { group: "agent.tool", status: "running" },
  "agent.tool.completed": { group: "agent.tool", status: "completed" },
  "agent.tool.failed": { group: "agent.tool", status: "failed" },
  "planner.started": { group: "planner", status: "running" },
  "planner.completed": { group: "planner", status: "completed" },
  "todo.started": { group: "todo", status: "running" },
  "todo.completed": { group: "todo", status: "completed" },
  "todo.failed": { group: "todo", status: "failed" },
  "tool.policy_decision": { group: "agent.tool", status: "info" },
  "interrupt.tool_review": { group: "agent.tool", status: "waiting" },
  "tool_review.approve": { group: "agent.tool", status: "completed" },
  "tool_review.modify": { group: "agent.tool", status: "completed" },
  "tool_review.deny": { group: "agent.tool", status: "cancelled" },
  "tool_review.cancel": { group: "agent.tool", status: "cancelled" },
};

const WAITING_EVENTS = new Set(["interrupt.plan_review", "interrupt.tool_review"]);
const SUCCESS_EVENTS = new Set([
  "plan.approved",
  "tool_review.approve",
  "deviation_review.approve",
  "mcp_review.approve",
]);
const CANCELLED_EVENTS = new Set([
  "plan.cancelled",
  "tool_review.cancel",
  "tool_review.deny",
  "deviation_review.cancel",
  "mcp_review.cancel",
  "mcp_review.deny",
]);

function buildTimelineItems(events: TimelineEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  const lifecycleItems = new Map<string, TimelineItem>();
  const reviewToolKeys = new Map<string, string>();

  for (const event of events) {
    const lifecycle = LIFECYCLE_EVENTS[event.kind];
    if (!lifecycle) {
      items.push(toStandaloneItem(event));
      continue;
    }

    const key = lifecycleKey(event, lifecycle.group, reviewToolKeys);
    const existing = lifecycleItems.get(key);
    if (existing) {
      existing.status = eventStatus(event, lifecycle.status);
      existing.endedAt = event.at;
      existing.kind = event.kind;
      existing.count += 1;
      if (shouldReplaceTitle(existing.title, event)) {
        existing.title = lifecycleTitle(event);
      }
      continue;
    }

    const item: TimelineItem = {
      id: event.id,
      title: lifecycleTitle(event),
      status: eventStatus(event, lifecycle.status),
      startedAt: event.at,
      endedAt: lifecycle.status === "running" ? undefined : event.at,
      kind: event.kind,
      count: 1,
    };
    lifecycleItems.set(key, item);
    items.push(item);
  }

  return items;
}

function toStandaloneItem(event: TimelineEvent): TimelineItem {
  const status = eventStatus(event);
  return {
    id: event.id,
    title: event.message,
    status,
    startedAt: event.at,
    endedAt: status === "running" ? undefined : event.at,
    kind: event.kind,
    count: 1,
  };
}

function lifecycleKey(event: TimelineEvent, group: string, reviewToolKeys: Map<string, string>): string {
  if (group === "agent.tool") {
    const toolCallId = fieldValue(event, "tool_call_id", "toolCallId") || event.correlationId;
    const reviewId = fieldValue(event, "review_id", "reviewId");
    if (toolCallId) {
      const key = `${group}:tool:${toolCallId}`;
      if (reviewId) reviewToolKeys.set(reviewId, key);
      return key;
    }
    if (reviewId && reviewToolKeys.has(reviewId)) {
      return reviewToolKeys.get(reviewId) as string;
    }
    if (reviewId) {
      const key = `${group}:review:${reviewId}`;
      reviewToolKeys.set(reviewId, key);
      return key;
    }
  }

  return [
    group,
    event.phase,
    event.correlationId,
    event.fields?.tool_call_id,
    event.fields?.toolCallId,
    event.fields?.todo_id,
    event.fields?.todoId,
    event.fields?.turn_index,
  ]
    .filter((value) => value !== undefined && value !== null && value !== "")
    .join(":");
}

function fieldValue(event: TimelineEvent, ...names: string[]): string | null {
  for (const name of names) {
    const value = event.fields?.[name];
    if (value !== undefined && value !== null && value !== "") return String(value);
  }
  return null;
}

function eventStatus(event: TimelineEvent, fallback: TimelineStatus = "info"): TimelineStatus {
  if (event.severity === "error") return "failed";
  if (event.severity === "warning") return "warning";
  if (WAITING_EVENTS.has(event.kind)) return "waiting";
  if (SUCCESS_EVENTS.has(event.kind)) return "completed";
  if (CANCELLED_EVENTS.has(event.kind)) return "cancelled";
  return fallback;
}

function lifecycleTitle(event: TimelineEvent): string {
  const title = event.message.replace(/\s+(开始|启动)\s*·\s*/, " · ");
  return title
    .replace(/\s*(开始|启动|完成|结束|失败|已取消|需要修订)([:：].*)?$/, "")
    .replace(/\s*调用工具\s*/, " 工具调用 ")
    .trim();
}

function shouldReplaceTitle(currentTitle: string, event: TimelineEvent): boolean {
  if (currentTitle.startsWith("Tool policy ")) return true;
  return event.kind === "agent.tool.started";
}

function durationLabel(item: TimelineItem): string | null {
  if (!item.endedAt || item.endedAt === item.startedAt) return null;
  const durationMs = Date.parse(item.endedAt) - Date.parse(item.startedAt);
  if (!Number.isFinite(durationMs) || durationMs <= 0) return null;
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 1 : 0)}s`;
}

function eventTime(value: string): string {
  return new Date(value).toLocaleTimeString();
}

function iconFor(item: TimelineItem): React.ReactNode {
  if (item.status === "running") return <LoaderCircle className="w-3.5 h-3.5 animate-spin" />;
  return ICONS[item.kind] ?? <CircleDot className="w-3.5 h-3.5" />;
}

export function Timeline({ events }: Props) {
  if (events.length === 0) return null;
  const items = buildTimelineItems(events);

  return (
    <ol className="relative pl-5 border-l border-border space-y-3">
      {items.map((item) => (
        <li key={item.id} className="relative">
          <span
            className={cn(
              "absolute -left-[27px] top-0 w-5 h-5 rounded-full grid place-items-center bg-secondary text-secondary-foreground",
              STATUS_TONE[item.status],
              TONE[item.kind],
            )}
          >
            {iconFor(item)}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm">{item.title}</div>
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[11px] leading-none",
                STATUS_TONE[item.status],
              )}
            >
              {STATUS_LABEL[item.status]}
            </span>
          </div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {eventTime(item.startedAt)}
            {item.endedAt && item.endedAt !== item.startedAt ? ` - ${eventTime(item.endedAt)}` : ""}
            {durationLabel(item) ? ` · ${durationLabel(item)}` : ""}
            {item.count > 1 ? ` · ${item.count} 条事件` : ""}
            {` · ${item.kind}`}
          </div>
        </li>
      ))}
    </ol>
  );
}

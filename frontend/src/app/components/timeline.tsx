import {
  AlertTriangle,
  Ban,
  Check,
  CircleDot,
  Cog,
  FileCheck2,
  Hammer,
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

const ICONS: Record<TimelineEvent["kind"], React.ReactNode> = {
  "session.created": <Sparkles className="w-3.5 h-3.5" />,
  "run.started": <Play className="w-3.5 h-3.5" />,
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
  "run.completed": <Check className="w-3.5 h-3.5" />,
  "run.failed": <AlertTriangle className="w-3.5 h-3.5" />,
};

const TONE: Partial<Record<TimelineEvent["kind"], string>> = {
  "interrupt.plan_review": "text-amber-500 bg-amber-500/15",
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

export function Timeline({ events }: Props) {
  if (events.length === 0) return null;
  return (
    <ol className="relative pl-5 border-l border-border space-y-3">
      {events.map((e) => (
        <li key={e.id} className="relative">
          <span
            className={cn(
              "absolute -left-[27px] top-0 w-5 h-5 rounded-full grid place-items-center bg-secondary text-secondary-foreground",
              TONE[e.kind],
            )}
          >
            {ICONS[e.kind]}
          </span>
          <div className="text-sm">{e.message}</div>
          <div className="text-xs text-muted-foreground">
            {new Date(e.at).toLocaleTimeString()} · {e.kind}
          </div>
        </li>
      ))}
    </ol>
  );
}

import { AlertTriangle, Check, ChevronDown, Loader2, X, CircleDashed, SkipForward } from "lucide-react";
import { Progress } from "./ui/progress";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "./ui/collapsible";
import { ExecutionLogEntry, TodoItem } from "./types";
import { cn } from "./ui/utils";
import { MarkdownContent } from "./markdown-content";

interface Props {
  todos: TodoItem[];
  log: ExecutionLogEntry[];
}

export function ExecutionView({ todos, log }: Props) {
  const total = todos.length;
  const completed = log.filter((e) => e.status === "completed").length;
  const skippedCount = log.filter((e) => e.status === "skipped").length;
  const failed = log.filter((e) => e.status === "failed").length;
  const blocked = log.filter((e) => e.status === "blocked").length;
  const inProgress = log.find((e) => e.status === "in_progress");
  const pct = total === 0 ? 0 : Math.round(((completed + skippedCount + failed + blocked) / total) * 100);

  return (
    <Collapsible defaultOpen>
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span>执行进度</span>
              <Badge variant="outline" className="text-xs">executor</Badge>
            </div>
            <CollapsibleTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7 group"
                aria-label="切换执行进度折叠状态"
              >
                <ChevronDown className="w-3.5 h-3.5 transition-transform group-data-[state=open]:rotate-180" />
              </Button>
            </CollapsibleTrigger>
          </div>
          <div className="flex items-center gap-4 mt-3 text-sm text-muted-foreground">
            <span>共 {total} 项</span>
            <span className="text-emerald-500">已完成 {completed}</span>
            {skippedCount > 0 && <span className="text-muted-foreground">跳过 {skippedCount}</span>}
            {failed > 0 && <span className="text-destructive">失败 {failed}</span>}
            {blocked > 0 && <span className="text-amber-600">受阻 {blocked}</span>}
            {inProgress && (
              <span className="flex items-center gap-1 text-primary">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                进行中：{inProgress.content}
              </span>
            )}
          </div>
          <Progress value={pct} className="mt-3 h-1.5" />
        </div>
        <CollapsibleContent>
          <ul className="divide-y divide-border">
            {todos.map((t, i) => {
              const entry = log[i];
              const status = entry?.status ?? t.status;
              const skipped = status === "skipped" || (!entry && t.status === "completed");
              return (
                <li key={i} className="px-5 py-3 flex items-start gap-3">
                  <div className="mt-0.5">
                    {skipped ? (
                      <SkipForward className="w-4 h-4 text-muted-foreground" />
                    ) : status === "completed" ? (
                      <Check className="w-4 h-4 text-emerald-500" />
                    ) : status === "in_progress" ? (
                      <Loader2 className="w-4 h-4 text-primary animate-spin" />
                    ) : status === "failed" ? (
                      <X className="w-4 h-4 text-destructive" />
                    ) : status === "blocked" ? (
                      <AlertTriangle className="w-4 h-4 text-amber-600" />
                    ) : (
                      <CircleDashed className="w-4 h-4 text-muted-foreground" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground w-5">{i + 1}.</span>
                      <span
                        className={cn(
                          "text-sm",
                          status === "failed" && "text-destructive",
                          status === "blocked" && "text-amber-700",
                        )}
                      >
                        {t.content}
                      </span>
                      {skipped && (
                        <Badge variant="secondary" className="text-xs">
                          已完成，跳过执行
                        </Badge>
                      )}
                      {status === "blocked" && (
                        <Badge variant="secondary" className="text-xs text-amber-700">
                          受阻
                        </Badge>
                      )}
                    </div>
                    {entry?.result && (
                      <MarkdownContent
                        markdown={entry.result}
                        className="text-sm text-muted-foreground mt-1 pl-7 space-y-1 [&_p]:my-0 [&_ul]:my-1 [&_ol]:my-1"
                      />
                    )}
                    {entry?.error && (
                      <MarkdownContent
                        markdown={`⚠ ${entry.error}`}
                        className="text-sm text-destructive mt-1 pl-7 space-y-1 [&_p]:my-0"
                      />
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

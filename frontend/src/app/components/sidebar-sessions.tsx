import { Plus, MessageSquare, Sparkles, ListChecks, PanelLeftClose } from "lucide-react";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { FsAgentSession } from "./types";
import { cn } from "./ui/utils";

interface Props {
  sessions: FsAgentSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onCollapse: () => void;
  overlay?: boolean;
}

const STATUS_LABEL: Record<FsAgentSession["status"], string> = {
  idle: "草稿",
  running: "运行中",
  planning: "生成计划",
  awaiting_plan_review: "待审核",
  editing_plan: "编辑中",
  retrying_plan: "重试中",
  executing: "执行中",
  awaiting_tool_review: "工具审批",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
};

const SESSION_TITLE_MAX_CHARS = 16;

export function SidebarSessions({ sessions, activeId, onSelect, onNew, onCollapse, overlay = false }: Props) {
  const grouped = groupByDate(sessions);
  return (
    <aside
      className={cn(
        "w-[260px] shrink-0 h-full min-h-0 overflow-hidden flex flex-col border-r border-border bg-sidebar text-sidebar-foreground",
        overlay && "fixed inset-y-0 left-0 z-40 w-[min(82vw,260px)] shadow-xl",
      )}
    >
      <div className="h-14 px-3 flex items-center gap-2 border-b border-border shrink-0">
        <div className="w-7 h-7 rounded-md bg-primary text-primary-foreground grid place-items-center">
          <Sparkles className="w-4 h-4" />
        </div>
        <div className="min-w-0 flex-1 truncate">fsAgent</div>
        <Button variant="ghost" size="icon" onClick={onCollapse} className="h-7 w-7">
          <PanelLeftClose className="w-4 h-4" />
        </Button>
      </div>
      <div className="p-3 shrink-0">
        <Button onClick={onNew} className="w-full justify-start gap-2" variant="secondary">
          <Plus className="w-4 h-4" />
          开启新会话
        </Button>
      </div>
      <ScrollArea className="flex-1 min-h-0 overflow-hidden">
        <div className="px-2 pb-4 space-y-4">
          {grouped.length === 0 && (
            <div className="px-2 py-8 text-center text-muted-foreground text-sm">
              暂无会话
            </div>
          )}
          {grouped.map((group) => (
            <div key={group.label}>
              <div className="px-2 py-1 text-xs text-muted-foreground">{group.label}</div>
              <div className="space-y-0.5">
                {group.items.map((s) => {
                  const title = s.message || (s.mode === "plan" ? "未命名计划" : "新对话");
                  return (
                    <button
                      key={s.sessionId}
                      onClick={() => onSelect(s.sessionId)}
                      title={title}
                      className={cn(
                        "w-full min-w-0 overflow-hidden text-left px-2 py-2 rounded-md flex items-start gap-2 hover:bg-sidebar-accent",
                        s.sessionId === activeId && "bg-sidebar-accent",
                      )}
                    >
                      {s.mode === "plan" ? (
                        <ListChecks className="w-4 h-4 mt-0.5 text-muted-foreground shrink-0" />
                      ) : (
                        <MessageSquare className="w-4 h-4 mt-0.5 text-muted-foreground shrink-0" />
                      )}
                      <div className="min-w-0 flex-1 overflow-hidden">
                        <div className="max-w-full truncate text-sm font-medium leading-5">
                          {formatSessionTitle(title)}
                        </div>
                        <div className="min-w-0 overflow-hidden text-xs text-muted-foreground flex items-center gap-1.5 whitespace-nowrap">
                          <span className="uppercase shrink-0">{s.mode}</span>
                          <span className="shrink-0">·</span>
                          <span className="truncate">{STATUS_LABEL[s.status]}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
      <div className="p-3 border-t border-border shrink-0 flex items-center gap-2">
        <div className="w-7 h-7 rounded-full bg-muted grid place-items-center text-xs">L</div>
        <div className="min-w-0 flex-1 truncate text-sm text-muted-foreground">local@fsagent</div>
      </div>
    </aside>
  );
}

function formatSessionTitle(title: string) {
  const chars = Array.from(title.trim());
  if (chars.length <= SESSION_TITLE_MAX_CHARS) return title;
  return `${chars.slice(0, SESSION_TITLE_MAX_CHARS).join("")}...`;
}

function groupByDate(sessions: FsAgentSession[]) {
  const today: FsAgentSession[] = [];
  const week: FsAgentSession[] = [];
  const earlier: FsAgentSession[] = [];
  const nowMs = Date.now();
  for (const s of sessions) {
    const diff = nowMs - new Date(s.updatedAt).getTime();
    if (diff < 24 * 3600 * 1000) today.push(s);
    else if (diff < 7 * 24 * 3600 * 1000) week.push(s);
    else earlier.push(s);
  }
  const groups = [
    { label: "今天", items: today },
    { label: "7 天内", items: week },
    { label: "更早", items: earlier },
  ].filter((g) => g.items.length > 0);
  return groups;
}

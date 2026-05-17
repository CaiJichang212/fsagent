import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  Cpu,
  PanelLeftOpen,
  Plug,
  Sparkles,
  Zap,
} from "lucide-react";
import { Toaster } from "./components/ui/sonner";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { ScrollArea } from "./components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "./components/ui/alert";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "./components/ui/collapsible";
import { SidebarSessions } from "./components/sidebar-sessions";
import { TaskComposer, ComposerSubmit } from "./components/task-composer";
import { ExecutionView } from "./components/execution-view";
import { ReportView } from "./components/report-view";
import { MarkdownContent } from "./components/markdown-content";
import { Timeline } from "./components/timeline";
import { ReviewGate } from "./components/review-gate";
import {
  FsAgentSession,
  ModelConfigItem,
  ReviewRecord,
} from "./components/types";
import { getModelConfig, streamCreateRun, streamDecideReview, type ReviewDecisionPayload } from "./api/fsagent-client";
import { toast } from "sonner";

const STATUS_LABEL: Record<FsAgentSession["status"], { label: string; tone: string }> = {
  idle: { label: "草稿", tone: "" },
  running: { label: "运行中", tone: "text-primary" },
  planning: { label: "生成计划中", tone: "text-primary" },
  awaiting_plan_review: { label: "等待审核", tone: "text-amber-500" },
  editing_plan: { label: "编辑计划", tone: "text-amber-500" },
  retrying_plan: { label: "重试计划", tone: "text-amber-500" },
  executing: { label: "执行中", tone: "text-primary" },
  awaiting_tool_review: { label: "工具审批", tone: "text-amber-500" },
  verifying: { label: "验证中", tone: "text-primary" },
  needs_revision: { label: "需修订", tone: "text-amber-500" },
  completed: { label: "已完成", tone: "text-emerald-500" },
  cancelled: { label: "已取消", tone: "text-muted-foreground" },
  failed: { label: "失败", tone: "text-destructive" },
};

export default function App() {
  const [sessions, setSessions] = useState<FsAgentSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [compactLayout, setCompactLayout] = useState(false);
  const [models, setModels] = useState<ModelConfigItem[]>([]);

  const active = useMemo(
    () => sessions.find((s) => s.sessionId === activeId) ?? null,
    [sessions, activeId],
  );

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const syncLayout = () => {
      setCompactLayout(query.matches);
      if (query.matches) setSidebarOpen(false);
    };
    syncLayout();
    query.addEventListener("change", syncLayout);
    return () => query.removeEventListener("change", syncLayout);
  }, []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("fsagent.sessions");
      if (raw) {
        const data = JSON.parse(raw) as { sessions: FsAgentSession[]; activeId: string | null };
        const restored = data.sessions.map((s) =>
          s.status === "running" || s.status === "planning" || s.status === "executing" || s.status === "verifying"
            ? { ...s, status: "failed" as const, error: "服务重启或断线，无法继续之前的运行。请重新提交任务。" }
            : s,
        );
        setSessions(restored);
        setActiveId(data.activeId);
      }
    } catch {}
  }, []);
  useEffect(() => {
    localStorage.setItem(
      "fsagent.sessions",
      JSON.stringify({ sessions, activeId }),
    );
  }, [sessions, activeId]);

  useEffect(() => {
    void getModelConfig()
      .then((config) => {
        setModels(config.models);
      })
      .catch((error) => {
        toast.error(`加载模型配置失败：${toErrorMessage(error)}`);
      });
  }, []);

  const replaceSession = (session: FsAgentSession) => {
    setSessions((arr) =>
      arr.some((s) => s.sessionId === session.sessionId)
        ? arr.map((s) => (s.sessionId === session.sessionId ? session : s))
        : [session, ...arr],
    );
  };

  const markPending = (
    id: string,
    status: FsAgentSession["status"],
    pendingReview?: ReviewRecord | null,
  ) => {
    setSessions((arr) =>
      arr.map((s) =>
        s.sessionId === id
          ? {
              ...s,
              status,
              ...(pendingReview !== undefined ? { pendingReview } : {}),
              updatedAt: new Date().toISOString(),
            }
          : s,
      ),
    );
  };

  const handleNew = () => setActiveId(null);

  const handleSubmit = async (v: ComposerSubmit) => {
    try {
      await streamCreateRun(v, (session) => {
        replaceSession(session);
        setActiveId(session.sessionId);
      });
    } catch (error) {
      toast.error(toErrorMessage(error));
    }
  };

  const submitReviewDecision = async (
    id: string,
    review: ReviewRecord,
    payload: ReviewDecisionPayload,
    pendingStatus: FsAgentSession["status"],
  ) => {
    markPending(id, pendingStatus, null);
    try {
      await streamDecideReview(id, review.id, payload, replaceSession);
    } catch (error) {
      toast.error(toErrorMessage(error));
      markPending(id, review.kind === "plan_review" ? "awaiting_plan_review" : "awaiting_tool_review", review);
    }
  };

  const onReviewDecision = (
    id: string,
    review: ReviewRecord,
    payload: ReviewDecisionPayload,
    pendingStatus: FsAgentSession["status"],
  ) => {
    void submitReviewDecision(id, review, payload, pendingStatus);
  };

  return (
    <div className="h-dvh w-full min-h-0 overflow-hidden flex bg-background text-foreground dark">
      {sidebarOpen && (
        <>
          {compactLayout && (
            <button
              aria-label="关闭侧栏遮罩"
              className="fixed inset-0 z-30 bg-background/70 md:hidden"
              onClick={() => setSidebarOpen(false)}
            />
          )}
          <SidebarSessions
            sessions={sessions}
            activeId={activeId}
            onSelect={(id) => {
              setActiveId(id);
              if (compactLayout) setSidebarOpen(false);
            }}
            onNew={() => {
              handleNew();
              if (compactLayout) setSidebarOpen(false);
            }}
            onCollapse={() => setSidebarOpen(false)}
            overlay={compactLayout}
          />
        </>
      )}
      <main className="flex-1 min-w-0 min-h-0 flex flex-col">
        <header className="h-14 border-b border-border flex items-center px-4 gap-2 shrink-0 min-w-0">
          {!sidebarOpen && (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSidebarOpen(true)}>
              <PanelLeftOpen className="w-4 h-4" />
            </Button>
          )}
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary" />
            <span>fsAgent</span>
            <span className="text-muted-foreground hidden md:inline">— Fast / Plan 双模式运行时</span>
          </div>
          <div className="flex-1" />
          {active && <SessionBadges session={active} />}
        </header>

        <ScrollArea className="flex-1 min-h-0 overflow-hidden">
          <div className="px-4 md:px-6 py-6">
            {!active ? (
              <WelcomeScreen onSubmit={handleSubmit} models={models} />
            ) : (
              <SessionView
                session={active}
                onReviewDecision={(review, payload, pendingStatus) =>
                  onReviewDecision(active.sessionId, review, payload, pendingStatus)
                }
              />
            )}
          </div>
        </ScrollArea>

        <div className="shrink-0 border-t border-border bg-background/80 backdrop-blur px-4 md:px-6 py-3">
          <div className="max-w-4xl mx-auto">
            <TaskComposer onSubmit={handleSubmit} models={models} compact />
          </div>
        </div>
      </main>
      <Toaster position="top-right" />
    </div>
  );
}

function toErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "API 请求失败";
}

function SessionBadges({ session }: { session: FsAgentSession }) {
  const status = STATUS_LABEL[session.status];
  return (
    <div className="min-w-0 flex items-center gap-2 text-sm">
      <Badge variant="outline" className="gap-1">
        {session.mode === "fast" ? <Zap className="w-3 h-3" /> : <Cpu className="w-3 h-3" />}
        {session.mode.toUpperCase()}
      </Badge>
      <Badge variant="outline" className="gap-1 hidden md:inline-flex">
        <Cpu className="w-3 h-3" />
        {session.model.split("/").pop()}
      </Badge>
      <Badge variant="outline" className="gap-1 hidden md:inline-flex">
        <Plug className="w-3 h-3" />
        MCP {session.mcpEnabled ? "已加载" : "禁用"}
      </Badge>
      <span className={"px-2 " + status.tone}>● {status.label}</span>
    </div>
  );
}

function WelcomeScreen({ onSubmit, models }: { onSubmit: (v: ComposerSubmit) => void; models: ModelConfigItem[] }) {
  const defaultModel = models[0];
  const presets: { mode: "fast" | "plan"; title: string; desc: string }[] = [
    { mode: "fast", title: "快速搜索仓库", desc: "在 fsAgent 中查找 plan_review 相关入口" },
    { mode: "plan", title: "梳理 HITL 协议", desc: "规划并产出 fsAgent Web 适配文档" },
    { mode: "plan", title: "重构建议", desc: "分析依赖关系并给出可执行的优化项" },
  ];
  return (
    <div className="min-h-[calc(100dvh-12rem)] flex flex-col items-center justify-center gap-8">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary text-primary-foreground grid place-items-center">
          <Sparkles className="w-5 h-5" />
        </div>
        <div>
          <h1>使用 fsagent 开始任务</h1>
          <p className="text-muted-foreground mt-1">Fast 模式快速回答；Plan 模式生成计划并经你确认后执行。</p>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 max-w-2xl w-full">
        {presets.map((s, i) => (
          <button
            key={i}
            className="text-left rounded-lg border border-border p-3 hover:border-primary/50 hover:bg-accent/40 transition-colors"
            onClick={() =>
              defaultModel &&
              onSubmit({
                message: s.desc,
                mode: s.mode,
                model: defaultModel.name,
                thinking: defaultModel.defaultThinkingEnabled,
                mcpEnabled: false,
                trustProjectMcp: false,
                mcpConfigPath: "mcp.json",
              })
            }
            disabled={!defaultModel}
          >
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground uppercase">
              {s.mode === "fast" ? <Zap className="w-3 h-3" /> : <Cpu className="w-3 h-3" />}
              {s.mode}
            </div>
            <div className="mt-1.5 text-sm">{s.title}</div>
            <div className="text-xs text-muted-foreground mt-0.5">{s.desc}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function SessionView({
  session,
  onReviewDecision,
}: {
  session: FsAgentSession;
  onReviewDecision: (review: ReviewRecord, payload: ReviewDecisionPayload, pendingStatus: FsAgentSession["status"]) => void;
}) {
  const showExecution =
    session.mode === "plan" &&
    session.todos.length > 0 &&
    (session.executionLog.length > 0 ||
      session.status === "executing" ||
      session.status === "awaiting_tool_review" ||
      session.status === "verifying" ||
      session.status === "needs_revision" ||
      session.status === "completed" ||
      session.status === "failed");

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div className="rounded-xl border border-border bg-card px-5 py-4">
        <div className="text-xs text-muted-foreground">用户输入</div>
        <p className="mt-1 whitespace-pre-wrap">{session.message}</p>
      </div>

      {session.status === "failed" && session.error && (
        <Alert variant="destructive">
          <AlertTitle>运行失败</AlertTitle>
          <AlertDescription>{session.error}</AlertDescription>
        </Alert>
      )}

      {session.pendingReview && (
        <ReviewGate
          review={session.pendingReview}
          todos={session.todos}
          planMeta={session.planMeta}
          onDecision={onReviewDecision}
        />
      )}

      {showExecution && (
        <ExecutionView todos={session.todos} log={session.executionLog} />
      )}

      {session.finalResponse && session.mode === "plan" && (
        <ReportView markdown={session.finalResponse} />
      )}

      {session.finalResponse && session.mode === "fast" && (
        <div className="rounded-xl border border-border bg-card px-5 py-4">
          <MarkdownContent markdown={session.finalResponse} className="text-sm space-y-3" />
        </div>
      )}

      <Collapsible defaultOpen>
        <CollapsibleTrigger asChild>
          <button className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ChevronDown className="w-3.5 h-3.5" />
            运行日志（{session.timeline.length}）
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-3">
          <Timeline events={session.timeline} />
        </CollapsibleContent>
      </Collapsible>

    </div>
  );
}

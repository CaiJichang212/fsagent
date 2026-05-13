import { useState } from "react";
import {
  Check,
  ChevronDown,
  CircleDashed,
  GripVertical,
  Loader2,
  Pencil,
  Plus,
  RefreshCcw,
  Trash2,
  X,
  AlertTriangle,
} from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Textarea } from "./ui/textarea";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "./ui/collapsible";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "./ui/alert-dialog";
import { TodoItem, PlanMeta, TodoStatus } from "./types";
import { cn } from "./ui/utils";

interface Props {
  todos: TodoItem[];
  planMeta: PlanMeta;
  instructions: string;
  allowedActions: ("approve" | "edit" | "retry" | "cancel")[];
  onApprove: () => void;
  onEdit: (todos: TodoItem[], planMeta: PlanMeta) => void;
  onRetry: (feedback: string) => void;
  onCancel: (reason: string) => void;
  locked?: boolean;
}

export function PlanReviewPanel({
  todos,
  planMeta,
  instructions,
  allowedActions,
  onApprove,
  onEdit,
  onRetry,
  onCancel,
  locked,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [draftTodos, setDraftTodos] = useState<TodoItem[]>(todos);
  const [draftMeta, setDraftMeta] = useState<PlanMeta>(planMeta);
  const [feedback, setFeedback] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const startEdit = () => {
    setDraftTodos(todos.map((t) => ({ ...t })));
    setDraftMeta({
      goal: planMeta.goal,
      assumptions: [...(planMeta.assumptions ?? [])],
      verification: [...(planMeta.verification ?? [])],
      final_output_format: planMeta.final_output_format,
    });
    setEditing(true);
  };

  const updateTodo = (i: number, patch: Partial<TodoItem>) => {
    setDraftTodos((arr) => arr.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  };
  const addTodo = () => setDraftTodos((arr) => [...arr, { content: "", status: "pending" }]);
  const removeTodo = (i: number) => setDraftTodos((arr) => arr.filter((_, idx) => idx !== i));
  const moveTodo = (i: number, dir: -1 | 1) => {
    setDraftTodos((arr) => {
      const j = i + dir;
      if (j < 0 || j >= arr.length) return arr;
      const next = arr.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  };

  const saveEdit = () => {
    if (draftTodos.length === 0) {
      setValidationError("至少保留一个 todo。");
      return;
    }
    if (draftTodos.some((t) => !t.content.trim())) {
      setValidationError("每个 todo 内容不能为空。");
      return;
    }
    if (!draftMeta.goal.trim()) {
      setValidationError("计划目标不能为空。");
      return;
    }
    setValidationError(null);
    onEdit(
      draftTodos.map((t) => ({ content: t.content.trim(), status: t.status })),
      {
        goal: draftMeta.goal.trim(),
        assumptions: (draftMeta.assumptions ?? []).map((a) => a.trim()).filter(Boolean),
        verification: (draftMeta.verification ?? []).map((item) => item.trim()).filter(Boolean),
        final_output_format: draftMeta.final_output_format?.trim() || "markdown",
      },
    );
    setEditing(false);
  };

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-start gap-3">
        <div className="w-8 h-8 rounded-lg bg-amber-500/15 text-amber-500 grid place-items-center">
          <AlertTriangle className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span>计划审核</span>
            <Badge variant="outline" className="text-xs">plan_review</Badge>
            {locked && <Badge className="text-xs">已确认</Badge>}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{instructions}</p>
        </div>
      </div>

      <div className="px-5 py-4 space-y-4">
        {!editing ? (
          <ReadOnlyMeta meta={planMeta} />
        ) : (
          <EditMeta meta={draftMeta} onChange={setDraftMeta} />
        )}

        <Collapsible defaultOpen>
          <div className="flex items-center justify-between mb-2">
            <Label className="text-sm">Todos</Label>
            <div className="flex items-center gap-1.5">
              {editing && (
                <Button size="sm" variant="ghost" onClick={addTodo} className="gap-1">
                  <Plus className="w-3.5 h-3.5" />
                  插入步骤
                </Button>
              )}
              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 group"
                  aria-label="切换 Todos 折叠状态"
                >
                  <ChevronDown className="w-3.5 h-3.5 transition-transform group-data-[state=open]:rotate-180" />
                </Button>
              </CollapsibleTrigger>
            </div>
          </div>
          <CollapsibleContent>
            <ol className="space-y-1.5">
              {(editing ? draftTodos : todos).map((t, i) => (
                <li
                  key={i}
                  className="rounded-md border border-border bg-background/50 px-3 py-2 flex items-start gap-2"
                >
                  <div className="text-muted-foreground pt-0.5">
                    {editing ? <GripVertical className="w-4 h-4" /> : <StatusIcon status={t.status} />}
                  </div>
                  <span className="text-xs text-muted-foreground pt-0.5 w-5">{i + 1}.</span>
                  {!editing ? (
                    <div className="flex-1 min-w-0">
                      <div className="text-sm">{t.content}</div>
                    </div>
                  ) : (
                    <div className="flex-1 min-w-0 space-y-2">
                      <Input
                        value={t.content}
                        onChange={(e) => updateTodo(i, { content: e.target.value })}
                        placeholder="任务内容"
                        className="h-8"
                      />
                      <div className="flex items-center gap-1.5">
                        <StatusSelect
                          value={t.status}
                          onChange={(s) => updateTodo(i, { status: s })}
                        />
                        <div className="flex-1" />
                        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => moveTodo(i, -1)}>↑</Button>
                        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => moveTodo(i, 1)}>↓</Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-destructive"
                          onClick={() => removeTodo(i)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  )}
                  {!editing && (
                    <Badge variant="secondary" className="text-xs capitalize">
                      {t.status}
                    </Badge>
                  )}
                </li>
              ))}
            </ol>
          </CollapsibleContent>
        </Collapsible>

        {validationError && (
          <div className="text-sm text-destructive">{validationError}</div>
        )}

        {retrying && (
          <div className="space-y-2">
            <Label className="text-sm">反馈给 planner</Label>
            <Textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="例如：计划太粗，请拆成更小的前端交互步骤。可留空直接重试。"
            />
          </div>
        )}
      </div>

      {!locked && (
        <div className="px-5 py-3 border-t border-border bg-muted/30 flex flex-wrap items-center gap-2 justify-end">
          {editing ? (
            <>
              <Button variant="ghost" onClick={() => setEditing(false)}>
                放弃修改
              </Button>
              <Button onClick={saveEdit} className="gap-1.5">
                <Check className="w-4 h-4" />
                保存并提交
              </Button>
            </>
          ) : retrying ? (
            <>
              <Button variant="ghost" onClick={() => setRetrying(false)}>
                取消
              </Button>
              <Button
                onClick={() => {
                  onRetry(feedback);
                  setRetrying(false);
                  setFeedback("");
                }}
                className="gap-1.5"
              >
                <RefreshCcw className="w-4 h-4" />
                重新生成计划
              </Button>
            </>
          ) : (
            <>
              <CancelButton enabled={allowedActions.includes("cancel")} onCancel={onCancel} />
              {allowedActions.includes("retry") && (
                <Button variant="outline" onClick={() => setRetrying(true)} className="gap-1.5">
                  <RefreshCcw className="w-4 h-4" />
                  重试
                </Button>
              )}
              {allowedActions.includes("edit") && (
                <Button variant="outline" onClick={startEdit} className="gap-1.5">
                  <Pencil className="w-4 h-4" />
                  编辑
                </Button>
              )}
              {allowedActions.includes("approve") && (
                <Button onClick={onApprove} className="gap-1.5">
                  <Check className="w-4 h-4" />
                  批准并执行
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ReadOnlyMeta({ meta }: { meta: PlanMeta }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      <Field label="目标" value={meta.goal} className="md:col-span-3" />
      <Field
        label="假设"
        value={meta.assumptions?.length ? meta.assumptions.join("；") : "—"}
        className="md:col-span-2"
      />
      <Field label="输出格式" value={meta.final_output_format ?? "markdown"} />
    </div>
  );
}

function EditMeta({ meta, onChange }: { meta: PlanMeta; onChange: (m: PlanMeta) => void }) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">目标</Label>
        <Input
          value={meta.goal}
          onChange={(e) => onChange({ ...meta, goal: e.target.value })}
          className="h-9"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">假设（每行一条）</Label>
        <Textarea
          value={(meta.assumptions ?? []).join("\n")}
          onChange={(e) =>
            onChange({ ...meta, assumptions: e.target.value.split("\n").map((s) => s) })
          }
          rows={3}
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">输出格式</Label>
        <Input
          value={meta.final_output_format ?? "markdown"}
          onChange={(e) => onChange({ ...meta, final_output_format: e.target.value })}
          className="h-9"
        />
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={cn("rounded-md border border-border bg-background/50 px-3 py-2", className)}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm mt-0.5 break-words">{value}</div>
    </div>
  );
}

function StatusSelect({
  value,
  onChange,
}: {
  value: TodoStatus;
  onChange: (s: TodoStatus) => void;
}) {
  // 编辑态建议只允许 pending 或保留 completed
  const options: TodoStatus[] = value === "completed" ? ["pending", "completed"] : ["pending"];
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as TodoStatus)}
      className="h-7 px-2 rounded-md border border-border bg-background text-xs"
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function StatusIcon({ status }: { status: TodoStatus }) {
  if (status === "completed")
    return <Check className="w-4 h-4 text-emerald-500" />;
  if (status === "in_progress")
    return <Loader2 className="w-4 h-4 text-primary animate-spin" />;
  if (status === "failed") return <X className="w-4 h-4 text-destructive" />;
  return <CircleDashed className="w-4 h-4 text-muted-foreground" />;
}

function CancelButton({
  enabled,
  onCancel,
}: {
  enabled: boolean;
  onCancel: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  if (!enabled) return null;
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" className="text-destructive gap-1.5">
          <X className="w-4 h-4" />
          取消
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>取消本次计划？</AlertDialogTitle>
          <AlertDialogDescription>
            取消后会话将进入 cancelled 状态，executor 不会运行。可填写取消原因。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          placeholder="可选：取消原因"
        />
        <AlertDialogFooter>
          <AlertDialogCancel>返回</AlertDialogCancel>
          <AlertDialogAction onClick={() => onCancel(reason)}>确认取消</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

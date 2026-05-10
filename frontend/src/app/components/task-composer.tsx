import { useEffect, useState } from "react";
import { Zap, Brain, Paperclip, ArrowUp, Settings2, Plug } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Switch } from "./ui/switch";
import { Label } from "./ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "./ui/popover";
import { cn } from "./ui/utils";
import { ModelConfigItem, RuntimeMode } from "./types";

const CONTROL_SELECTED_CLASSES =
  "!border-blue-500 !bg-blue-500/15 !text-blue-400 hover:!border-blue-500 hover:!bg-blue-500/20 hover:!text-blue-400 active:!bg-blue-500/25";
const CONTROL_UNSELECTED_CLASSES =
  "!border-border !bg-background !text-foreground hover:!border-border hover:!bg-background hover:!text-foreground active:!bg-background";

export interface ComposerSubmit {
  message: string;
  mode: RuntimeMode;
  model: string;
  thinking: boolean;
  mcpEnabled: boolean;
  trustProjectMcp: boolean;
  mcpConfigPath: string;
}

interface Props {
  onSubmit: (v: ComposerSubmit) => void;
  models?: ModelConfigItem[];
  defaultMode?: RuntimeMode;
  compact?: boolean;
}

export function TaskComposer({ onSubmit, models = [], defaultMode = "fast", compact }: Props) {
  const [mode, setMode] = useState<RuntimeMode>(defaultMode);
  const [message, setMessage] = useState("");
  const [model, setModel] = useState(models[0]?.name ?? "");
  const [thinking, setThinking] = useState(false);
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [trustProjectMcp, setTrustProjectMcp] = useState(false);
  const [mcpConfigPath, setMcpConfigPath] = useState("mcp.json");

  const modelInfo = models.find((m) => m.name === model) ?? models[0];
  const thinkingDisabled = !modelInfo || !(modelInfo.supportsThinking && modelInfo.thinkingSwitchType === "toggle");
  const effectiveThinking = modelInfo ? resolveThinking(modelInfo, thinking) : false;

  useEffect(() => {
    if (!models.length) {
      setModel("");
      setThinking(false);
      return;
    }
    if (!models.some((item) => item.name === model)) {
      const first = models[0];
      setModel(first.name);
      setThinking(first.defaultThinkingEnabled);
    }
  }, [model, models]);

  const trimmed = message.trim();
  const canSubmit = trimmed.length > 0 && Boolean(modelInfo);

  const submit = () => {
    if (!canSubmit) return;
    onSubmit({
      message: trimmed,
      mode,
      model,
      thinking: effectiveThinking,
      mcpEnabled,
      trustProjectMcp,
      mcpConfigPath,
    });
    setMessage("");
  };

  return (
    <div
      className={cn(
        "rounded-2xl border border-border bg-input-background/60 backdrop-blur p-3 shadow-sm",
        compact ? "max-w-3xl mx-auto" : "w-full max-w-2xl",
      )}
    >
      <Textarea
        placeholder={
          mode === "fast"
            ? "向 fsagent 发送消息（Fast 模式：最多一轮工具调用）"
            : "描述要规划的复杂任务（Plan 模式：先生成计划再确认执行）"
        }
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
        className="min-h-[72px] max-h-[260px] resize-none border-0 bg-transparent focus-visible:ring-0 focus-visible:ring-offset-0 px-1"
      />
      <div className="flex flex-wrap items-center gap-2 pt-2">
        <div className="flex items-center rounded-full border border-border p-0.5 bg-background">
          <ModeChip
            active={mode === "fast"}
            onClick={() => setMode("fast")}
            icon={<Zap className="w-3.5 h-3.5" />}
            label="Fast"
          />
          <ModeChip
            active={mode === "plan"}
            onClick={() => setMode("plan")}
            icon={<Brain className="w-3.5 h-3.5" />}
            label="Plan"
          />
        </div>

        <Select
          value={model}
          onValueChange={(value) => {
            const next = models.find((item) => item.name === value);
            setModel(value);
            setThinking(next?.defaultThinkingEnabled ?? false);
          }}
          disabled={!models.length}
        >
          <SelectTrigger className="h-8 rounded-full bg-background w-auto max-w-[11rem] gap-1 px-3">
            <SelectValue placeholder="加载模型..." />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.name} value={m.name}>
                {m.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button
          variant="outline"
          size="sm"
          className={cn(
            "h-8 rounded-full gap-1.5",
            effectiveThinking ? CONTROL_SELECTED_CLASSES : CONTROL_UNSELECTED_CLASSES,
          )}
          aria-pressed={effectiveThinking}
          disabled={thinkingDisabled}
          onClick={() => setThinking((v) => !v)}
          title={thinkingDisabled ? "当前模型不支持 Thinking 切换" : undefined}
        >
          <Brain className="w-3.5 h-3.5" />
          深度思考
        </Button>

        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={cn(
                "h-8 rounded-full gap-1.5",
                mcpEnabled ? CONTROL_SELECTED_CLASSES : CONTROL_UNSELECTED_CLASSES,
              )}
              aria-pressed={mcpEnabled}
            >
              <Plug className="w-3.5 h-3.5" />
              MCP
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="mcp-toggle">启用 MCP</Label>
              <Switch id="mcp-toggle" checked={mcpEnabled} onCheckedChange={setMcpEnabled} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">配置文件路径</Label>
              <input
                value={mcpConfigPath}
                onChange={(e) => setMcpConfigPath(e.target.value)}
                disabled={!mcpEnabled}
                className="w-full h-8 px-2 rounded-md border border-border bg-background text-sm disabled:opacity-50"
                placeholder="mcp.json"
              />
            </div>
            <div className="flex items-start justify-between gap-2 pt-1">
              <div className="space-y-0.5">
                <Label htmlFor="trust-stdio" className="text-sm">
                  信任 stdio MCP
                </Label>
                <p className="text-xs text-muted-foreground">
                  开启后将允许执行本地命令，请仅对可信项目启用。
                </p>
              </div>
              <Switch
                id="trust-stdio"
                disabled={!mcpEnabled}
                checked={trustProjectMcp}
                onCheckedChange={setTrustProjectMcp}
              />
            </div>
          </PopoverContent>
        </Popover>

        <div className="flex-1" />

        <Button variant="ghost" size="icon" className="h-8 w-8" disabled>
          <Paperclip className="w-4 h-4" />
        </Button>
        <Button
          size="icon"
          className="h-8 w-8 rounded-full"
          onClick={submit}
          disabled={!canSubmit}
          title={mode === "fast" ? "快速运行 (⌘/Ctrl+Enter)" : "生成计划 (⌘/Ctrl+Enter)"}
        >
          <ArrowUp className="w-4 h-4" />
        </Button>
      </div>
    </div>
  );
}

function resolveThinking(model: ModelConfigItem, requested: boolean) {
  if (model.thinkingSwitchType === "toggle" && model.supportsThinking) return requested;
  return model.defaultThinkingEnabled;
}

function ModeChip({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "h-7 px-3 rounded-full border border-transparent inline-flex items-center gap-1.5 text-sm transition-colors",
        active
          ? CONTROL_SELECTED_CLASSES
          : "!text-muted-foreground hover:!text-foreground",
      )}
      aria-pressed={active}
    >
      {icon}
      {label}
    </button>
  );
}

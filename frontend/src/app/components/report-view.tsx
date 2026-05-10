import { useState } from "react";
import { Copy, Download, Eye, Code2 } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { toast } from "sonner";
import { MarkdownContent } from "./markdown-content";

interface Props {
  markdown: string;
}

export function ReportView({ markdown }: Props) {
  const [view, setView] = useState<"rendered" | "raw">("rendered");

  const copy = async () => {
    await navigator.clipboard.writeText(markdown);
    toast.success("Markdown 已复制");
  };
  const download = () => {
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `fsagent_report_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-5 py-3 border-b border-border flex items-center gap-2">
        <span>最终报告</span>
        <Badge variant="outline" className="text-xs">formatter</Badge>
        <div className="flex-1" />
        <div className="flex items-center rounded-md border border-border p-0.5">
          <button
            onClick={() => setView("rendered")}
            className={
              "h-7 px-2 rounded-sm text-xs inline-flex items-center gap-1 " +
              (view === "rendered" ? "bg-secondary" : "text-muted-foreground")
            }
          >
            <Eye className="w-3.5 h-3.5" />
            预览
          </button>
          <button
            onClick={() => setView("raw")}
            className={
              "h-7 px-2 rounded-sm text-xs inline-flex items-center gap-1 " +
              (view === "raw" ? "bg-secondary" : "text-muted-foreground")
            }
          >
            <Code2 className="w-3.5 h-3.5" />
            源码
          </button>
        </div>
        <Button variant="ghost" size="sm" onClick={copy} className="gap-1.5">
          <Copy className="w-3.5 h-3.5" />
          复制
        </Button>
        <Button variant="ghost" size="sm" onClick={download} className="gap-1.5">
          <Download className="w-3.5 h-3.5" />
          下载 .md
        </Button>
      </div>
      {view === "rendered" ? (
        <MarkdownContent markdown={markdown} className="px-6 py-5 text-sm space-y-3" />
      ) : (
        <pre className="px-6 py-5 text-xs whitespace-pre-wrap break-words bg-muted/30">
          {markdown}
        </pre>
      )}
    </div>
  );
}

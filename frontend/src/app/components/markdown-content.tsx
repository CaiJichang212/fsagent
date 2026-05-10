import { useMemo } from "react";
import { cn } from "./ui/utils";
import { renderMarkdown } from "./markdown-renderer.js";

interface Props {
  markdown: string;
  className?: string;
}

export function MarkdownContent({ markdown, className }: Props) {
  const html = useMemo(() => renderMarkdown(markdown), [markdown]);

  return (
    <div
      className={cn("max-w-none leading-relaxed [&_strong]:font-semibold", className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

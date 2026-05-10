const TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;

export function renderMarkdown(markdown) {
  const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let listType = null;
  let inCodeFence = false;
  let codeFenceLines = [];

  const closeList = () => {
    if (!listType) return;
    out.push(`</${listType}>`);
    listType = null;
  };

  const openList = (type) => {
    if (listType === type) return;
    closeList();
    out.push(`<${type} class="${type === "ol" ? "list-decimal" : "list-disc"} pl-6 space-y-1">`);
    listType = type;
  };

  const closeCodeFence = () => {
    out.push(
      `<pre class="my-3 overflow-x-auto rounded-md border border-border bg-muted/40 p-3 text-xs"><code>${escapeHtml(
        codeFenceLines.join("\n"),
      )}</code></pre>`,
    );
    codeFenceLines = [];
    inCodeFence = false;
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      closeList();
      if (inCodeFence) {
        closeCodeFence();
      } else {
        inCodeFence = true;
        codeFenceLines = [];
      }
      continue;
    }

    if (inCodeFence) {
      codeFenceLines.push(line);
      continue;
    }

    if (trimmed === "") {
      closeList();
      continue;
    }

    if (isTableStart(lines, i)) {
      closeList();
      const header = splitTableRow(lines[i]);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      i -= 1;
      out.push(renderTable(header, rows));
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 6);
      const className = level <= 3 ? "mt-5 mb-2" : "mt-4 mb-2";
      out.push(`<h${level} class="${className}">${renderInline(heading[2])}</h${level}>`);
      continue;
    }

    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
    if (ordered) {
      openList("ol");
      out.push(`<li>${renderInline(ordered[1])}</li>`);
      continue;
    }

    const unordered = /^\s*[-*]\s+(.+)$/.exec(line);
    if (unordered) {
      openList("ul");
      out.push(renderListItem(unordered[1]));
      continue;
    }

    closeList();
    out.push(`<p>${renderInline(line)}</p>`);
  }

  if (inCodeFence) closeCodeFence();
  closeList();
  return out.join("\n");
}

function renderListItem(value) {
  let text = value;
  let className = "";
  let prefix = "";
  const state = /^(\[[ xX!\-]\])\s+/.exec(text);
  if (state) {
    text = text.slice(state[0].length);
    if (/^\[[xX]\]$/.test(state[1])) {
      className = ' class="text-emerald-400"';
      prefix = "✓ ";
    } else if (state[1] === "[!]") {
      className = ' class="text-red-400"';
      prefix = "⚠ ";
    } else if (state[1] === "[-]") {
      className = ' class="text-muted-foreground"';
      prefix = "↷ ";
    } else {
      prefix = "○ ";
    }
  }
  return `<li${className}>${prefix}${renderInline(text)}</li>`;
}

function renderTable(header, rows) {
  const head = header.map((cell) => `<th class="border border-border px-2 py-1 text-left">${renderInline(cell)}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${row.map((cell) => `<td class="border border-border px-2 py-1">${renderInline(cell)}</td>`).join("")}</tr>`,
    )
    .join("");
  return `<div class="my-3 overflow-x-auto"><table class="w-full border-collapse text-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function isTableStart(lines, index) {
  return isTableRow(lines[index]) && TABLE_SEPARATOR_RE.test(lines[index + 1] ?? "");
}

function isTableRow(line) {
  return /^\s*\|.+\|\s*$/.test(line);
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderInline(value) {
  const code = [];
  let html = escapeHtml(value).replace(/`([^`]+)`/g, (_, content) => {
    const token = `@@CODE_${code.length}@@`;
    code.push(`<code class="rounded bg-muted px-1 py-0.5">${content}</code>`);
    return token;
  });
  html = html
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>");
  return code.reduce((current, snippet, index) => current.replace(`@@CODE_${index}@@`, snippet), html);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

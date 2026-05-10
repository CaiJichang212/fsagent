import test from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "../src/app/components/markdown-renderer.js";

test("renders common markdown syntax used by execution results", () => {
  const html = renderMarkdown(
    [
      "## Result",
      "**结果：ls 工具测试成功**",
      "- **功能**：列出指定目录下的所有文件",
      "- **测试**：`ls(path=\"/\")` 执行成功，返回空列表 `[]`",
      "1. 使用 `write_file` 创建文件",
      "2. 使用 `read_file` 读取文件",
    ].join("\n"),
  );

  assert.match(html, /<h3[^>]*>Result<\/h3>/);
  assert.match(html, /<strong>结果：ls 工具测试成功<\/strong>/);
  assert.match(html, /<strong>功能<\/strong>：列出指定目录下的所有文件/);
  assert.match(html, /<code[^>]*>ls\(path=&quot;\/&quot;\)<\/code>/);
  assert.match(html, /<ol[^>]*>[\s\S]*<li>使用 <code[^>]*>write_file<\/code> 创建文件<\/li>[\s\S]*<\/ol>/);
  assert.doesNotMatch(html, /\*\*结果/);
  assert.doesNotMatch(html, /`ls\(path/);
});

test("renders markdown tables and escapes unsafe html", () => {
  const html = renderMarkdown(
    [
      "| 工具 | 状态 |",
      "|------|------|",
      "| **ls** | ✅ 正常 |",
      "<script>alert(1)</script>",
    ].join("\n"),
  );

  assert.match(html, /<table[^>]*>[\s\S]*<strong>ls<\/strong>[\s\S]*<\/table>/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

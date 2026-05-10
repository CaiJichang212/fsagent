import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const planReviewSource = readFileSync(resolve(__dirname, "../src/app/components/plan-review-panel.tsx"), "utf8");
const executionViewSource = readFileSync(resolve(__dirname, "../src/app/components/execution-view.tsx"), "utf8");

test("plan review todos are rendered in a collapsible section", () => {
  assert.match(planReviewSource, /ChevronDown/);
  assert.match(planReviewSource, /Collapsible,\s*CollapsibleContent,\s*CollapsibleTrigger/s);
  assert.match(planReviewSource, /<Collapsible defaultOpen>/);
  assert.match(planReviewSource, /aria-label="切换 Todos 折叠状态"/);
  assert.match(
    planReviewSource,
    /<CollapsibleContent[^>]*>\s*<ol className="space-y-1\.5">/s,
  );
});

test("execution progress details are rendered in a collapsible section", () => {
  assert.match(executionViewSource, /ChevronDown/);
  assert.match(executionViewSource, /Collapsible,\s*CollapsibleContent,\s*CollapsibleTrigger/s);
  assert.match(executionViewSource, /<Collapsible defaultOpen>/);
  assert.match(executionViewSource, /aria-label="切换执行进度折叠状态"/);
  assert.match(
    executionViewSource,
    /<CollapsibleContent>\s*<ul className="divide-y divide-border">/s,
  );
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const typesSource = readFileSync(resolve(__dirname, "../src/app/components/types.ts"), "utf8");
const clientSource = readFileSync(resolve(__dirname, "../src/app/api/fsagent-client.ts"), "utf8");
const appSource = readFileSync(resolve(__dirname, "../src/app/App.tsx"), "utf8");
const gateSource = readFileSync(resolve(__dirname, "../src/app/components/review-gate.tsx"), "utf8");
const planReviewSource = readFileSync(resolve(__dirname, "../src/app/components/plan-review-panel.tsx"), "utf8");

test("frontend models generic review snapshots from the API", () => {
  assert.match(typesSource, /export type ReviewKind =/);
  assert.match(typesSource, /"deviation_review"/);
  assert.match(typesSource, /pendingReview\?: ReviewRecord \| null/);
  assert.match(typesSource, /toolCalls: ToolCallRecord\[\]/);
  assert.match(typesSource, /verification: VerificationRecord\[\]/);
  assert.match(typesSource, /"verifying"/);
  assert.match(typesSource, /"needs_revision"/);
});

test("generic review decisions use the review-id decision stream endpoint", () => {
  assert.match(clientSource, /ReviewDecisionPayload/);
  assert.match(clientSource, /streamDecideReview/);
  assert.match(clientSource, /reviews\/\$\{reviewId\}\/decision\/stream/);
  assert.match(appSource, /pendingReview/);
  assert.match(appSource, /ReviewGate/);
  assert.match(gateSource, /tool_review/);
  assert.match(gateSource, /deviation_review/);
  assert.match(gateSource, /mcp_review/);
  assert.match(gateSource, /editedSubject:\s*\{\s*decisions/s);
});

test("tool review gate supports per-action batch decisions", () => {
  assert.doesNotMatch(gateSource, /actionRequests\.length === 1/);
  assert.match(gateSource, /editedSubject:\s*\{\s*decisions/s);
  assert.match(gateSource, /type:\s*"edit"/);
  assert.match(gateSource, /decision\.type === "reject"/);
  assert.match(gateSource, /decision\.type === "respond"/);
});

test("plan review edits preserve verification suggestions", () => {
  assert.match(typesSource, /verification\?: string\[\]/);
  assert.match(planReviewSource, /verification:\s*\[\.\.\.\(planMeta\.verification \?\? \[\]\)\]/);
  assert.match(planReviewSource, /verification:\s*\(draftMeta\.verification \?\? \[\]\)/);
});

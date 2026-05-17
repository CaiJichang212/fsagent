import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const typesSource = readFileSync(resolve(__dirname, "../src/app/components/types.ts"), "utf8");
const timelineSource = readFileSync(resolve(__dirname, "../src/app/components/timeline.tsx"), "utf8");

test("planner capability warnings are represented in frontend timeline events", () => {
  assert.match(typesSource, /\|\s*"planner\.capability_warning"/);
  assert.match(timelineSource, /"planner\.capability_warning":\s*<AlertTriangle/);
  assert.match(timelineSource, /"planner\.capability_warning":\s*"text-amber-500 bg-amber-500\/15"/);
});

test("timeline lifecycle events are collapsed into stateful visual items", () => {
  assert.match(timelineSource, /function buildTimelineItems/);
  assert.match(timelineSource, /LIFECYCLE_EVENTS/);
  assert.match(timelineSource, /status:\s*"running"/);
  assert.match(timelineSource, /status:\s*"completed"/);
  assert.match(timelineSource, /status:\s*"failed"/);
  assert.match(timelineSource, /return\s*"waiting"/);
  assert.match(timelineSource, /animate-spin/);
  assert.doesNotMatch(timelineSource, /events\.map\(\(e\)/);
});

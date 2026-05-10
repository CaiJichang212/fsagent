import { readFile } from "node:fs/promises";
import test from "node:test";
import assert from "node:assert/strict";

const source = await readFile(
  new URL("../src/app/components/task-composer.tsx", import.meta.url),
  "utf8",
);

test("selected composer controls use the shared blue selected-state classes", () => {
  const selectedState =
    "!border-blue-500 !bg-blue-500/15 !text-blue-400 hover:!border-blue-500 hover:!bg-blue-500/20 hover:!text-blue-400 active:!bg-blue-500/25";
  const unselectedState =
    "!border-border !bg-background !text-foreground hover:!border-border hover:!bg-background hover:!text-foreground active:!bg-background";

  assert.match(
    source,
    /TaskComposer\(\{ onSubmit, models = \[\], defaultMode = "fast", compact \}/,
    "composer should tolerate missing model config while API data is loading",
  );
  assert.match(
    source,
    /effectiveThinking\s*\?\s*CONTROL_SELECTED_CLASSES\s*:\s*CONTROL_UNSELECTED_CLASSES/,
    "thinking button should use explicit selected and unselected states",
  );
  assert.match(
    source,
    /mcpEnabled\s*\?\s*CONTROL_SELECTED_CLASSES\s*:\s*CONTROL_UNSELECTED_CLASSES/,
    "MCP enabled state should use blue selected-state classes",
  );
  assert.match(
    source,
    /active\s*\?\s*CONTROL_SELECTED_CLASSES\s*:\s*"!text-muted-foreground hover:!text-foreground"/,
    "Fast/Plan selected chips should use blue selected-state classes",
  );
  assert.ok(source.includes(selectedState), "expected shared selected-state class sequence");
  assert.ok(source.includes(unselectedState), "expected explicit non-blue hover state for disabled toggles");
});

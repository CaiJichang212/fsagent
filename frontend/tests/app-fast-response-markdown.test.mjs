import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, "../src/app/App.tsx"), "utf8");

test("fast final responses are rendered through MarkdownContent", () => {
  assert.match(appSource, /import \{ MarkdownContent \} from "\.\/components\/markdown-content";/);
  assert.match(
    appSource,
    /\{session\.finalResponse && session\.mode === "fast" && \(\s*<div[^>]*>\s*<MarkdownContent markdown=\{session\.finalResponse\}/s,
  );
});

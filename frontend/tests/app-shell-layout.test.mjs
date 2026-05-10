import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, "../src/app/App.tsx"), "utf8");
const themeSource = readFileSync(resolve(__dirname, "../src/styles/theme.css"), "utf8");
const sidebarSource = readFileSync(resolve(__dirname, "../src/app/components/sidebar-sessions.tsx"), "utf8");
const composerSource = readFileSync(resolve(__dirname, "../src/app/components/task-composer.tsx"), "utf8");

test("app shell owns viewport scrolling instead of the document", () => {
  assert.match(appSource, /h-dvh/);
  assert.match(appSource, /overflow-hidden/);
  assert.match(appSource, /<main className="[^"]*min-h-0/);
  assert.match(themeSource, /html,\s*body,\s*#root\s*\{[^}]*height:\s*100%;[^}]*overflow:\s*hidden;/s);
  assert.match(themeSource, /body\s*\{[^}]*background:\s*oklch\(0\.145 0 0\);/s);
  assert.match(appSource, /matchMedia\("\(max-width: 767px\)"\)/);
  assert.match(appSource, /overlay=\{compactLayout\}/);
  assert.match(sidebarSource, /fixed inset-y-0 left-0 z-40/);
  assert.match(composerSource, /flex flex-wrap items-center/);
});

test("session sidebar keeps footer fixed and truncates long session titles", () => {
  assert.match(sidebarSource, /SESSION_TITLE_MAX_CHARS/);
  assert.match(sidebarSource, /formatSessionTitle\(title\)/);
  assert.match(sidebarSource, /<aside[\s\S]*overflow-hidden/);
  assert.match(sidebarSource, /<ScrollArea className="flex-1 min-h-0 overflow-hidden"/);
  assert.match(sidebarSource, /border-t border-border shrink-0/);
  assert.match(sidebarSource, /title=\{title\}/);
});

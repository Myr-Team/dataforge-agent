import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("primary shell navigation does not repeat workspace role or invitation controls", async () => {
  const source = await readFile(new URL("./components.jsx", import.meta.url), "utf8");
  const start = source.indexOf("export function ShellNav");
  const end = source.indexOf("export function MobileNav", start);
  assert.ok(start >= 0 && end > start);
  const shellSource = source.slice(start, end);

  assert.doesNotMatch(shellSource, /ws-foot/);
  assert.doesNotMatch(shellSource, /Invite members/);
  assert.doesNotMatch(shellSource, />Workspace</);
  assert.doesNotMatch(shellSource, />Role</);
});

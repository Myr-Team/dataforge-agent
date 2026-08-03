import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("Playwright isolates worktree ports and bypasses proxies for loopback readiness", async () => {
  const source = await readFile(new URL("../playwright.config.mjs", import.meta.url), "utf8");

  assert.match(source, /DF_PLAYWRIGHT_PORT/);
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /localhost/);
  assert.match(source, /process\.env\.NO_PROXY\s*=/);
  assert.match(source, /process\.env\.no_proxy\s*=/);
});

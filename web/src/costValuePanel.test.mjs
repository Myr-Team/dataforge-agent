import assert from "node:assert/strict";
import test from "node:test";

import { createServer } from "vite";

let server;

test.before(async () => {
  server = await createServer({
    root: process.cwd(),
    server: { middlewareMode: true },
    appType: "custom",
  });
});

test.after(async () => {
  await server?.close();
});

test("exports the Cost and Value panel", async () => {
  const { CostValuePanel } = await server.ssrLoadModule("/src/components.jsx");

  assert.equal(typeof CostValuePanel, "function");
});

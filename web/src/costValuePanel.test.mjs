import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";

let server;
const webRoot = fileURLToPath(new URL("..", import.meta.url));

test.before(async () => {
  server = await createServer({
    root: webRoot,
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

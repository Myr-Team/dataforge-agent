import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("the root logo is cached independently from the no-store SPA shell", async () => {
  const config = await readFile(new URL("../nginx.conf.template", import.meta.url), "utf8");

  assert.match(config, /location = \/dataforge-logo\.png/);
  assert.match(config, /max-age=2592000, stale-while-revalidate=604800/);
});

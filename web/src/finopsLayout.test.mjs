import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("operations header isolates title copy from the synchronization control", async () => {
  const [component, styles] = await Promise.all([
    readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8"),
    readFile(new URL("./styles.css", import.meta.url), "utf8"),
  ]);

  assert.match(component, /className="finops-head-copy"/);
  assert.match(component, /<h1>运营管理<\/h1>/);
  assert.doesNotMatch(styles, /\.finops-head\s*>\s*div\s*>\s*span/);
  assert.match(styles, /\.finops-head-copy\s*>\s*span/);
  assert.match(styles, /\.finops-live\s*>\s*span/);
});

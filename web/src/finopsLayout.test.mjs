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


test("overview uses six metrics, data trust, selectable trend and no budget forecast", async () => {
  const component = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(component, /aria-label="趋势指标"/);
  assert.match(component, /title="数据可信度"/);
  assert.match(component, /计价覆盖/);
  assert.match(component, /Token 覆盖/);
  assert.match(component, /APIM 对账/);
  assert.doesNotMatch(component, /title="预算消耗与期末预测"/);
});


test("trend bars scale inside a dedicated plot track without clipping near-maximum values", async () => {
  const [component, styles] = await Promise.all([
    readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8"),
    readFile(new URL("./styles.css", import.meta.url), "utf8"),
  ]);

  assert.match(component, /className="finops-trend-plot"/);
  assert.match(component, /className="finops-trend-bar-slot"/);
  assert.match(styles, /\.finops-trend-plot\s*\{[^}]*height:\s*100%/s);
  assert.match(styles, /\.finops-trend-plot\s*\{[^}]*grid-template-rows:\s*18px minmax\(0, 1fr\)/s);
  assert.match(styles, /\.finops-trend-bar-slot\s*\{[^}]*height:\s*100%[^}]*align-items:\s*flex-end/s);
  assert.doesNotMatch(styles, /\.finops-trend-stack\s*\{[^}]*max-height:/s);
});

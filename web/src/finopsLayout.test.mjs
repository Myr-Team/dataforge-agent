import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";


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


test("overview uses operations metrics, data trust, selectable trend and no budget forecast", async () => {
  const component = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(component, /aria-label="趋势指标"/);
  assert.match(component, /title="数据可信度"/);
  assert.match(component, /计价覆盖/);
  assert.match(component, /Token 覆盖/);
  assert.match(component, /CUSTOMER_INFRA_LABELS\.reconciliation/);
  assert.doesNotMatch(component, /APIM 对账/);
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


test("ROI page exposes one honest operating-decision hierarchy", async () => {
  const source = await readFile(
    new URL("./finops/RoiDecisionPage.jsx", import.meta.url),
    "utf8",
  );

  for (const label of [
    "本期运营判断",
    "本期月度测算",
    "价值桥",
    "证据成熟度",
    "单位效能趋势",
    "平台自动确认",
    "业务侧补充验证",
    "咨询运营 AI",
  ]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /roiDecisionView\(payload\)/);
  assert.match(source, /<ValueBridge/);
  assert.match(source, /<EvidenceMaturity/);
  assert.match(source, /<FinOpsCapabilityNote/);
  assert.match(source, /evidenceRefs:\s*stage\.requestEvidenceRefs/);
  assert.match(source, /loading\s*&&\s*!payload/);
  assert.match(source, /error\s*&&\s*payload/);
  assert.match(source, /updating/);
  assert.doesNotMatch(source, /Azure Cost Management|APIM|API Management/);
});


test("portal loads one ROI decision and removes duplicate legacy ROI bodies", async () => {
  const source = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /loadFinOpsRoiDecision/);
  assert.match(source, /<RoiDecisionPage/);
  assert.match(source, /roiRefreshKey/);
  assert.match(source, /openRoiEditor/);
  assert.doesNotMatch(source, /loadWorkspaceCostValue/);
  assert.doesNotMatch(source, /loadWorkspaceRoi/);
  assert.doesNotMatch(source, /function RoiEconomics/);
  assert.doesNotMatch(source, /function RoiPage/);
});


test("ROI scenario dialog preserves server save semantics without inventing model cost", async () => {
  const source = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /createWorkspaceRoiScenario\(workspaceId, payload\)/);
  assert.match(source, /name="model_cost"/);
  assert.match(source, /previous_id:\s*latestScenario\.scenario_id/);
  assert.match(source, /base_revision:\s*latestScenario\.revision/);
  assert.doesNotMatch(source, /Number\(observedModelCost\s*\|\|\s*0\)/);
});


test("ROI stage request actions exclude run and outcome references", async () => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
  });
  try {
    const { RoiDecisionPage } = await server.ssrLoadModule("/src/finops/RoiDecisionPage.jsx");
    const markup = renderToStaticMarkup(React.createElement(RoiDecisionPage, {
      payload: {
        evidence_maturity: {
          stages: [
            {
              id: "usage",
              label: "使用",
              value: 2,
              unit: "次调用",
              status: "observed",
              evidence_count: 2,
              evidence_refs: ["req_12345678", "run-12345678"],
            },
            {
              id: "outcome",
              label: "业务结果",
              value: 1,
              unit: "项结果",
              status: "observed",
              evidence_count: 1,
              evidence_refs: ["outcome-12345678"],
            },
          ],
        },
      },
      onEvidence() {},
    }));

    assert.match(markup, /使用 · 1 条/);
    assert.doesNotMatch(markup, /业务结果 · 1 条/);
    assert.doesNotMatch(markup, /run-12345678|outcome-12345678/);
  } finally {
    await server.close();
  }
});

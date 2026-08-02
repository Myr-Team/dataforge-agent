import assert from "node:assert/strict";
import test from "node:test";

import {
  applyDimensionFilter,
  filterChips,
  metricContext,
  metricTooltip,
  previousEqualWindow,
} from "./finopsInteraction.js";
import { readFile } from "node:fs/promises";


test("cache tooltip exposes only recorded cache states", () => {
  const tooltip = metricTooltip({
    kind: "cache",
    label: "缓存使用",
    cache: {
      hit: 8,
      miss: 2,
      bypassed: 1,
      unavailable: 3,
      eligible: 10,
    },
    dataStatus: "partial",
    evidenceState: "observed",
  });

  assert.deepEqual(tooltip.rows.map((row) => row.label), [
    "缓存命中",
    "缓存未命中",
    "绕过缓存",
    "状态不可用",
    "可缓存样本",
  ]);
  assert.equal(tooltip.dataStatus, "partial");
});


test("latency tooltip never guesses a cache result", () => {
  const tooltip = metricTooltip({
    kind: "quality",
    label: "请求质量",
    requests: 50,
    successRatePct: 96,
    p50Ms: 820,
    p95Ms: 2100,
    error4xx: 1,
    error5xx: 1,
  });

  assert.deepEqual(tooltip.rows.map((row) => row.label), [
    "调用样本",
    "成功率",
    "P50",
    "P95",
    "4xx",
    "5xx",
  ]);
  assert.equal(tooltip.rows.some((row) => row.label.includes("缓存")), false);
});


test("metric context keeps only bounded safe fields", () => {
  const context = metricContext(
    {
      id: "cache_hit_rate",
      label: "缓存命中率",
      value: 62.5,
      unit: "%",
      kind: "cache",
      dimension: "model",
      dimensionValue: "gpt-5",
      dataStatus: "partial",
      evidenceState: "observed",
      cacheState: "hit",
      secret: "must-not-pass",
    },
    {
      window: {
        from: "2026-07-01T00:00:00Z",
        to: "2026-07-26T00:00:00Z",
      },
      filters: {
        workspaceId: "ws-a",
        departmentId: "finance",
        unsupported: "must-not-pass",
      },
    },
  );

  assert.deepEqual(context, {
    metric_id: "cache_hit_rate",
    label: "缓存命中率",
    value: 62.5,
    unit: "%",
    dimension: "model",
    dimension_value: "gpt-5",
    window: {
      from: "2026-07-01T00:00:00Z",
      to: "2026-07-26T00:00:00Z",
    },
    filters: {
      workspace_id: "ws-a",
      department_id: "finance",
    },
    data_status: "partial",
    evidence_state: "observed",
    cache_state: "hit",
  });
});


test("dimension selection drives visible filter chips and resettable state", () => {
  const selected = applyDimensionFilter(
    { departmentId: "finance", agentId: "", model: "" },
    { dimension: "model", value: "gpt-5" },
  );
  assert.deepEqual(selected, {
    departmentId: "finance",
    agentId: "",
    model: "gpt-5",
  });
  assert.deepEqual(filterChips(selected), [
    { key: "departmentId", label: "部门", value: "finance" },
    { key: "model", label: "模型", value: "gpt-5" },
  ]);
});


test("comparison window uses the immediately preceding equal duration", () => {
  assert.deepEqual(previousEqualWindow({
    from: "2026-07-11T00:00:00Z",
    to: "2026-07-26T00:00:00Z",
  }), {
    from: "2026-06-26T00:00:00.000Z",
    to: "2026-07-11T00:00:00.000Z",
  });
});


test("operations trends keep event counts in hover detail without persistent markers", async () => {
  const source = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");

  assert.match(source, /loadFinOpsTrends/);
  assert.match(source, /previousEqualWindow/);
  assert.match(source, /className="finops-trend-comparison"/);
  assert.doesNotMatch(source, /className="finops-trend-event"/);
  assert.match(source, /rowEvents\.length \? <span>运营事件/);
});


test("risk selection distinguishes initial selection from an explicit close", async () => {
  const server = await import("vite").then(({ createServer }) => createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  }));
  try {
    const { resolveSelectedRisk } = await server.ssrLoadModule("/src/finops/RiskDecisionPage.jsx");
    const priorities = [{ id: "risk-a" }, { id: "risk-b" }];
    assert.equal(resolveSelectedRisk(undefined, priorities)?.id, "risk-a");
    assert.equal(resolveSelectedRisk("risk-b", priorities)?.id, "risk-b");
    assert.equal(resolveSelectedRisk(null, priorities), null);
    assert.equal(resolveSelectedRisk("missing", priorities), null);
  } finally {
    await server.close();
  }
});


test("portfolio selection defaults only when the selectedId prop is omitted", async () => {
  const server = await import("vite").then(({ createServer }) => createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  }));
  try {
    const {
      OpportunityPortfolio,
      resolvePortfolioSelection,
    } = await server.ssrLoadModule("/src/finops/DecisionCharts.jsx");
    const validIds = ["risk-a", "risk-b"];
    assert.equal(resolvePortfolioSelection(undefined, "", validIds, false), "risk-a");
    assert.equal(resolvePortfolioSelection("", "risk-a", validIds, true), "");
    assert.equal(resolvePortfolioSelection(null, "risk-a", validIds, true), "");
    assert.equal(resolvePortfolioSelection("risk-b", "", validIds, true), "risk-b");

    const React = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const data = {
      items: [
        { id: "risk-a", label: "风险 A", domainLabel: "成本", effortLabel: "低", impactLevelLabel: "高", impactLabel: "待验证" },
        { id: "risk-b", label: "风险 B", domainLabel: "体验", effortLabel: "中", impactLevelLabel: "中", impactLabel: "待验证" },
      ],
      points: [],
      metadata: {},
    };
    const omitted = renderToStaticMarkup(React.createElement(OpportunityPortfolio, { data }));
    const closed = renderToStaticMarkup(React.createElement(OpportunityPortfolio, { data, selectedId: null }));
    assert.match(omitted, /<li class="finops-decision-selected"/);
    assert.doesNotMatch(closed, /<li class="finops-decision-selected"/);
  } finally {
    await server.close();
  }
});


test("risk mutation refresh uses scoped invalidation without a forced server bypass", async () => {
  const source = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");
  assert.match(source, /invalidateFinOpsMutation\("risk_draft", \{ workspaceId \}\)/);
  assert.match(source, /requestTabRefresh\("risk", \{ force: false \}\)/);
});


test("portal risk integration uses one decision read and conflict-safe draft reload", async () => {
  const source = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");

  assert.match(source, /loadFinOpsRiskDecision/);
  assert.match(source, /<RiskDecisionPage/);
  assert.match(source, /<RemediationDraftPanel/);
  assert.match(source, /orchestrateRemediationMutation/);
  assert.match(source, /refreshOpportunity/);
  assert.match(source, /loadFinOpsTab\(\{[\s\S]*tab:\s*"risk"[\s\S]*force:\s*true[\s\S]*\}\)\.promise/);
  assert.match(source, /loadFinOpsRemediationDraft/);
  assert.match(source, /mutationError=\{riskMutation\.error\}/);
  assert.match(source, /busyId=\{riskMutation\.busyId\}/);
  assert.doesNotMatch(source, /function RiskPage\(/);
  assert.doesNotMatch(source, /risk:\s*\(\)\s*=>\s*Promise\.all/);
});


test("model setting callbacks run only after a successful write", async () => {
  const server = await import("vite").then(({ createServer }) => createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  }));
  try {
    const { persistModelSetting } = await server.ssrLoadModule("/src/ModelRoutingPage.jsx");
    let changes = 0;
    const saved = await persistModelSetting(
      async () => ({ revision: 2 }),
      () => { changes += 1; },
      "model",
    );
    assert.deepEqual(saved, { revision: 2 });
    assert.equal(changes, 1);
    await assert.rejects(
      persistModelSetting(
        async () => { throw new Error("save failed"); },
        () => { changes += 1; },
        "price",
      ),
      /save failed/,
    );
    assert.equal(changes, 1);
  } finally {
    await server.close();
  }
});

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
  assert.match(component, /const pageTitle = surface === "risk" \? "风险与优化" : "成本管理"/);
  assert.match(component, /<h1>\{pageTitle\}<\/h1>/);
  assert.doesNotMatch(styles, /\.finops-head\s*>\s*div\s*>\s*span/);
  assert.match(styles, /\.finops-head-copy\s*>\s*span/);
  assert.match(styles, /\.finops-live\s*>\s*span/);
  assert.match(styles, /\.finops-live\s*>\s*span\s*\{[^}]*min-width:/s);
  assert.doesNotMatch(component, /新鲜度/);
});


test("overview renders one executive decision hierarchy", async () => {
  const component = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(component, /executiveOverviewView\(data\)/);
  assert.match(component, /aria-label="运营决策概览"/);
  assert.match(component, /aria-label="趋势指标"/);
  assert.match(component, /title="部门成本构成"/);
  assert.match(component, /查看成本分析/);
  assert.match(component, /成本分析[\s\S]*成本来自哪里/);
  assert.match(component, /效能与 ROI[\s\S]*投入是否产生价值/);
  assert.match(component, /风险与优化[\s\S]*现在应优先处理什么/);
  assert.doesNotMatch(component, /title="数据可信度"/);
  assert.doesNotMatch(component, /title="部门成本与运行质量"/);
  assert.doesNotMatch(component, /APIM 对账/);
  assert.doesNotMatch(component, /title="预算消耗与期末预测"/);
});


test("cost analysis starts with one compact cost scope instead of the overview KPI grid", async () => {
  const component = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );
  const costPage = component.match(/function CostPage[\s\S]*?function RoiScenarioDialog/)?.[0] || "";

  assert.match(costPage, /className="finops-cost-summary"/);
  assert.match(costPage, /维护计价映射/);
  assert.doesNotMatch(costPage, /<MetricCards/);
  assert.match(costPage, /title="Agent 成本归因"/);
  assert.match(costPage, /title="模型成本归因"/);
  assert.doesNotMatch(costPage, /title="Agent 成本结构"/);
  assert.doesNotMatch(costPage, /title="模型成本结构"/);
});


test("risk decisions stay ahead of detailed scan rules", async () => {
  const source = await readFile(
    new URL("./finops/RiskDecisionPage.jsx", import.meta.url),
    "utf8",
  );
  const workbench = source.match(/function RiskScanWorkbench[\s\S]*?function EvidenceChain/)?.[0] || "";
  const page = source.match(/export function RiskDecisionPage[\s\S]*$/)?.[0] || "";

  assert.match(workbench, /<details className="finops-risk-scan-disclosure">/);
  assert.match(workbench, /<summary>[\s\S]*判定规则/);
  assert.match(workbench, /finops-risk-scan-rules/);
  assert.ok(page.indexOf("finops-decision-risk-columns") < page.indexOf("<EvidenceChain"));
  assert.ok(page.indexOf("<EvidenceChain") < page.indexOf("<OpportunityPortfolio"));
});


test("executive donut replaces the browser SVG focus rectangle with a slice highlight", async () => {
  const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

  assert.match(styles, /\.finops-executive-donut:focus-within\s*\{[^}]*outline:\s*none/s);
  assert.match(styles, /\.finops-executive-donut \.segment:focus-visible\s*\{[^}]*outline:\s*none\s*!important/s);
  assert.match(styles, /\.finops-executive-donut \.segment:focus-visible\s*\{[^}]*stroke-width:\s*7/s);
});


test("mobile operations AI launcher stays compact over dashboard content", async () => {
  const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

  assert.match(styles, /\.finops-ai-launcher\s*\{[^}]*width:\s*42px[^}]*padding:\s*0/s);
  assert.match(styles, /\.finops-ai-launcher:hover[\s\S]*width:\s*auto/s);
  assert.match(styles, /@media \(min-width: 981px\)[\s\S]*\.finops-content\s*\{[^}]*padding-right:\s*56px/s);
  assert.match(styles, /@media \(max-width: 640px\)[\s\S]*\.finops-ai-launcher\s*\{[^}]*width:\s*42px[^}]*padding:\s*0/s);
  assert.match(styles, /@media \(max-width: 640px\)[\s\S]*\.finops-ai-launcher span\s*\{[^}]*display:\s*none/s);
});


test("decision surfaces reserve readable type and a safe area for the AI launcher", async () => {
  const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

  assert.match(styles, /\.finops-content\s*\{[^}]*padding-bottom:\s*64px/s);
  assert.match(styles, /\.finops-risk-scan-disclosure\s*>\s*summary\s*\{[^}]*cursor:\s*pointer/s);
  assert.match(styles, /\.finops-risk-scan-rules\s*>\s*li\s*>\s*p\s*\{[^}]*font-size:\s*10\.5px/s);
  assert.match(styles, /\.finops-decision-roi-metric\s*>\s*p\s*\{[^}]*font-size:\s*10\.5px/s);
  assert.match(styles, /\.finops-decision-risk-priorities b\s*\{[^}]*font-size:\s*11\.5px/s);
  assert.match(styles, /\.finops-decision-status\s*\{[^}]*font-size:\s*10px/s);
  assert.match(styles, /\.finops-decision-risk-row\s*>\s*span\s*>\s*b\s*\{[^}]*font-size:\s*11px/s);
  assert.match(styles, /\.finops-decision-risk-evidence-card dt\s*\{[^}]*font-size:\s*10px/s);
  assert.match(styles, /\.finops-decision-risk-insight p,[\s\S]*font-size:\s*11px/s);
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


test("metric and trend tooltips render at viewport level and clamp to screen edges", async (context) => {
  const [component, tooltipSource, styles] = await Promise.all([
    readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8"),
    readFile(new URL("./finops/ViewportTooltip.jsx", import.meta.url), "utf8"),
    readFile(new URL("./styles.css", import.meta.url), "utf8"),
  ]);
  assert.match(component, /ViewportTooltip/);
  assert.match(tooltipSource, /createPortal/);
  assert.match(tooltipSource, /className={`finops-viewport-tooltip/);
  assert.doesNotMatch(component, /className="finops-trend-tooltip"/);
  assert.match(styles, /\.finops-viewport-tooltip\s*\{[^}]*position:\s*fixed[^}]*z-index:\s*1[2-9]\d/s);

  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { viewportTooltipPosition } = await server.ssrLoadModule("/src/FinOpsPortal.jsx");
  assert.deepEqual(viewportTooltipPosition(
    { left: 0, right: 20, top: 120, bottom: 140, width: 20 },
    { width: 180, height: 80 },
    { width: 320, height: 240 },
  ), { left: 12, top: 30 });
  assert.deepEqual(viewportTooltipPosition(
    { left: 300, right: 320, top: 8, bottom: 28, width: 20 },
    { width: 180, height: 100 },
    { width: 320, height: 240 },
  ), { left: 128, top: 38 });
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
  assert.doesNotMatch(source, /finops-decision-roi-ai|咨询当前判断|开始咨询/);
  assert.doesNotMatch(source, /Azure Cost Management|APIM|API Management/);

  const chartSource = await readFile(
    new URL("./finops/DecisionCharts.jsx", import.meta.url),
    "utf8",
  );
  assert.match(chartSource, /finops-decision-value-formula/);
  assert.match(chartSource, /finops-decision-value-result-strip/);
  assert.doesNotMatch(chartSource, /finops-decision-zero-axis/);
});


test("portal loads one ROI decision and removes duplicate legacy ROI bodies", async () => {
  const source = await readFile(
    new URL("./FinOpsPortal.jsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /loadFinOpsRoiDecision/);
  assert.match(source, /<RoiDecisionPage/);
  assert.match(source, /requestTabRefresh\("roi"/);
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


test("ROI verified result remains separate from estimated scenario metrics", async () => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
  });
  try {
    const { RoiDecisionPage } = await server.ssrLoadModule("/src/finops/RoiDecisionPage.jsx");
    const estimatedMetric = {
      id: "monthly_benefit",
      label: "月度收益",
      value: 800,
      unit: "USD",
      status: "estimated",
    };
    const verifiedMarkup = renderToStaticMarkup(React.createElement(RoiDecisionPage, {
      payload: {
        metrics: [estimatedMetric],
        verified_roi: { value: 1.25, status: "verified" },
      },
    }));
    const unverifiedMarkup = renderToStaticMarkup(React.createElement(RoiDecisionPage, {
      payload: {
        metrics: [estimatedMetric],
        verified_roi: { value: 1.25, status: "observed" },
      },
    }));

    assert.match(verifiedMarkup, /aria-label="已验证 ROI"/);
    assert.match(verifiedMarkup, /125%/);
    assert.match(verifiedMarkup, /已验证/);
    assert.match(verifiedMarkup, /本期月度测算/);
    assert.match(unverifiedMarkup, /aria-label="已验证 ROI"/);
    assert.match(unverifiedMarkup, /证据不足/);
    assert.match(unverifiedMarkup, /结果待验证/);
    assert.doesNotMatch(unverifiedMarkup, /125%/);
  } finally {
    await server.close();
  }
});


test("ROI portal status, scenario readiness, and refresh stay locally bounded", async () => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
  });
  try {
    const {
      FinOpsPortal,
      RoiScenarioDialog,
      finOpsPortalStatusVisibility,
    } = await server.ssrLoadModule("/src/FinOpsPortal.jsx");
    assert.equal(typeof FinOpsPortal, "function");

    assert.deepEqual(finOpsPortalStatusVisibility({
      tab: "roi",
      overviewLoading: true,
      overviewError: "overview failed",
      hasOverviewMetrics: false,
    }), {
      showOverviewSkeleton: false,
      showOverviewStaleError: false,
      showOverviewHardError: false,
    });
    assert.equal(finOpsPortalStatusVisibility({
      tab: "overview",
      overviewLoading: true,
      overviewError: "",
      hasOverviewMetrics: false,
    }).showOverviewSkeleton, true);

    const loadingMarkup = renderToStaticMarkup(React.createElement(RoiScenarioDialog, {
      loading: true,
      onClose() {},
      onSave() {},
    }));
    const readyMarkup = renderToStaticMarkup(React.createElement(RoiScenarioDialog, {
      loading: false,
      observedModelCost: 12.34,
      onClose() {},
      onSave() {},
    }));
    assert.doesNotMatch(loadingMarkup, /<form/);
    assert.match(loadingMarkup, /正在读取最近一次情景参数/);
    assert.match(readyMarkup, /<form/);
    assert.match(readyMarkup, /name="model_cost"[^>]*value="12\.34"/);

  } finally {
    await server.close();
  }
});


test("risk page contains the complete linked signal-to-verification hierarchy", async () => {
  const [source, charts] = await Promise.all([
    readFile(new URL("./finops/RiskDecisionPage.jsx", import.meta.url), "utf8"),
    readFile(new URL("./finops/DecisionCharts.jsx", import.meta.url), "utf8"),
  ]);

  for (const label of [
    "本期风险判断",
    "风险矩阵",
    "优先事项",
    "优化组合",
    "信号",
    "评估样本量",
    "代表证据",
    "判定依据",
    "处置建议",
    "改善验证",
    "最新 AI 解读",
    "治理边界",
  ]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /riskDecisionView\(payload\)/);
  assert.match(source, /<RiskMatrix/);
  assert.match(source, /<OpportunityPortfolio/);
  assert.match(source, /selectedRiskId/);
  assert.match(source, /requestEvidenceRefs/);
  assert.match(source, /finops-decision-risk-recommendation/);
  assert.match(source, /finops-decision-risk-facts/);
  assert.match(source, /<details[^>]*className="finops-decision-risk-technical"/);
  assert.doesNotMatch(source, /真实影响范围|业务影响/);
  assert.doesNotMatch(source, /咨询当前判断|继续询问/);
  assert.match(charts, /finops-decision-risk-quadrants/);
  assert.match(charts, /finops-decision-opportunity-bars/);
  assert.doesNotMatch(charts, /影响高|影响可控|· 影响 \{point\.yImpact\}/);
  assert.doesNotMatch(charts, /finops-decision-matrix-point/);
  assert.doesNotMatch(charts, /finops-decision-portfolio-point/);
  assert.doesNotMatch(source, /provider_response_id|prompt|raw_identity|internal_error/);
});


test("risk quadrants keep colocated risks distinct without changing source values", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { riskQuadrants } = await server.ssrLoadModule("/src/finops/DecisionCharts.jsx");
  const points = [
    { id: "a", xConfidence: 3, yImpact: 3, bubbleSize: 198 },
    { id: "b", xConfidence: 3, yImpact: 3, bubbleSize: 60 },
    { id: "c", xConfidence: 1, yImpact: 1, bubbleSize: 12 },
  ];

  const quadrants = riskQuadrants(points);

  assert.deepEqual(quadrants.map((item) => item.id), ["priority", "validate", "improve", "observe"]);
  assert.deepEqual(quadrants[0].items.map((item) => item.id), ["a", "b"]);
  assert.deepEqual(quadrants[3].items.map((item) => item.id), ["c"]);
  assert.deepEqual(points.map((item) => [item.xConfidence, item.yImpact, item.bubbleSize]), [
    [3, 3, 198],
    [3, 3, 60],
    [1, 1, 12],
  ]);
});


test("risk page renders only selected request evidence and keeps technical IDs collapsed", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { RiskDecisionPage } = await server.ssrLoadModule("/src/finops/RiskDecisionPage.jsx");
  const payload = {
    decision: {
      state: "prioritized",
      title: "风险优先级已形成",
      summary: "按服务端证据排序。",
      evidence_state: "observed",
    },
    risk_domains: [
      { id: "cost", count: 1 },
      { id: "experience", count: 1 },
      { id: "efficiency", count: 1 },
      { id: "governance", count: 1 },
    ],
    risk_matrix: [
      { opportunity_id: "risk-cache", policy_type: "cache_hit_rate", risk_domain: "efficiency", x_confidence: 3, y_impact: 2, bubble_size: 60 },
      { opportunity_id: "risk-latency", policy_type: "p95_latency", risk_domain: "experience", x_confidence: 2, y_impact: 3, bubble_size: 20 },
    ],
    priorities: [
      { opportunity_id: "risk-cache", policy_type: "cache_hit_rate", risk_domain: "efficiency", impact: "medium", confidence: "high", effort: "low", sample_count: 60, evidence_refs: ["req_cache_001", "run-private"], base_version: "cache-policy-v9" },
      { opportunity_id: "risk-latency", policy_type: "p95_latency", risk_domain: "experience", impact: "high", confidence: "medium", effort: "medium", sample_count: 20, evidence_refs: ["req_latency_001"] },
    ],
    selected_evidence_summaries: [
      { request_ref: "req_cache_001", request_name: "缓存复用检查", cache_state: "miss", status: "succeeded", latency_ms: 810, signal: { metric: "request", value: 1, unit: "requests" }, visible_answer_summary: "已返回可见分析摘要", technical_refs: { request_ref: "req_cache_001", run_id: "run-safe", provider_response_id: "provider-secret" } },
      { request_ref: "req_latency_001", request_name: "高时延请求", cache_state: "bypassed", status: "failed", error_category: "timeout", latency_ms: 4200, signal: { metric: "request", value: 1, unit: "requests" } },
    ],
  };
  const evidenceCalls = [];
  const markup = renderToStaticMarkup(React.createElement(RiskDecisionPage, {
    payload,
    selectedRiskId: "risk-cache",
    onSelectRisk() {},
    onEvidence(value) { evidenceCalls.push(value); },
    onCreateDraft() {},
  }));

  assert.match(markup, /缓存复用检查/);
  assert.match(markup, /已返回可见分析摘要/);
  assert.match(markup, /<details class="finops-decision-risk-technical">/);
  assert.match(markup, /req_cache_001/);
  assert.doesNotMatch(markup, /高时延请求|req_latency_001|run-private|provider-secret/);
  assert.match(markup, /查看证据/);
  assert.match(markup, /查看整改方案/);
  assert.match(markup, /finops-decision-risk-recommendation/);
  assert.match(markup, /finops-decision-risk-facts/);
});


test("risk page shows local cache and mutation states without infrastructure wording", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { RiskDecisionPage } = await server.ssrLoadModule("/src/finops/RiskDecisionPage.jsx");
  const payload = {
    freshness: { query_cache: { status: "hit_stale" } },
    decision: { state: "prioritized", title: "风险判断", summary: "证据已排序", evidence_state: "observed" },
    risk_domains: [],
    risk_matrix: [],
    priorities: [{
      opportunity_id: "risk-cache",
      anomaly_id: "anomaly-cache",
      anomaly_status: "open",
      applicable_actions: ["acknowledge", "suppress"],
      policy_type: "cache_hit_rate",
      risk_domain: "efficiency",
      impact: "medium",
      confidence: "high",
      effort: "low",
      sample_count: 20,
      base_version: "cache-policy-v7",
    }],
    optimization_portfolio: [],
    selected_evidence_summaries: [],
    governance_capability: { draft_enabled: true },
  };
  const markup = renderToStaticMarkup(React.createElement(RiskDecisionPage, {
    payload,
    updating: true,
    mutationError: "异常治理操作失败",
    busyId: "anomaly-cache",
    onAcknowledge() {},
    onSuppress() {},
  }));

  assert.match(markup, /正在使用最近一次结果/);
  assert.match(markup, /后台更新中/);
  assert.match(markup, /异常治理操作失败/);
  assert.match(markup, /<button type="button" disabled="">确认异常<\/button>/);
  assert.match(markup, /<button type="button" disabled="">抑制异常<\/button>/);
  assert.doesNotMatch(markup, /新鲜度|APIM|Azure API Management/);
});


test("risk page renders an explainable read-only scan with rule-specific actions", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { RiskDecisionPage } = await server.ssrLoadModule("/src/finops/RiskDecisionPage.jsx");
  const scan = {
    scan_ref: "rscan_0123456789abcdef0123456789abcdef",
    status: "completed",
    rules_evaluated: 7,
    rules_triggered: 1,
    rules_clear: 5,
    rules_insufficient: 1,
    request_sample_count: 146,
    evidence_coverage_pct: 100,
    policy_revision: "policy_v7",
    ledger_revision: "ledger_v9",
    started_at: "2026-08-03T02:30:00Z",
    finished_at: "2026-08-03T02:30:01Z",
    findings: [{
      policy_type: "cache_hit_rate",
      status: "triggered",
      severity: "warning",
      observed_value: 18.4,
      threshold_value: 20,
      unit: "%",
      sample_count: 62,
      minimum_samples: 20,
      reason: "观测值已达到当前策略的风险判定条件。",
      recommendation: "检查缓存资格、键策略与失效窗口。",
      evidence_refs: ["req_cache_scan_001"],
    }],
    evidence_sets: [],
    governance: { mode: "read_only_scan", automatic_actions: false },
  };
  const markup = renderToStaticMarkup(React.createElement(RiskDecisionPage, {
    payload: {
      decision: { state: "prioritized", title: "风险判断", summary: "证据已排序", evidence_state: "observed" },
      risk_domains: [], risk_matrix: [], priorities: [], optimization_portfolio: [], selected_evidence_summaries: [],
      governance_capability: { draft_enabled: true },
    },
    scan,
    onRunScan() {},
    onEvidence() {},
    onAsk() {},
  }));

  for (const copy of ["只读规则扫描", "重新扫描", "七项运营检查", "缓存效率", "观测值", "阈值", "样本", "查看证据", "问 AI"]) {
    assert.match(markup, new RegExp(copy));
  }
  assert.match(markup, /18\.4%/);
  assert.match(markup, /扫描不会修改模型、缓存或生产策略/);
  assert.doesNotMatch(markup, /一键整改|批准并执行|actor_ref|tenant_ref/);
});


test("remediation panel keeps draft governance visibly separate from execution", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const { RemediationDraftPanel } = await server.ssrLoadModule("/src/finops/RemediationDraftPanel.jsx");
  const draft = {
    draft_id: "draft-safe",
    workspace_id: "ws-a",
    source_opportunity_id: "risk-cache",
    title: "缓存策略复核",
    summary: "先保存并复核候选策略。",
    status: "reviewed",
    revision: 2,
    action_kind: "cache_policy",
    execution_capability: "typed_action_available",
    base_version: "cache-policy-v9",
    proposed_changes: [{ field: "ttl_seconds", current_value: 0, candidate_value: 1800, rationale: "候选有效期" }],
    expected_impact: { amount: null, unit: null, status: "unavailable", calculation_basis: "证据不足" },
    prerequisites: ["确认数据版本一致"],
    risks_and_guardrails: ["不一致时绕过缓存"],
    verification_plan: [{ metric: "cache_hit_rate_pct", operator: "gte", baseline_value: 0, baseline_window: "30 days", target: 70, candidate_window_minutes: 60, minimum_samples: 20 }],
    rollback_plan: ["恢复上一版本"],
  };
  const markup = renderToStaticMarkup(React.createElement(RemediationDraftPanel, {
    draft,
    actionsEnabled: false,
    onClose() {},
    onReload() {},
    onReview() {},
    onPromote() {},
  }));

  for (const label of ["整改草案", "来源证据", "适用范围", "候选修改", "预期影响", "前置条件", "护栏", "验证标准", "回滚方案"]) {
    assert.match(markup, new RegExp(label));
  }
  assert.match(markup, /不会直接执行/);
  assert.match(markup, /提升为审批动作草案/);
  assert.match(markup, /生产执行保持关闭/);
  assert.doesNotMatch(markup, /一键执行|立即执行|批准并执行|提交执行/);
});

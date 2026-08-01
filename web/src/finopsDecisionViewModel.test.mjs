import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

import {
  remediationDraftView,
  riskDecisionView,
  roiDecisionView,
} from "./finopsDecisionViewModel.js";


test("ROI view never labels an estimated scenario as verified", () => {
  const view = roiDecisionView({
    decision: {
      state: "scenario_positive_unverified",
      title: "测算显示具备投入价值，业务结果仍需验证",
      summary: "情景参数与运行事实严格分开。",
      evidence_state: "estimated",
    },
    metrics: [{
      id: "monthly_net_benefit",
      label: "月度净收益",
      value: 2299.97,
      unit: "USD",
      status: "estimated",
      explanation: "来自情景测算。",
    }],
    verified_roi: { value: 2.2, status: "estimated", currency: "USD" },
    value_bridge: { items: [], formula_revision: "dataforge-roi-v1" },
    evidence_maturity: { score_pct: 75, stages: [] },
  });

  assert.equal(view.metrics[0].badge, "情景测算");
  assert.equal(view.verifiedRoiLabel, "证据不足");
  assert.equal(view.verifiedRoiValue, null);
  assert.equal(view.valueBridge.formulaRevision, "dataforge-roi-v1");

  const partial = roiDecisionView({
    scenarios: [{ scenario_id: "scenario-partial", status: "partial", result: {} }],
  });
  assert.equal(partial.scenarios[0].badge, "部分证据");
});


test("zero is preserved while missing remains unavailable", () => {
  const view = roiDecisionView({
    metrics: [
      { id: "cost", label: "成本", value: 0, unit: "USD", status: "observed" },
      { id: "value", label: "价值", value: null, unit: "USD", status: "unavailable" },
    ],
    evidence_maturity: {
      score_pct: 0,
      stages: [
        { id: "investment", label: "投入", value: 0, unit: "USD", status: "observed" },
        { id: "outcome", label: "业务结果", value: null, unit: "项结果", status: "not_recorded" },
      ],
    },
  });

  assert.equal(view.metrics[0].value, 0);
  assert.equal(view.metrics[0].valueLabel, "$0.00");
  assert.equal(view.metrics[1].value, null);
  assert.equal(view.metrics[1].valueLabel, "暂不可用");
  assert.equal(view.evidenceMaturity.scorePct, 0);
  assert.equal(view.evidenceMaturity.stages[0].valueLabel, "$0.00");
  assert.equal(view.evidenceMaturity.stages[1].valueLabel, "未记录");
});


test("risk bubbles preserve source coordinates and real size differences", () => {
  const view = riskDecisionView({
    risk_matrix: [
      {
        id: "risk-a",
        policy_type: "p95_latency",
        risk_domain: "experience",
        x_confidence: 3,
        y_impact: 3,
        bubble_size: 60,
      },
      {
        id: "risk-b",
        policy_type: "cache_hit_rate",
        risk_domain: "efficiency",
        x_confidence: 2,
        y_impact: 1,
        bubble_size: 20,
      },
    ],
    priorities: [{
      id: "risk-a",
      policy_type: "p95_latency",
      expected_impact: { amount: null, status: "unavailable" },
    }],
  });

  assert.deepEqual(
    view.matrix.map((point) => [point.xConfidence, point.yImpact, point.bubbleSize]),
    [[3, 3, 60], [2, 1, 20]],
  );
  assert.notEqual(view.matrix[0].radius, view.matrix[1].radius);
  assert.ok(view.matrix[0].radius > view.matrix[1].radius);
  assert.equal(view.priorities[0].impactLabel, "待验证");
});


test("visual placement clamps without mutating server coordinates", () => {
  const view = riskDecisionView({
    risk_matrix: [{
      id: "risk-edge",
      policy_type: "error_rate",
      risk_domain: "experience",
      x_confidence: 9,
      y_impact: -3,
      bubble_size: 4,
    }],
  });

  assert.equal(view.matrix[0].xConfidence, 9);
  assert.equal(view.matrix[0].yImpact, -3);
  assert.equal(view.matrix[0].visualX, 100);
  assert.equal(view.matrix[0].visualY, 0);
});


test("unknown states and malformed collections become neutral safe projections", () => {
  const view = roiDecisionView({
    decision: { state: "<script>", title: "T".repeat(500), evidence_state: "critical-secret" },
    metrics: [
      { id: "safe", label: "安全指标", value: 5, unit: "USD", status: "critical-secret" },
      "not-an-object",
    ],
    evidence_maturity: { stages: { unexpected: true } },
  });

  assert.equal(view.decision.state, "unavailable");
  assert.equal(view.decision.status, "unavailable");
  assert.equal(view.decision.badge, "状态待确认");
  assert.equal(view.decision.title.length, 160);
  assert.equal(view.metrics.length, 1);
  assert.equal(view.metrics[0].status, "unavailable");
  assert.equal(view.metrics[0].badge, "状态待确认");
  assert.deepEqual(view.evidenceMaturity.stages, []);
  const risk = riskDecisionView({ risk_matrix: {}, priorities: "bad" });
  assert.deepEqual(risk.matrix, []);
  assert.deepEqual(risk.portfolio.metadata, {
    xAxis: "",
    yAxis: "",
    size: "",
    color: "",
    status: "unavailable",
  });
});


test("capability and remediation views expose only bounded allowlisted fields", () => {
  const roi = roiDecisionView({
    capability_explanation: {
      "平台自动确认": ["调用与成本事实", "X".repeat(500), { secret: "hidden" }],
      "业务侧补充验证": ["结果负责人确认"],
      "治理边界": ["整改草案不会直接执行"],
      arbitrary: ["should not appear"],
    },
  });
  assert.deepEqual(Object.keys(roi.capability), [
    "platformConfirmed",
    "businessVerification",
    "governanceBoundary",
  ]);
  assert.equal(roi.capability.platformConfirmed.length, 2);
  assert.equal(roi.capability.platformConfirmed[1].length, 200);
  assert.deepEqual(roi.capability.businessVerification, ["结果负责人确认"]);
  assert.deepEqual(roi.capability.governanceBoundary, ["整改草案不会直接执行"]);

  const draft = remediationDraftView({
    draft: {
      draft_id: "draft-safe",
      tenant_ref: "tenant-secret",
      created_by: "owner@example.test",
      workspace_id: "workspace-safe",
      title: "缓存策略复核",
      summary: "仅保存草案。",
      status: "draft",
      revision: 0,
      action_kind: "cache_policy",
      execution_capability: "typed_action_available",
      proposed_changes: [{
        field: "ttl_seconds",
        current_value: 0,
        candidate_value: 1800,
        rationale: "复核候选有效期。",
        secret: "hidden",
      }],
      expected_impact: {
        amount: null,
        unit: null,
        status: "unavailable",
        calculation_basis: "当前证据无法量化。",
      },
      verification_plan: [{
        metric: "cache_hit_rate_pct",
        operator: "gte",
        baseline_value: 0,
        target: 70,
        minimum_samples: 20,
        candidate_window_minutes: 60,
        arbitrary: "hidden",
      }],
    },
  });

  assert.equal(draft.id, "draft-safe");
  assert.equal(draft.revision, 0);
  assert.equal(draft.proposedChanges[0].currentValue, 0);
  assert.equal(draft.expectedImpact.label, "待验证");
  assert.equal(draft.verificationPlan[0].baselineValue, 0);
  assert.equal(Object.hasOwn(draft, "tenantRef"), false);
  assert.equal(Object.hasOwn(draft, "createdBy"), false);
  assert.doesNotMatch(JSON.stringify(draft), /tenant-secret|owner@example\.test|hidden/);
});


test("shared decision components declare accessible charts and bounded tooltips", () => {
  const charts = readFileSync(new URL("./finops/DecisionCharts.jsx", import.meta.url), "utf8");
  const capability = readFileSync(new URL("./finops/FinOpsCapabilityNote.jsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

  for (const component of ["ValueBridge", "EvidenceMaturity", "RiskMatrix", "OpportunityPortfolio"]) {
    assert.match(charts, new RegExp(`export function ${component}\\b`));
  }
  assert.match(charts, /type="button"/);
  assert.match(charts, /onSelect\?\.\(point\.id\)/);
  assert.match(charts, /aria-label=/);
  assert.match(charts, /role="tooltip"/);
  assert.match(charts, /<table/);
  assert.match(charts, /<ol/);
  assert.match(capability, /export function FinOpsCapabilityNote\b/);
  assert.doesNotMatch(capability, /APIM|Azure API Management|Azure Cost Management/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(styles, /\.finops-decision-tooltip/);
});


test("shared charts render proportional accessible structures through Vite SSR", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const {
    EvidenceMaturity,
    OpportunityPortfolio,
    RiskMatrix,
    ValueBridge,
  } = await server.ssrLoadModule("/src/finops/DecisionCharts.jsx");

  const bridge = renderToStaticMarkup(React.createElement(ValueBridge, {
    items: [
      { id: "small", label: "较小", value: 2, valueLabel: "$2.00", status: "estimated", badge: "情景测算", barPct: 20 },
      { id: "large", label: "较大", value: 10, valueLabel: "$10.00", status: "estimated", badge: "情景测算", barPct: 100 },
    ],
    formulaRevision: "formula-v1",
  }));
  assert.match(bridge, /<table/);
  assert.match(bridge, /--finops-decision-bar-width:20%/);
  assert.match(bridge, /--finops-decision-bar-width:100%/);

  const hostileBridge = renderToStaticMarkup(React.createElement(ValueBridge, {
    items: [{
      id: "safe",
      label: "安全",
      value: 1,
      valueLabel: "1",
      status: "hostile-class-marker",
      badge: "状态待确认",
      barPct: 10,
    }],
  }));
  assert.doesNotMatch(hostileBridge, /hostile-class-marker/);

  const maturity = renderToStaticMarkup(React.createElement(EvidenceMaturity, {
    stages: [{
      id: "investment",
      label: "投入",
      valueLabel: "$0.00",
      badge: "已观测",
      status: "observed",
      description: "投入：$0.00；已观测",
    }],
    scoreLabel: "0%",
  }));
  assert.match(maturity, /投入/);
  assert.match(maturity, /\$0\.00/);
  assert.match(maturity, /aria-label="投入：\$0\.00；已观测"/);

  const matrix = renderToStaticMarkup(React.createElement(RiskMatrix, {
    points: [{
      id: "risk-a",
      label: "响应时延",
      domain: "experience",
      domainLabel: "体验",
      xConfidence: 3,
      yImpact: 2,
      bubbleSize: 60,
      visualX: 100,
      visualY: 50,
      radius: 20,
      accessibleLabel: "响应时延；证据置信度 3；业务影响 2；影响范围 60 次请求",
    }],
    onSelect: () => {},
  }));
  assert.match(matrix, /<button[^>]+type="button"/);
  assert.match(matrix, /响应时延；证据置信度 3/);
  assert.match(matrix, /role="tooltip"/);

  const portfolio = renderToStaticMarkup(React.createElement(OpportunityPortfolio, {
    data: {
      points: [],
      items: [{
        id: "risk-a",
        label: "响应时延",
        domain: "experience",
        domainLabel: "体验",
        effortLabel: "中",
        impactLevelLabel: "高",
        impactLabel: "待验证",
      }],
    },
  }));
  assert.match(portfolio, /<ol/);
  assert.match(portfolio, /响应时延/);
  assert.match(portfolio, /当前缺少服务端坐标/);
});

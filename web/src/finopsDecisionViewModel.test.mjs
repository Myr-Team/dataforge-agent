import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

import {
  remediationDraftView,
  riskDecisionView,
  riskScanHistoryView,
  riskScanView,
  roiDecisionView,
} from "./finopsDecisionViewModel.js";


test("risk scan view exposes all rule basis without leaking internal identities", () => {
  const view = riskScanView({
    scan_ref: "rscan_0123456789abcdef0123456789abcdef",
    status: "completed",
    rules_evaluated: 7,
    rules_triggered: 2,
    rules_clear: 3,
    rules_insufficient: 1,
    request_sample_count: 146,
    evidence_coverage_pct: 85.71,
    policy_revision: "policy_abc123",
    ledger_revision: "ledger_def456",
    started_at: "2026-08-03T02:30:00Z",
    finished_at: "2026-08-03T02:30:01Z",
    initiated_by_ref: "actor-secret",
    findings: [
      {
        policy_type: "error_rate",
        status: "triggered",
        severity: "critical",
        observed_value: 12.5,
        threshold_value: 5,
        unit: "%",
        sample_count: 24,
        minimum_samples: 20,
        reason: "观测值已达到当前策略的风险判定条件。",
        recommendation: "检查失败来源。",
        evidence_refs: ["req_error_001", "run-private"],
        arbitrary: "hidden",
      },
      {
        policy_type: "daily_cost_budget",
        status: "unavailable",
        severity: "info",
        observed_value: null,
        threshold_value: null,
        unit: "%",
        sample_count: 0,
        minimum_samples: 1,
        reason: "当前缺少该规则所需的预算或历史基线。",
        recommendation: "配置预算后重新扫描。",
        evidence_refs: [],
      },
    ],
    evidence_sets: [{
      subject_type: "risk",
      subject_id: "error_rate",
      policy_type: "error_rate",
      items: [{ request_ref: "req_error_002" }],
    }],
    governance: {
      mode: "read_only_scan",
      automatic_actions: false,
      explanation_agent_invoked: false,
    },
  });

  assert.equal(view.isAvailable, true);
  assert.equal(view.summary.evaluated, 7);
  assert.equal(view.summary.unavailable, 1);
  assert.equal(view.findings[0].label, "调用失败率");
  assert.equal(view.findings[0].statusLabel, "需关注");
  assert.equal(view.findings[0].observedLabel, "12.5%");
  assert.equal(view.findings[0].thresholdLabel, "5%");
  assert.deepEqual(view.findings[0].evidenceRefs, ["req_error_001", "req_error_002"]);
  assert.equal(view.findings[1].statusLabel, "暂不可评估");
  assert.equal(view.readOnly, true);
  assert.equal(Object.hasOwn(view, "initiatedByRef"), false);
  assert.doesNotMatch(JSON.stringify(view), /actor-secret|run-private|arbitrary|hidden/);
});

test("risk scan history distinguishes completed failed and running scans", () => {
  const view = riskScanHistoryView({
    items: [
      {
        scan_ref: "rscan_completed",
        status: "completed",
        rules_triggered: 2,
        rule_count: 7,
        request_sample_count: 146,
        evidence_coverage_pct: 85.71,
        finished_at: "2026-08-09T02:30:01Z",
      },
      {
        scan_ref: "rscan_failed",
        status: "failed",
        safe_error_category: "risk_scan_evaluation_failed",
        started_at: "2026-08-08T02:30:00Z",
      },
      { scan_ref: "rscan_running", status: "running", started_at: "2026-08-09T03:00:00Z" },
    ],
  });

  assert.equal(view.length, 3);
  assert.deepEqual(view.map((item) => item.statusLabel), ["已完成", "未完成", "扫描中"]);
  assert.equal(view[0].summary, "2 项需关注 · 146 次请求 · 证据覆盖 85.71%");
  assert.equal(view[1].summary, "扫描未完成，可重新执行");
  assert.doesNotMatch(JSON.stringify(view), /risk_scan_evaluation_failed/);
});


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
    case_story: {
      title: "运营自动化测算",
      summary: "假设每月节省 40 小时，并按 50 USD/小时折算。",
      status: "estimated",
      assumptions: [
        { id: "hours_saved", label: "每月节省工时", value: 40, unit: "小时/月" },
        { id: "hourly_value", label: "小时价值", value: 50, unit: "USD" },
      ],
      boundary: "业务结果验证前不计为已实现 ROI。",
    },
    value_bridge: { items: [], formula_revision: "dataforge-roi-v1" },
    evidence_maturity: { score_pct: 75, stages: [] },
  });

  assert.equal(view.metrics[0].badge, "情景测算");
  assert.equal(view.verifiedRoiLabel, "证据不足");
  assert.equal(view.verifiedRoiValue, null);
  assert.equal(view.valueBridge.formulaRevision, "dataforge-roi-v1");
  assert.equal(view.caseStory.title, "运营自动化测算");
  assert.equal(view.caseStory.assumptions[0].valueLabel, "40 小时/月");
  assert.match(view.caseStory.boundary, /业务结果验证前/);

  const partial = roiDecisionView({
    scenarios: [{ scenario_id: "scenario-partial", status: "partial", result: {} }],
  });
  assert.equal(partial.scenarios[0].badge, "部分证据");
});

test("synthetic demo review is labeled separately from production verified ROI", () => {
  const view = roiDecisionView({
    verified_roi: { status: "unavailable", value: null },
    scenarios: [{
      scenario_id: "roi_scenario_demo",
      status: "estimated",
      result: {},
      demo_evidence: {
        provenance: "synthetic_demo",
        production_quality_claim: false,
        label: "演示验证结果 · 合成数据",
        measured: { paired_evaluations: 20, historical_hours: 20, assisted_hours: 10 },
        process: { analysis_tasks: 96, reports: 78, evidence_reviews: 18, reviewed_savings_hours: 174.6 },
        source_refs: { run_id: "synthetic-shenzhen-site-selection-0001", request_ref: "req_shenzhen_00000001", correlation_ref: "corr_shenzhen_00000001", attempt_ref: "attempt_shenzhen_00000001" },
      },
    }],
  });

  assert.equal(view.verifiedRoiStatus, "unavailable");
  assert.equal(view.demoEvidence.label, "演示验证结果 · 合成数据");
  assert.equal(view.demoEvidence.productionQualityClaim, false);
  assert.equal(view.demoEvidence.pairedEvaluations, 20);
  assert.equal(view.demoEvidence.historicalHours, 20);
  assert.deepEqual(view.demoEvidence.process, { analysisTasks: 96, reports: 78, evidenceReviews: 18, reviewedSavingsHours: 174.6 });
  assert.equal(view.demoEvidence.sourceRefs.requestRef, "req_shenzhen_00000001");
});


test("ROI regression view exposes error metrics without calling them verified ROI", () => {
  const view = roiDecisionView({
    decision: { evidence_state: "estimated" },
    verified_roi: { status: "not_recorded", value: null },
    forecast_validation: {
      status: "estimated",
      target: "cost_per_successful_request",
      unit: "USD/次成功调用",
      sample_count: 10,
      train_count: 7,
      validation_count: 3,
      mse: 0.000004,
      rmse: 0.002,
      mae: 0.0016,
      r2: 0.82,
      baseline_mse: 0.000016,
      improvement_pct: 75,
      method_revision: "linear-holdout-v1",
    },
  });

  assert.equal(view.forecastValidation.status, "estimated");
  assert.equal(view.forecastValidation.mseLabel, "0.000004");
  assert.equal(view.forecastValidation.rmseLabel, "$0.002");
  assert.equal(view.forecastValidation.improvementLabel, "75%");
  assert.match(view.forecastValidation.boundary, /不等于已实现 ROI/);
  assert.equal(view.verifiedRoiLabel, "证据不足");
});


test("ROI view preserves a non-positive scenario decision instead of calling it valuable", () => {
  const view = roiDecisionView({
    decision: {
      state: "scenario_not_positive",
      title: "当前测算尚未达到正向回报",
      summary: "当前情景中的净收益或 ROI 不为正。",
      evidence_state: "estimated",
    },
  });

  assert.equal(view.decision.state, "scenario_not_positive");
  assert.equal(view.decision.title, "当前测算尚未达到正向回报");
  assert.equal(view.decision.badge, "情景测算");
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


test("unit economics trend preserves the server-owned per-successful-request unit", () => {
  const view = roiDecisionView({
    unit_economics_trend: [{
      period: "2026-07-31",
      label: "每次成功调用成本",
      value: 0.025,
      unit: "USD per successful request",
      status: "estimated",
    }],
  });

  assert.equal(view.unitEconomicsTrend[0].unitLabel, "USD / 成功调用");
  assert.equal(view.unitEconomicsTrend[0].valueLabel, "0.03 USD / 成功调用");
});


test("small non-zero USD unit costs remain visibly distinct instead of rounding to zero", () => {
  const view = roiDecisionView({
    unit_economics_trend: [
      { period: "7月22日", label: "每次成功调用成本", value: 0.00039, unit: "USD", status: "estimated" },
      { period: "7月23日", label: "每次成功调用成本", value: 0.00046, unit: "USD", status: "estimated" },
    ],
  });

  assert.deepEqual(
    view.unitEconomicsTrend.map((item) => item.valueLabel),
    ["$0.00039", "$0.00046"],
  );
});


test("value bridge scales only comparable units and makes legacy costs negative", () => {
  const view = roiDecisionView({
    metrics: [
      { id: "monthly_benefit", label: "月度收益", value: 100, unit: "USD", status: "estimated" },
      { id: "monthly_total_cost", label: "月度总成本", value: 50, unit: "USD", status: "estimated" },
      { id: "monthly_net_benefit", label: "月度净收益", value: 25, unit: "USD", status: "estimated" },
      { id: "roi_ratio", label: "ROI 比率", value: 2, unit: "ratio", status: "estimated" },
    ],
    value_bridge: { formula_revision: "formula-mixed-v1" },
  });

  assert.deepEqual(
    view.valueBridge.items.map((item) => [item.id, item.value, item.unit, item.barPct]),
    [
      ["monthly_benefit", 100, "USD", 100],
      ["monthly_total_cost", -50, "USD", 50],
      ["monthly_net_benefit", 25, "USD", 25],
      ["roi_ratio", 2, "ratio", 100],
    ],
  );
  assert.equal(view.valueBridge.items[1].direction, "negative");
  assert.equal(view.valueBridge.items[1].sign, -1);
  assert.equal(view.valueBridge.items[1].directionLabel, "负值");
  assert.equal(view.valueBridge.items[1].valueLabel, "-$50.00");
  assert.equal(view.valueBridge.items[1].formulaValueLabel, "$50.00");
  assert.equal(view.valueBridge.items[0].direction, "positive");
  assert.equal(view.valueBridge.items[2].direction, "positive");
  assert.equal(view.valueBridge.items[3].scaleGroup, "ratio");
  assert.deepEqual(view.valueBridge.items.map((item) => item.id), [
    "monthly_benefit",
    "monthly_total_cost",
    "monthly_net_benefit",
    "roi_ratio",
  ]);
});


test("value bridge preserves the explicit cost deduction contract", () => {
  const view = roiDecisionView({
    value_bridge: {
      items: [
        { id: "monthly_benefit", label: "月度收益", value: 3000, unit: "USD", status: "estimated" },
        { id: "monthly_total_cost", label: "AI 运营总投入", value: -800, unit: "USD", status: "estimated" },
        { id: "monthly_net_benefit", label: "月度净收益", value: 2200, unit: "USD", status: "estimated" },
      ],
    },
  });

  const cost = view.valueBridge.items.find((item) => item.id === "monthly_total_cost");
  assert.equal(cost.direction, "negative");
  assert.equal(cost.formulaValueLabel, "$800.00");
});


test("monthly total cost is presented as AI operating investment", () => {
  const view = roiDecisionView({
    metrics: [{
      id: "monthly_total_cost",
      label: "月度总成本",
      value: 800,
      unit: "USD",
      status: "estimated",
      explanation: "来自情景成本。",
    }],
  });

  assert.equal(view.metrics[0].label, "AI 运营总投入");
  assert.match(view.metrics[0].explanation, /实施摊销/);
  assert.match(view.metrics[0].explanation, /固定运营成本/);
  assert.match(view.metrics[0].explanation, /模型成本/);
  assert.equal(view.valueBridge.items[0].label, "AI 运营总投入");
  assert.equal(view.valueBridge.items[0].value, -800);
  assert.equal(view.valueBridge.items[0].direction, "negative");
  assert.equal(view.valueBridge.items[0].formulaValueLabel, "$800.00");
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

  const noTextCoercion = roiDecisionView({
    decision: { title: 12345, summary: { text: "hidden" } },
    capability_explanation: {
      "平台自动确认": [12345, "可展示文本", { text: "hidden" }],
    },
  });
  assert.equal(noTextCoercion.decision.title, "");
  assert.equal(noTextCoercion.decision.summary, "");
  assert.deepEqual(noTextCoercion.capability.platformConfirmed, ["可展示文本"]);
});


test("missing impact and unknown insight status stay unavailable", () => {
  const view = riskDecisionView({
    priorities: [{
      id: "risk-no-status",
      policy_type: "cache_hit_rate",
      estimated_savings: 99,
      currency: "USD",
    }],
    insight: {
      title: "分析结论",
      summary: "需要复核。",
      status: "hostile-status-marker",
    },
  });

  assert.equal(view.priorities[0].expectedImpact.value, null);
  assert.equal(view.priorities[0].expectedImpact.status, "unavailable");
  assert.equal(view.priorities[0].impactLabel, "待验证");
  assert.equal(view.insight.status, "unavailable");
  assert.equal(view.insight.badge, "状态待确认");
  assert.doesNotMatch(JSON.stringify(view), /hostile-status-marker/);
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
  const viewportTooltip = readFileSync(new URL("./finops/ViewportTooltip.jsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

  for (const component of ["ValueBridge", "EvidenceMaturity", "RiskMatrix", "OpportunityPortfolio"]) {
    assert.match(charts, new RegExp(`export function ${component}\\b`));
  }
  assert.match(charts, /type="button"/);
  assert.match(charts, /toggleRiskPointSelection/);
  assert.match(charts, /onSelect\?\.\(nextId \|\| null\)/);
  assert.match(charts, /aria-label=/);
  assert.match(viewportTooltip, /role="tooltip"/);
  assert.doesNotMatch(charts, /<table/);
  assert.match(charts, /<ol/);
  assert.match(capability, /export function FinOpsCapabilityNote\b/);
  assert.doesNotMatch(capability, /APIM|Azure API Management|Azure Cost Management/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(charts, /ViewportTooltip/);
  assert.match(styles, /\.finops-decision-tooltip-content/);
  assert.doesNotMatch(styles, /--finops-decision-bar-width\)\s*\/\s*2/);
  assert.doesNotMatch(styles, /\.finops-decision-value-bridge\s*\{[^}]*overflow:\s*(?:hidden|clip)/s);
  assert.doesNotMatch(styles, /\.finops-decision-maturity\s*\{[^}]*overflow:\s*(?:hidden|clip)/s);
  assert.doesNotMatch(styles, /finops-decision-tooltip-boundary:(?:first|last)-child/);
  assert.match(styles, /\.finops-decision-tooltip-boundary\s*\{[^}]*width:\s*100%[^}]*overflow:\s*visible/s);
  assert.match(styles, /\.finops-decision-tooltip-content\s*\{[^}]*width:\s*min\(230px,/s);
  assert.match(styles, /\.finops-decision-value-label\s*\{[^}]*flex-wrap:\s*wrap/s);
  assert.match(styles, /\.finops-decision-maturity-stages header\s*\{[^}]*flex-wrap:\s*wrap/s);
  assert.doesNotMatch(styles, /\.finops-decision-risk-quadrants[^}]*overflow:\s*(?:hidden|clip)/s);
});


test("risk and remediation projections expose only bounded interaction fields", () => {
  const risk = riskDecisionView({
    priorities: [{
      opportunity_id: "risk-cache",
      policy_type: "cache_hit_rate",
      risk_domain: "efficiency",
      base_version: "cache-policy-v9",
      anomaly_id: "anomaly-cache",
      anomaly_status: "open",
      applicable_actions: ["acknowledge", "suppress", "execute", "<script>"],
      recommendation: "先复核缓存资格与失效窗口。",
      evidence_refs: ["req_cache_001", "run-private"],
      prompt: "must-not-render",
    }],
    selected_evidence_summaries: [{
      request_ref: "req_cache_001",
      request_name: "缓存复用检查",
      operation: "分析数据",
      model_label: "通用分析模型",
      signal: { metric: "request", value: 1, unit: "requests" },
      cache_state: "miss",
      status: "succeeded",
      visible_answer_summary: "已返回可见分析摘要",
      technical_refs: {
        request_ref: "req_cache_001",
        run_id: "run-safe",
        trace_id: "trace-safe",
        correlation_id: "cor-safe",
        provider_response_id: "provider-secret",
      },
      raw_identity: "person@example.test",
      internal_error: "secret stack",
    }],
  });

  assert.equal(risk.priorities[0].baseVersion, "cache-policy-v9");
  assert.equal(risk.priorities[0].anomalyId, "anomaly-cache");
  assert.equal(risk.priorities[0].summary, "先复核缓存资格与失效窗口。");
  assert.deepEqual(risk.priorities[0].applicableActions, ["acknowledge", "suppress"]);
  assert.equal(risk.evidence[0].visibleAnswerSummary, "已返回可见分析摘要");
  assert.deepEqual(risk.evidence[0].technical, {
    requestRef: "req_cache_001",
    runId: "run-safe",
    traceId: "trace-safe",
    correlationId: "cor-safe",
  });
  assert.doesNotMatch(JSON.stringify(risk), /must-not-render|provider-secret|person@example|secret stack/);

  const draft = remediationDraftView({
    draft_id: "draft-safe",
    workspace_id: "ws-a",
    scope: {
      workspace_id: "ws-a",
      agent_id: "agent-safe",
      model: "model-safe",
      operation: "analysis",
      resource_id: "must-not-render",
    },
  });
  assert.deepEqual(draft.scope, {
    workspaceId: "ws-a",
    agentId: "agent-safe",
    model: "model-safe",
    operation: "analysis",
  });
  assert.doesNotMatch(JSON.stringify(draft), /resource_id|must-not-render/);
});


test("risk decision view localizes internal evidence terms for customer-facing cards", () => {
  const view = riskDecisionView({
    priorities: [{
      opportunity_id: "opp-coverage",
      policy_type: "apim_coverage",
      risk_domain: "governance",
      recommendation: "定位app_observed、unmanaged或unknown调用链。",
      impact: "high",
      confidence: "high",
      effort: "medium",
      evidence_refs: ["req_coverage_001"],
    }],
    selected_evidence_summaries: [{
      request_ref: "req_coverage_001",
      signal: { metric: "gateway_coverage", value: null, unit: "" },
      status: "failed",
      error_category: "provider_5xx",
    }],
  });

  assert.equal(view.priorities[0].summary, "定位应用侧已观测、未纳入统一入口或来源待确认调用链。");
  assert.equal(view.evidence[0].signal.metric, "入口治理覆盖");
  assert.equal(view.evidence[0].errorCategory, "模型服务异常");
  assert.doesNotMatch(JSON.stringify(view), /gateway_coverage|app_observed|unmanaged|unknown|provider_5xx/);
});


test("risk evidence projects numeric and allowlisted string signals with customer labels", () => {
  const signals = [
    { metric: "latency_ms", value: 6200, unit: "ms", expectedMetric: "响应时延", expectedValue: "6,200 毫秒" },
    { metric: "request_status", value: "failed", unit: "status", expectedMetric: "调用状态", expectedValue: "调用失败" },
    { metric: "pricing_status", value: "unpriced", unit: "status", expectedMetric: "计价状态", expectedValue: "未计价" },
    { metric: "cache_state", value: "miss", unit: "state", expectedMetric: "缓存状态", expectedValue: "缓存未命中" },
    { metric: "gateway_coverage", value: "unmanaged", unit: "state", expectedMetric: "入口治理覆盖", expectedValue: "未纳入统一入口" },
    { metric: "tokens_total", value: 31580, unit: "token", expectedMetric: "Token 总量", expectedValue: "31,580 Token" },
    { metric: "estimated_cost", value: 12.34, unit: "USD", expectedMetric: "估算成本", expectedValue: "$12.34" },
  ];
  const view = riskDecisionView({
    portfolio_metadata: {
      x_axis: "effort",
      y_axis: "value_impact",
      size: "sample_count",
      color: "risk_domain",
    },
    selected_evidence_summaries: signals.map((signal, index) => ({
      request_ref: `req_signal_${index}`,
      signal,
    })),
  });

  assert.equal(view.portfolio.metadata.size, "评估样本量");
  assert.equal(riskDecisionView({
    portfolio_metadata: {
      x_axis: "effort",
      y_axis: "value_impact",
      size: "affected_scope",
      color: "risk_domain",
    },
  }).portfolio.metadata.size, "评估样本量");
  assert.deepEqual(
    view.evidence.map((item) => [item.signal.metric, item.signal.valueLabel]),
    signals.map((item) => [item.expectedMetric, item.expectedValue]),
  );

  const allowlistedValues = [
    ["request_status", "succeeded", "status", "调用成功"],
    ["request_status", "failed", "status", "调用失败"],
    ["cache_state", "hit", "state", "缓存命中"],
    ["cache_state", "miss", "state", "缓存未命中"],
    ["cache_state", "bypassed", "state", "未使用缓存"],
    ["cache_state", "unavailable", "state", "状态暂不可用"],
    ["pricing_status", "priced", "status", "已计价"],
    ["pricing_status", "unpriced", "status", "未计价"],
    ["pricing_status", "estimated", "status", "估算值"],
    ["pricing_status", "unavailable", "status", "状态暂不可用"],
    ["gateway_coverage", "apim_governed", "state", "已纳入统一入口"],
    ["gateway_coverage", "app_observed", "state", "应用侧已观测"],
    ["gateway_coverage", "unmanaged", "state", "未纳入统一入口"],
    ["gateway_coverage", "unknown", "state", "来源待确认"],
    ["gateway_coverage", "unavailable", "state", "状态暂不可用"],
  ];
  const valueView = riskDecisionView({
    selected_evidence_summaries: allowlistedValues.map(([metric, value, unit], index) => ({
      request_ref: `req_value_${index}`,
      signal: { metric, value, unit },
    })),
  });
  assert.deepEqual(
    valueView.evidence.map((item) => item.signal.valueLabel),
    allowlistedValues.map(([, , , label]) => label),
  );
  assert.doesNotMatch(
    JSON.stringify(valueView.evidence),
    /"(?:failed|miss|unmanaged|apim_governed|app_observed|unknown)"/,
  );
});


test("risk evidence rejects cross-metric values, incompatible units, and unknown metrics", () => {
  const view = riskDecisionView({
    selected_evidence_summaries: [
      { request_ref: "req_invalid_status", signal: { metric: "request_status", value: "miss", unit: "status" } },
      { request_ref: "req_invalid_cache", signal: { metric: "cache_state", value: "failed", unit: "state" } },
      { request_ref: "req_invalid_pricing", signal: { metric: "pricing_status", value: "unmanaged", unit: "status" } },
      { request_ref: "req_invalid_coverage", signal: { metric: "gateway_coverage", value: "unpriced", unit: "state" } },
      { request_ref: "req_invalid_unit", signal: { metric: "cache_state", value: "miss", unit: "status" } },
      { request_ref: "req_unknown_metric", signal: { metric: "provider_internal_state", value: "miss", unit: "state" } },
    ],
  });

  assert.deepEqual(view.evidence.map((item) => item.signal.value), [null, null, null, null, null, null]);
  assert.deepEqual(view.evidence.map((item) => item.signal.valueLabel), Array(6).fill("暂不可用"));
  assert.equal(view.evidence[5].signal.metric, "运营信号");
  assert.doesNotMatch(JSON.stringify(view), /provider_internal_state|"miss"|"failed"|"unmanaged"|"unpriced"/);
});


test("risk prose preserves bounded identifiers while unknown signal metrics stay hidden", () => {
  const view = riskDecisionView({
    priorities: [{
      opportunity_id: "opp-identifiers",
      policy_type: "apim_coverage",
      risk_domain: "governance",
      recommendation: "定位 app_observed 与 gateway_coverage_v2、gateway_coverage-v2、provider_5xx-retryable、gateway_coverage.v2、risk/gateway_coverage 和 provider:provider_5xx；保留 provider_5xx_retryable。",
      impact: "high",
      confidence: "high",
      effort: "medium",
      evidence_refs: ["req_identifiers_001"],
    }],
    selected_evidence_summaries: [{
      request_ref: "req_identifiers_001",
      signal: { metric: "gateway_coverage_v2", value: null, unit: "" },
      status: "failed",
      error_category: "provider_5xx_retryable",
    }],
  });

  assert.equal(
    view.priorities[0].summary,
    "定位 应用侧已观测 与 gateway_coverage_v2、gateway_coverage-v2、provider_5xx-retryable、gateway_coverage.v2、risk/gateway_coverage 和 provider:provider_5xx；保留 provider_5xx_retryable。",
  );
  assert.equal(view.evidence[0].signal.metric, "运营信号");
  assert.equal(view.evidence[0].errorCategory, "provider_5xx_retryable");
});


test("shared charts render proportional accessible structures through Vite SSR", async (context) => {
  const server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  });
  context.after(() => server.close());
  const {
    EvidenceMaturity,
    OpportunityPortfolio,
    RiskMatrix,
    ValueBridge,
    resolveRiskPointSelection,
    toggleRiskPointSelection,
  } = await server.ssrLoadModule("/src/finops/DecisionCharts.jsx");
  const { RiskDecisionPage } = await server.ssrLoadModule("/src/finops/RiskDecisionPage.jsx");

  const bridge = renderToStaticMarkup(React.createElement("section", { className: "finops-panel" }, React.createElement(ValueBridge, {
    items: [
      { id: "small", label: "较小", value: 2, valueLabel: "$2.00", status: "estimated", badge: "情景测算", barPct: 20, explanation: "较小值说明" },
      { id: "large", label: "较大", value: 10, valueLabel: "$10.00", status: "estimated", badge: "情景测算", barPct: 100 },
    ],
    formulaRevision: "formula-v1",
  })));
  assert.match(bridge, /^<section class="finops-panel">/);
  assert.match(bridge, /finops-decision-help"><button[^>]*aria-expanded="false"/);
  assert.doesNotMatch(bridge, /<table/);
  assert.match(bridge, /finops-decision-value-formula/);
  assert.match(bridge, /finops-decision-value-term/);
  assert.match(bridge, /finops-decision-value-operator/);
  assert.doesNotMatch(bridge, /finops-decision-zero-axis/);
  assert.doesNotMatch(bridge, /finops-decision-value-track/);
  assert.match(bridge, /finops-decision-value-term finops-decision-tooltip-boundary[^>]*>[\s\S]*finops-decision-help/);

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

  const maturity = renderToStaticMarkup(React.createElement("section", { className: "finops-panel" }, React.createElement(EvidenceMaturity, {
    stages: [{
      id: "investment",
      label: "投入",
      valueLabel: "$0.00",
      badge: "已观测",
      status: "observed",
      description: "投入：$0.00；已观测",
      evidenceGap: "仍需补充业务证据",
    }],
    scoreLabel: "0%",
  })));
  assert.match(maturity, /^<section class="finops-panel">/);
  assert.match(maturity, /投入/);
  assert.match(maturity, /\$0\.00/);
  assert.match(maturity, /aria-label="投入：\$0\.00；已观测"/);
  assert.match(maturity, /<li class="finops-decision-tooltip-boundary[^>]*>[\s\S]*finops-decision-help/);

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
  assert.match(matrix, /finops-decision-risk-quadrants/);
  assert.match(matrix, /优先处置/);
  assert.match(matrix, /60 次请求/);
  assert.doesNotMatch(matrix, /finops-decision-selected/);
  assert.doesNotMatch(matrix, /aria-pressed="true"/);
  assert.equal(resolveRiskPointSelection("missing", ["risk-a"]), "");
  assert.equal(resolveRiskPointSelection("risk-a", ["risk-a"]), "risk-a");
  assert.equal(toggleRiskPointSelection("", "risk-a", ["risk-a"]), "risk-a");
  assert.equal(toggleRiskPointSelection("risk-a", "risk-a", ["risk-a"]), "");
  assert.equal(toggleRiskPointSelection("risk-a", "missing", ["risk-a"]), "");

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
  assert.match(portfolio, /当前缺少影响坐标/);

  const riskPage = renderToStaticMarkup(React.createElement(RiskDecisionPage, {
    payload: {
      decision: { state: "prioritized", title: "风险判断", summary: "按证据排序。", evidence_state: "observed" },
      risk_domains: [{ id: "experience", count: 1 }],
      risk_matrix: [{
        opportunity_id: "risk-a",
        title: "响应时延",
        policy_type: "p95_latency",
        risk_domain: "experience",
        x_confidence: 3,
        y_impact: 2,
        bubble_size: 60,
      }],
      priorities: [{
        opportunity_id: "risk-a",
        title: "响应时延",
        policy_type: "p95_latency",
        risk_domain: "experience",
        impact: "medium",
        confidence: "high",
        effort: "medium",
        sample_count: 60,
        evidence_refs: ["req_risk_a"],
      }],
      optimization_portfolio: [],
      portfolio_metadata: { x_axis: "effort", y_axis: "value_impact", size: "sample_count", color: "risk_domain" },
      selected_evidence_summaries: [],
      governance_capability: { read_enabled: true, draft_enabled: false, actions_enabled: false },
    },
  }));
  assert.match(riskPage, /评估样本量/);
  assert.match(riskPage, /运营严重度/);
  assert.doesNotMatch(riskPage, /真实影响范围|业务影响/);
});

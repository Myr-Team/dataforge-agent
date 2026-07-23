import assert from "node:assert/strict";
import test from "node:test";

import { monitorDashboardViewModel } from "./monitorDashboardViewModel.js";

test("monitor view model keeps unavailable cost and pending ROI distinct", () => {
  const view = monitorDashboardViewModel({
    summary: {
      calls: { observed: 3, succeeded: 3, failed: 0, unknown: 0 },
      tokens: { input: 80, output: 20, total: 100, known_runs: 1, unknown_runs: 2 },
      cost: { status: "unavailable", amount: null, currency: "USD" },
      quality: {
        evidence_coverage_pct: null,
        audited_runs: 1,
        rework_runs: 0,
        evaluator_coverage_pct: null,
        context_optimization: { status: "stale", sample_count: 12, evaluator_version: "context-v1", eligible: false },
      },
      roi: { status: "pending_verification", verified_value: null, model_cost: null, evaluator_cost: null, roi_pct: null },
    },
    models: [],
    routes: [],
    series: { daily: [] },
    members: [],
    scope: { kind: "current", workspace_id: "ws-a" },
    coverage: { governed_text_calls: 0, out_of_scope_image_calls: 0 },
    opportunity: { status: "unavailable", kind: null, message: "No eligible optimization evidence yet." },
  });

  assert.equal(view.cards.cost.value, "未记录");
  assert.equal(view.cards.roi.badge, "待验证");
  assert.equal(view.cards.quality.badge, "离线评估过期");
  assert.equal(view.modelRows.length, 0);
});

test("monitor view model preserves explicit unknown token splits and route coverage notes", () => {
  const view = monitorDashboardViewModel({
    summary: {
      calls: { observed: 4, succeeded: 3, failed: 1, unknown: 0 },
      tokens: { input: null, output: null, total: 160, known_runs: 2, unknown_runs: 1 },
      cost: { status: "available", amount: 1.27, currency: "USD" },
      quality: {
        evidence_coverage_pct: null,
        audited_runs: null,
        rework_runs: 1,
        evaluator_coverage_pct: null,
        context_optimization: { status: "evaluated", sample_count: 24, evaluator_version: "context-v1", eligible: true },
      },
      roi: { status: "verified", verified_value: 2800, model_cost: 42, evaluator_cost: null, roi_pct: 6566.67, currency: "CNY" },
    },
    models: [{ deployment: "gpt-5.1", route: "analysis", calls: 3, total_tokens: 120 }],
    routes: [{ route: "analysis", calls: 3, total_tokens: 120 }],
    series: { daily: [{ date: "2026-07-20", calls: 4, succeeded: 3, failed: 1, total_tokens: 160 }] },
    members: [{ member_label: "owner-a", runs: 2, total_tokens: 160, cost: { total: null, currency: null, status: "unknown" } }],
    scope: { kind: "portfolio", workspace_ids: ["ws-a", "ws-b"], workspace_count: 2 },
    coverage: { governed_text_calls: 3, out_of_scope_image_calls: 1 },
    opportunity: { status: "available", kind: "context_optimization", message: "Candidate route is eligible." },
  });

  assert.equal(view.cards.tokens.value, "160");
  assert.equal(view.cards.tokens.meta, "输入/输出未完整记录");
  assert.equal(view.cards.roi.badge, "已验证");
  assert.equal(view.coverage.governedTextLabel, "3");
  assert.equal(view.coverage.imageCallLabel, "1");
  assert.equal(view.routeRows[0].shareLabel, "100%");
});

test("monitor view model never surfaces raw actor ids when member labels are absent", () => {
  const view = monitorDashboardViewModel({
    summary: {
      calls: { observed: 1, succeeded: 1, failed: 0, unknown: 0 },
      tokens: { input: 10, output: 5, total: 15, known_runs: 1, unknown_runs: 0 },
      cost: { status: "unavailable", amount: null, currency: "USD" },
      quality: {
        evidence_coverage_pct: null,
        audited_runs: 0,
        rework_runs: 0,
        evaluator_coverage_pct: null,
        context_optimization: { status: "unavailable", sample_count: 0, evaluator_version: null, eligible: false },
      },
      roi: { status: "unavailable", verified_value: null, model_cost: null, evaluator_cost: null, roi_pct: null },
    },
    members: [{ actor_id: "raw-owner-id", runs: 1, total_tokens: 15, cost: { total: null, currency: "USD", status: "unknown" } }],
  });

  assert.equal(view.memberRows[0].label, "成员");
  assert.ok(!("raw" in view));
});

test("monitor view model preserves explicit zero token totals for member display", () => {
  const view = monitorDashboardViewModel({
    members: [{ member_label: "owner-a", runs: 1, total_tokens: 0, cost: { total: 0, currency: "USD", status: "known" } }],
  });

  assert.equal(view.memberRows[0].totalTokens, 0);
  assert.equal(view.memberRows[0].totalTokensLabel, "0");
});

test("monitor view model labels persisted model estimates without claiming verified billing", () => {
  const view = monitorDashboardViewModel({
    summary: {
      calls: { observed: 2, succeeded: 2, failed: 0, unknown: 0 },
      tokens: { input: 30, output: 10, total: 40, known_runs: 2, unknown_runs: 0 },
      cost: { status: "estimated", amount: 0.0042, currency: "USD", unpriced_calls: 0 },
      quality: { audited_runs: 0, rework_runs: 0, context_optimization: {} },
      roi: { status: "unavailable" },
    },
    models: [{ deployment: "gpt-5.6-sol", route: "sol", calls: 2, total_tokens: 40, selection_counts: { workspace_policy: 2 }, estimated_cost: { status: "estimated", amount: 0.0042, currency: "USD" } }],
    routes: [{ route: "sol", calls: 2, total_tokens: 40, selection_counts: { workspace_policy: 2 }, estimated_cost: { status: "estimated", amount: 0.0042, currency: "USD" } }],
  });

  assert.equal(view.cards.cost.value, "USD 0.00");
  assert.equal(view.cards.cost.badge, "估算");
  assert.match(view.cards.cost.meta, /Owner 维护价格卡/);
  assert.equal(view.routeRows[0].selectionLabel, "策略 2 次");
  assert.match(view.modelRows[0].secondaryLabel, /估算 USD/);
});

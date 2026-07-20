import assert from "node:assert/strict";
import test from "node:test";

import { costValueViewModel } from "./costValueViewModel.js";

test("scenario estimates cannot render as verified ROI", () => {
  const view = costValueViewModel({
    cost_evidence: { status: "incomplete", total: null },
    outcome_evidence: { status: "not_recorded" },
    realized_roi: { status: "not_recorded" },
    scenarios: [{ scenario_id: "roi_scenario_1234567890abcdef", status: "estimated", result: { roi_ratio: 1.25, currency: "CNY" } }],
    foundry_integration: { state: "not_connected", official_source: false },
  });

  assert.equal(view.realized.label, "未记录");
  assert.equal(view.scenarios[0].badge, "情景估算");
  assert.equal(view.scenarios[0].tone, "neutral");
  assert.equal(view.foundry.label, "未接入官方 ROI 数据源");
  assert.equal(view.cost.label, "数据不完整");
});

test("only a verified server result can show a monetary ROI", () => {
  const view = costValueViewModel({
    cost_evidence: { status: "complete", total: 10, currency: "USD" },
    outcome_evidence: { status: "verified", outcome_event_ids: ["outcome-1"] },
    realized_roi: { status: "verified", value: 110, currency: "USD", net_value: 100, roi_ratio: 10 },
    foundry_integration: { state: "verified", official_source: true },
  });

  assert.equal(view.realized.label, "已验证 ROI");
  assert.equal(view.realized.valueText, "USD 110.00");
  assert.equal(view.foundry.label, "官方来源已验证");
});

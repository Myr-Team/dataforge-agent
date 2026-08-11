import assert from "node:assert/strict";
import test from "node:test";

import {
  executiveCostSummary,
  executiveOverviewView,
} from "./finopsExecutiveOverview.js";


const data = {
  overview: {
    data_status: "partial",
    metrics: {
      requests: 60,
      success_rate_pct: 93.3,
      estimated_cost: {
        amount: 0.0269,
        priced_requests: 58,
        unpriced_requests: 2,
        status: "partial",
      },
      cache_hit_rate_pct: 42,
      cache: {
        eligible_requests: 50,
        estimated_savings: 0.0118,
        data_status: "observed",
      },
    },
    trust: {
      pricing: {
        coverage_pct: 96.67,
        unpriced_requests: 2,
        state: "partial",
      },
    },
  },
  department: {
    items: [
      { key: "Commerce", estimated_cost: 0.014, data_status: "available" },
      { key: "Finance", estimated_cost: 0.0129, data_status: "partial" },
    ],
  },
  anomalies: {
    items: [{
      anomaly_id: "slow",
      title: "响应时延需要关注",
      severity: "warning",
      evidence_state: "observed",
    }],
  },
  insights: {
    roi: {
      status: "insufficient_data",
      title: "业务结果仍需验证",
      evidence_state: "unavailable",
    },
  },
};


test("executive cards expose four truthful decisions without inferred successful calls", () => {
  const view = executiveOverviewView(data);

  assert.deepEqual(
    view.cards.map((item) => item.id),
    ["cost", "quality", "cache_value", "value_assessment"],
  );
  assert.equal(view.cards[1].value, "93.3%");
  assert.equal(view.cards[1].meta, "共 60 次调用");
  assert.doesNotMatch(JSON.stringify(view.cards[1]), /56/);
  assert.equal(view.cards[3].value, "证据不足");
  assert.doesNotMatch(view.cards[3].value, /%/);
});


test("cost summary preserves price coverage and unpriced requests", () => {
  assert.deepEqual(executiveCostSummary(data.overview), {
    value: "$0.0269",
    meta: "计价覆盖 96.7% · 2 次未计价",
    coverageLabel: "96.7%",
    pricedLabel: "58",
    unpricedLabel: "2",
    totalRequestsLabel: "60",
    status: "partial",
  });
});


test("department composition uses real proportions and preserves unassigned", () => {
  const view = executiveOverviewView({
    ...data,
    department: {
      items: [
        { key: "A", estimated_cost: 40, data_status: "available" },
        { key: "B", estimated_cost: 30, data_status: "available" },
        { key: "C", estimated_cost: 20, data_status: "available" },
        { key: "Unassigned", estimated_cost: 7, data_status: "partial" },
        { key: "E", estimated_cost: 3, data_status: "available" },
      ],
    },
  });

  assert.deepEqual(
    view.costComposition.segments.map((item) => [item.label, item.value, item.sharePct]),
    [["A", 40, 40], ["B", 30, 30], ["Unassigned", 7, 7], ["Other", 23, 23]],
  );
  assert.equal(view.costComposition.segments[2].evidenceState, "partial");
  assert.equal(view.costComposition.segments[3].evidenceState, "available");
});


test("no comparable positive cost produces no fabricated slices", () => {
  const view = executiveOverviewView({
    ...data,
    department: {
      items: [
        { key: "Zero", estimated_cost: 0, data_status: "available" },
        { key: "Missing", estimated_cost: null, data_status: "unavailable" },
      ],
    },
  });

  assert.deepEqual(view.costComposition.segments, []);
  assert.equal(view.costComposition.status, "unavailable");
});


test("attention is bounded and combines risk, pricing, and ROI evidence", () => {
  const view = executiveOverviewView(data);

  assert.equal(view.attention.length, 3);
  assert.deepEqual(
    view.attention.map((item) => item.id),
    ["anomaly-slow", "pricing-gap", "roi-evidence"],
  );
});


test("observed zero remains visible while missing values stay unavailable", () => {
  const view = executiveOverviewView({
    overview: {
      data_status: "complete",
      metrics: {
        requests: 0,
        success_rate_pct: 0,
        estimated_cost: { amount: 0, priced_requests: 0, unpriced_requests: 0, status: "available" },
        cache_hit_rate_pct: 0,
        cache: { estimated_savings: 0, data_status: "observed" },
      },
      trust: { pricing: { coverage_pct: 0, unpriced_requests: 0, state: "available" } },
    },
  });

  assert.equal(view.cards[0].value, "$0");
  assert.equal(view.cards[1].value, "0%");
  assert.equal(view.cards[1].meta, "共 0 次调用");
  assert.equal(view.cards[2].value, "$0");
});

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


test("operations trends wire equal-period comparison and event annotations", async () => {
  const source = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");

  assert.match(source, /loadFinOpsTrends/);
  assert.match(source, /previousEqualWindow/);
  assert.match(source, /className="finops-trend-comparison"/);
  assert.match(source, /className="finops-trend-event"/);
});

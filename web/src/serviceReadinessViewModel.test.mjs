import assert from "node:assert/strict";
import test from "node:test";

import { serviceReadinessView } from "./serviceReadinessViewModel.js";


test("service readiness view groups safe states and counts attention items", () => {
  const view = serviceReadinessView({
    generated_at: "2026-08-09T02:00:00Z",
    groups: {
      identity: { label: "身份与权限", items: [{ key: "identity", label: "登录身份", status: "ready", details: { role: "owner" } }] },
      ai: { label: "AI 服务", items: [{ key: "foundry", label: "主模型服务", status: "degraded", details: { latency_ms: 42 } }] },
      background_jobs: { label: "后台任务", items: [{ key: "rollup", label: "运营指标聚合", status: "not_run", last_completed_at: null, details: {} }] },
    },
  });

  assert.equal(view.summary.total, 3);
  assert.equal(view.summary.ready, 1);
  assert.equal(view.summary.attention, 2);
  assert.deepEqual(view.groups.map((group) => group.id), ["identity", "ai", "background_jobs"]);
  assert.equal(view.groups[1].items[0].statusLabel, "需关注");
  assert.equal(view.groups[2].items[0].statusLabel, "尚未运行");
});


test("service readiness view drops unknown fields rather than rendering infrastructure values", () => {
  const serialized = JSON.stringify(serviceReadinessView({
    groups: {
      data: {
        label: "数据服务",
        items: [{
          key: "blob",
          label: "工作区文件",
          status: "ready",
          details: {
            state: "ok",
            latency_ms: 12,
            endpoint: "https://private.invalid",
            secret: "never-render",
          },
        }],
      },
    },
  }));

  assert.doesNotMatch(serialized, /private\.invalid|never-render|endpoint|secret/);
});

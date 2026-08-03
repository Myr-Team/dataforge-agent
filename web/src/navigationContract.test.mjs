import assert from "node:assert/strict";
import test from "node:test";

import { NAV_GROUPS, visibleNavItems } from "./constants.js";

test("navigation keeps business operations and system groups visible", () => {
  const workspace = NAV_GROUPS.find((group) => group.id === "workspace");
  const operations = NAV_GROUPS.find((group) => group.id === "operations");
  const system = NAV_GROUPS.find((group) => group.id === "system");

  assert.equal(workspace.label, "业务工作台");
  assert.deepEqual(workspace.items.map((item) => item.id), ["workspaces", "data", "conversations", "runs", "artifacts"]);
  assert.equal(workspace.items.find((item) => item.id === "conversations")?.label, "会话");
  assert.equal(workspace.items.find((item) => item.id === "runs")?.label, "运行记录");
  assert.equal(operations.label, "运营治理");
  assert.deepEqual(operations.items.map((item) => item.id), ["finops", "finops-risk"]);
  assert.equal(operations.items[0].label, "成本管理");
  assert.equal(operations.items[1].label, "风险与优化");
  assert.equal(system.label, "系统");
  assert.deepEqual(system.items.map((item) => item.id), ["settings"]);
  assert.equal(NAV_GROUPS.some((group) => group.items.some((item) => ["lineage", "monitor", "model-routing"].includes(item.id))), false);
});

test("unavailable governance capabilities do not make primary groups appear late", () => {
  const items = visibleNavItems({ sections: {
    members: { visible: true },
    lineage: { visible: true },
    monitor: { visible: false },
    model_routing: { visible: false },
    finops: { visible: false },
  } });

  assert.ok(items.some((item) => item.id === "settings"));
  assert.ok(items.some((item) => item.id === "finops"));
  assert.ok(items.some((item) => item.id === "finops-risk"));
  assert.ok(items.some((item) => item.id === "runs"));
  assert.ok(!items.some((item) => item.id === "members"));
  assert.ok(!items.some((item) => item.id === "lineage"));
  assert.ok(!items.some((item) => item.id === "monitor"));
  assert.ok(!items.some((item) => item.id === "model-routing"));
});

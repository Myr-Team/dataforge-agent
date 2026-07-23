import assert from "node:assert/strict";
import test from "node:test";

import { NAV_ITEMS, NAV_GROUPS, normalizePrimaryView, resolvePrimaryView, visibleNavItems } from "./constants.js";

const ownerCapabilities = {
  sections: {
    members: { visible: true, write: true },
    lineage: { visible: true, scope: "workspace" },
    cost_value: { visible: true },
    models_connections: { visible: true },
    settings: { visible: true },
  },
};

test("editor navigation excludes owner-only governance entries", () => {
  const items = visibleNavItems({
    sections: {
      members: { visible: true, write: false },
      lineage: { visible: true, scope: "self" },
      cost_value: { visible: false },
      models_connections: { visible: false },
      settings: { visible: false },
    },
  });

  assert.deepEqual(items.map((item) => item.id), [
    "workspaces", "data", "runs", "conversations", "artifacts", "members", "lineage",
  ]);
});

test("governance navigation is grouped after the operational workspace routes", () => {
  const workspaceGroup = NAV_GROUPS.find((group) => group.id === "workspace");
  const governanceGroup = NAV_GROUPS.find((group) => group.id === "governance");

  assert.deepEqual(workspaceGroup.items.map((item) => item.id), ["workspaces", "data", "runs", "conversations", "artifacts"]);
  assert.deepEqual(governanceGroup.items.map((item) => item.id), ["members", "lineage", "cost-value", "models-connections", "settings"]);
  assert.equal(NAV_ITEMS.find((item) => item.id === "cost-value")?.capabilityKey, "cost_value");
});

test("legacy governance routes resolve to their focused governance pages", () => {
  assert.equal(normalizePrimaryView("governance"), "lineage");
  assert.equal(normalizePrimaryView("monitor"), "cost-value");
  assert.equal(normalizePrimaryView("settings"), "settings");
});

test("owner-only routes fall back until server capabilities explicitly allow them", () => {
  assert.equal(resolvePrimaryView("cost-value", null), "workspaces");
  assert.equal(resolvePrimaryView("monitor", null), "workspaces");
  assert.equal(resolvePrimaryView("cost-value", { sections: { cost_value: { visible: false } } }), "workspaces");
  assert.equal(resolvePrimaryView("cost-value", ownerCapabilities), "cost-value");
});

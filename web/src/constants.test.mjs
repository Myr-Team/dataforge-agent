import assert from "node:assert/strict";
import test from "node:test";

import { NAV_ITEMS, NAV_GROUPS, normalizePrimaryView, resolvePrimaryView, visibleNavItems } from "./constants.js";

const ownerCapabilities = {
  sections: {
    members: { visible: true, write: true },
    lineage: { visible: true, scope: "workspace" },
    monitor: { visible: true },
    model_routing: { visible: true },
  },
};

test("editor navigation excludes owner-only governance entries", () => {
  const items = visibleNavItems({
    sections: {
      members: { visible: true, write: false },
      lineage: { visible: true, scope: "self" },
      monitor: { visible: false },
      model_routing: { visible: false },
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
  assert.deepEqual(governanceGroup.items.map((item) => item.id), ["members", "lineage", "monitor", "model-routing"]);
  assert.equal(NAV_ITEMS.find((item) => item.id === "monitor")?.capabilityKey, "monitor");
});

test("legacy governance routes resolve to their focused governance pages", () => {
  assert.equal(normalizePrimaryView("governance"), "lineage");
  assert.equal(normalizePrimaryView("cost-value"), "monitor");
  assert.equal(normalizePrimaryView("models-connections"), "model-routing");
  assert.equal(normalizePrimaryView("settings"), "workspaces");
});

test("owner-only routes fall back until server capabilities explicitly allow them", () => {
  assert.equal(resolvePrimaryView("monitor", null), "workspaces");
  assert.equal(resolvePrimaryView("monitor", null), "workspaces");
  assert.equal(resolvePrimaryView("monitor", { sections: { monitor: { visible: false } } }), "workspaces");
  assert.equal(resolvePrimaryView("monitor", ownerCapabilities), "monitor");
});

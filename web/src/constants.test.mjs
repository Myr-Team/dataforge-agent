import assert from "node:assert/strict";
import test from "node:test";

import {
  NAV_ITEMS,
  NAV_GROUPS,
  navigationAccessState,
  normalizePrimaryView,
  resolvePrimaryView,
  visibleNavItems,
} from "./constants.js";

const ownerCapabilities = {
  sections: {
    members: { visible: true, write: true },
    lineage: { visible: true, scope: "workspace" },
    monitor: { visible: true },
    model_routing: { visible: true },
    finops: { visible: true },
  },
};

test("primary navigation is structurally complete before capabilities resolve", () => {
  const items = visibleNavItems({
    sections: {
      members: { visible: true, write: false },
      lineage: { visible: true, scope: "self" },
      monitor: { visible: false },
      model_routing: { visible: false },
    },
  });

  assert.deepEqual(items.map((item) => item.id), [
    "workspaces",
    "data",
    "conversations",
    "runs",
    "artifacts",
    "finops",
    "settings",
  ]);
  assert.deepEqual(visibleNavItems(null).map((item) => item.id), items.map((item) => item.id));
});

test("operations dashboard is grouped between business workbench and system settings", () => {
  const workspaceGroup = NAV_GROUPS.find((group) => group.id === "workspace");
  const operationsGroup = NAV_GROUPS.find((group) => group.id === "operations");
  const systemGroup = NAV_GROUPS.find((group) => group.id === "system");

  assert.deepEqual(workspaceGroup.items.map((item) => item.id), ["workspaces", "data", "conversations", "runs", "artifacts"]);
  assert.deepEqual(operationsGroup.items.map((item) => item.id), ["finops"]);
  assert.deepEqual(systemGroup.items.map((item) => item.id), ["settings"]);
  assert.equal(NAV_ITEMS.find((item) => item.id === "finops")?.capabilityKey, "finops");
});

test("legacy governance routes converge on the operations dashboard", () => {
  assert.equal(normalizePrimaryView("governance"), "finops");
  assert.equal(normalizePrimaryView("cost-value"), "finops");
  assert.equal(normalizePrimaryView("models-connections"), "finops");
  assert.equal(normalizePrimaryView("settings"), "settings");
});

test("operations navigation remains selected while access resolves locally", () => {
  assert.equal(resolvePrimaryView("finops", null), "finops");
  assert.equal(resolvePrimaryView("finops", { sections: { finops: { visible: false } } }), "finops");
  assert.equal(resolvePrimaryView("finops", ownerCapabilities), "finops");
  assert.equal(navigationAccessState("finops", null), "loading");
  assert.equal(
    navigationAccessState("finops", { sections: { finops: { visible: false } } }),
    "denied",
  );
  assert.equal(navigationAccessState("finops", ownerCapabilities), "allowed");
});

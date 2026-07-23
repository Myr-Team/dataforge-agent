import assert from "node:assert/strict";
import test from "node:test";

import { NAV_GROUPS, visibleNavItems } from "./constants.js";

test("governance capabilities control the visible destinations", () => {
  const governance = NAV_GROUPS.find((group) => group.id === "governance");

  assert.ok(governance);
  assert.equal(governance.label, "治理");
  assert.deepEqual(governance.items.map((item) => item.id), ["members", "lineage", "monitor", "model-routing"]);
});

test("unavailable governance capabilities do not render as disabled navigation", () => {
  const items = visibleNavItems({ sections: {
    members: { visible: true },
    lineage: { visible: true },
    monitor: { visible: false },
    model_routing: { visible: false },
  } });

  assert.ok(items.some((item) => item.id === "members"));
  assert.ok(items.some((item) => item.id === "lineage"));
  assert.ok(!items.some((item) => item.id === "monitor"));
  assert.ok(!items.some((item) => item.id === "model-routing"));
});

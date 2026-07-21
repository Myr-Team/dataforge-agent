import assert from "node:assert/strict";
import test from "node:test";

import { NAV_ITEMS, visibleNavItems } from "./constants.js";

test("governance and ROI is a first-class workspace destination", () => {
  const governance = NAV_ITEMS.find((item) => item.id === "governance");

  assert.ok(governance);
  assert.equal(governance.label, "治理与 ROI");
  assert.ok(NAV_ITEMS.findIndex((item) => item.id === "governance") < NAV_ITEMS.findIndex((item) => item.id === "settings"));
});

test("governance navigation is available only to the persisted workspace owner", () => {
  const ownerItems = visibleNavItems({ allowed: true, role: "owner" });
  const adminItems = visibleNavItems({ allowed: true, role: "admin" });
  const unknownItems = visibleNavItems(null);

  assert.ok(ownerItems.some((item) => item.id === "governance"));
  assert.ok(!adminItems.some((item) => item.id === "governance"));
  assert.ok(!unknownItems.some((item) => item.id === "governance"));
});

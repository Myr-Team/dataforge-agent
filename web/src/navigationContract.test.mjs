import assert from "node:assert/strict";
import test from "node:test";

import { NAV_ITEMS } from "./constants.js";

test("governance and ROI is a first-class workspace destination", () => {
  const governance = NAV_ITEMS.find((item) => item.id === "governance");

  assert.ok(governance);
  assert.equal(governance.label, "治理与 ROI");
  assert.ok(NAV_ITEMS.findIndex((item) => item.id === "governance") < NAV_ITEMS.findIndex((item) => item.id === "settings"));
});

import assert from "node:assert/strict";
import test from "node:test";

import { NAV_ITEMS, normalizePrimaryView, visibleNavItems } from "./constants.js";

test("monitor navigation is visible only to a workspace owner", () => {
  const ownerItems = visibleNavItems({ allowed: true, role: "owner" });
  const editorItems = visibleNavItems({ allowed: true, role: "editor" });

  assert.ok(ownerItems.some((item) => item.id === "monitor"));
  assert.ok(!editorItems.some((item) => item.id === "monitor"));
});

test("monitor navigation appears before settings as a first-class workspace view", () => {
  const monitorIndex = NAV_ITEMS.findIndex((item) => item.id === "monitor");
  const settingsIndex = NAV_ITEMS.findIndex((item) => item.id === "settings");

  assert.ok(monitorIndex >= 0);
  assert.ok(settingsIndex >= 0);
  assert.ok(monitorIndex < settingsIndex);
});

test("legacy governance view resolves to the monitor workspace route", () => {
  assert.equal(normalizePrimaryView("governance"), "monitor");
  assert.equal(normalizePrimaryView("monitor"), "monitor");
  assert.equal(normalizePrimaryView("settings"), "settings");
});

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("settings can open identity governance as a first-class destination", async () => {
  const governance = await readFile(new URL("./ModelGovernanceSettings.jsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("./components.jsx", import.meta.url), "utf8");

  assert.match(governance, /initialTab = "agents"/);
  assert.match(governance, /useState\(safeInitialTab\)/);
  assert.match(shell, /身份与访问/);
  assert.match(shell, /setSettingsDrawer\("identity"\)/);
  assert.match(shell, /initialTab=\{settingsDrawer === "identity" \? "identity" : "agents"\}/);
});

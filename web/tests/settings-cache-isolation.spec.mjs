import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("Provider secret draft is removed synchronously on a session scope switch", async ({ page }) => {
  let scope = "a";
  let releaseB;
  await page.addInitScript(() => { window.__DF_FORCE_AUTH_SESSION__ = true; });
  await installFinOpsMockApi(page);
  await page.route("**/api/auth/session", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    authenticated: true, name: "Portal User", email: "owner@contoso.test", identity_provider: "microsoft_entra", identity_source: "trusted_proxy",
    tenant_ref: `tenant_provider_${scope}`, actor_ref: `actor_provider_${scope}`, session_ref: `session_provider_${scope}`,
  }) }));
  await page.route("**/api/model-providers", async (route) => {
    if (scope === "b") await new Promise((resolve) => { releaseB = resolve; });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], revision: 1 }) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();
  await page.locator(".set-cfg").first().getByRole("button").click();
  await page.getByRole("button", { name: "模型提供商" }).click();
  const secret = page.locator(".provider-key-input input");
  await secret.fill("test-secret-marker");
  scope = "b";
  await page.evaluate(() => window.dispatchEvent(new Event("dataforge:refresh-session")));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  await expect(secret).toHaveValue("");
  expect(releaseB).toBeTruthy();
  releaseB();
});

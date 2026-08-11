import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("Provider secret draft is removed synchronously on a session scope switch", async ({ page }) => {
  let scope = "a";
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


test("Model routing draft cannot survive a delayed scope switch", async ({ page }) => {
  let scope = "a";
  let releaseB;
  await page.addInitScript(() => { window.__DF_FORCE_AUTH_SESSION__ = true; });
  await installFinOpsMockApi(page);
  await page.route("**/api/auth/session", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    authenticated: true, name: "Portal User", email: "owner@contoso.test", identity_provider: "microsoft_entra", identity_source: "trusted_proxy",
    tenant_ref: `tenant_routing_${scope}`, actor_ref: `actor_routing_${scope}`, session_ref: `session_routing_${scope}`,
  }) }));
  await page.route("**/api/workspaces/demo-corpus/governance/model-routing", async (route) => {
    if (scope === "b") await new Promise((resolve) => { releaseB = resolve; });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      workspace_id: "demo-corpus", default_route: "analysis",
      routes: [
        { id: "analysis", deployment: "gpt-5.1", label: "GPT-5.1", capabilities: ["analysis", "chat"], selectable: true, provider_label: "Azure Foundry" },
        { id: "terra", deployment: "gpt-5.6-terra", label: "GPT-5.6 Terra", capabilities: ["analysis", "chat"], selectable: true, provider_label: "Azure Foundry" },
      ],
      policy: { revision: 3, default_route_id: "analysis", assignments: {}, agent_assignments: {} },
    }) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();
  await page.locator(".set-cfg").first().getByRole("button").click();
  const assignment = page.locator('select[aria-label$="主要模型"]').first();
  await expect(assignment).toBeVisible();
  await assignment.selectOption("terra");
  scope = "b";
  await page.evaluate(() => window.dispatchEvent(new Event("dataforge:refresh-session")));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  await expect(assignment).toHaveCount(0);
  expect(releaseB).toBeTruthy();
  releaseB();
});


test("Member budget mail modal is removed on a delayed scope switch", async ({ page }) => {
  let scope = "a";
  await page.addInitScript(() => { window.__DF_FORCE_AUTH_SESSION__ = true; });
  await installFinOpsMockApi(page);
  await page.route("**/api/auth/session", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    authenticated: true, name: "Portal User", email: "owner@contoso.test", identity_provider: "microsoft_entra", identity_source: "trusted_proxy",
    tenant_ref: `tenant_budget_${scope}`, actor_ref: `actor_budget_${scope}`, session_ref: `session_budget_${scope}`,
  }) }));
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();
  await page.getByRole("button", { name: "配置邮件" }).click();
  const dialog = page.getByRole("dialog", { name: "邮件提醒设置" });
  await dialog.getByLabel("管理员收件邮箱").fill("budget-secret-marker@example.test");
  scope = "b";
  await page.evaluate(() => window.dispatchEvent(new Event("dataforge:refresh-session")));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  await expect(dialog).toHaveCount(0);
});

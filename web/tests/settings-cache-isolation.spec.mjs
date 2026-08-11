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
  let releaseB;
  await page.addInitScript(() => { window.__DF_FORCE_AUTH_SESSION__ = true; });
  await installFinOpsMockApi(page);
  await page.route("**/api/auth/session", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    authenticated: true, name: "Portal User", email: "owner@contoso.test", identity_provider: "microsoft_entra", identity_source: "trusted_proxy",
    tenant_ref: `tenant_budget_${scope}`, actor_ref: `actor_budget_${scope}`, session_ref: `session_budget_${scope}`,
  }) }));
  await page.route("**/api/finops/member-budgets**", async (route) => {
    if (scope === "b") await new Promise((resolve) => { releaseB = resolve; });
    await route.fallback();
  });
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
  expect(releaseB).toBeTruthy();
  releaseB();
  await expect(page.getByText("Finance Admin").first()).toBeVisible();
});


async function assertMemberBudgetStaleRecovery(page, failedResources) {
  const counts = { budget: 0, members: 0, notification: 0, alerts: 0 };
  let releaseSecond;
  const secondGate = new Promise((resolve) => { releaseSecond = resolve; });
  await page.clock.install({ time: new Date("2026-08-11T00:00:00Z") });
  await installFinOpsMockApi(page);
  const failSecond = (name) => async (route) => {
    counts[name] += 1;
    if (counts[name] === 1) return route.fallback();
    if (!failedResources.includes(name)) return route.fallback();
    await secondGate;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "refresh unavailable" }) });
  };
  await page.route("**/api/finops/member-budgets**", failSecond("budget"));
  await page.route("**/api/finops/member-budget-members**", failSecond("members"));
  await page.route("**/api/finops/notification-settings**", failSecond("notification"));
  await page.route("**/api/finops/budget-alerts**", failSecond("alerts"));
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();
  await expect(page.getByText("Finance Admin").first()).toBeVisible();
  await expect(page.getByText("d***@example.test")).toBeVisible();
  await expect(page.getByText("95%").first()).toBeVisible();
  await page.getByRole("button", { name: "返回设置" }).click();
  await page.clock.runFor(30_001);
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();
  await expect(page.getByText("Finance Admin").first()).toBeVisible();
  await expect.poll(() => counts).toEqual({ budget: 2, members: 2, notification: 2, alerts: 2 });
  await expect(page.getByText("d***@example.test")).toBeVisible();
  await expect(page.getByText("95%").first()).toBeVisible();
  releaseSecond();
  await expect(page.getByText("更新失败，正在显示上次可用配置。")).toBeVisible();
  await expect(page.getByText("Finance Admin").first()).toBeVisible();
  await expect(page.getByText("d***@example.test")).toBeVisible();
  await expect(page.getByText("95%").first()).toBeVisible();
}

test("Member budget stale remount retains every current-key resource after all four 500 refreshes", async ({ page }) => {
  await assertMemberBudgetStaleRecovery(page, ["budget", "members", "notification", "alerts"]);
});

for (const resource of ["budget", "members", "notification", "alerts"]) {
  test(`Member budget stale remount retains all snapshots when ${resource} refresh fails`, async ({ page }) => {
    await assertMemberBudgetStaleRecovery(page, [resource]);
  });
}


test("Model routing stale remount retains routing catalog and mapping snapshots after 500", async ({ page }) => {
  const counts = { routing: 0, catalog: 0, mapping: 0 };
  let savedPayload = null;
  let releaseSecond;
  const secondGate = new Promise((resolve) => { releaseSecond = resolve; });
  await page.clock.install({ time: new Date("2026-08-11T00:00:00Z") });
  await installFinOpsMockApi(page);
  const failSecond = (name) => async (route) => {
    counts[name] += 1;
    if (counts[name] === 1) return route.fallback();
    await secondGate;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "refresh unavailable" }) });
  };
  await page.route("**/api/workspaces/demo-corpus/governance/model-routing**", failSecond("routing"));
  await page.route("**/api/finops/pricing/catalog**", failSecond("catalog"));
  await page.route("**/api/finops/pricing/mappings**", failSecond("mapping"));
  await page.goto("/");
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().includes("/governance/model-routing")) savedPayload = request.postDataJSON();
  });
  await page.getByRole("button", { name: "设置" }).first().click();
  await page.locator(".set-cfg").first().getByRole("button").click();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.clock.runFor(30_001);
  await page.locator(".set-cfg").first().getByRole("button").click();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await expect.poll(() => counts).toEqual({ routing: 2, catalog: 2, mapping: 2 });
  await expect(page.getByText("GPT-5.1").first()).toBeVisible();
  await expect(page.getByText("azure-retail-2026-07-27").first()).toBeVisible();
  await expect(page.locator(".routing-default-field select")).toHaveValue("analysis");
  await expect(page.locator('select[aria-label*="审计 Agent"]').first()).toHaveValue("terra");
  await expect(page.locator(".routing-price-picker select").first()).toHaveValue("azure-openai:gpt-5.1:global-standard:global");
  await expect(page.getByRole("button", { name: "保存模型分配" })).toBeEnabled();
  releaseSecond();
  await expect(page.getByText("更新失败，正在显示上次可用配置。")).toBeVisible();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await expect(page.getByText("GPT-5.1").first()).toBeVisible();
  await expect(page.getByText("azure-retail-2026-07-27").first()).toBeVisible();
  await expect(page.locator(".routing-default-field select")).toHaveValue("analysis");
  await expect(page.locator('select[aria-label*="审计 Agent"]').first()).toHaveValue("terra");
  await expect(page.locator(".routing-price-picker select").first()).toHaveValue("azure-openai:gpt-5.1:global-standard:global");
  await expect(page.getByRole("button", { name: "保存模型分配" })).toBeEnabled();
  await page.getByRole("button", { name: "保存模型分配" }).click();
  await expect.poll(() => savedPayload).not.toBeNull();
  expect(savedPayload).toMatchObject({
    base_revision: 3,
    default_route_id: "analysis",
    agent_assignments: {
      "df-auditor": { primary_route_id: "terra", fallback_route_id: "analysis" },
    },
  });
});

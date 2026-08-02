import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("metric-aware assistant remains compact, contextual and evidence-bound", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).first().click();

  const cacheMetric = page.locator(".finops-metric").filter({ hasText: "缓存收益" });
  await cacheMetric.getByRole("button", { name: "问 AI" }).click();

  const dialog = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("缓存收益").first()).toBeVisible();
  await dialog.getByRole("button", { name: "为什么发生变化？" }).click();
  await expect(dialog.getByText(/50 次可缓存调用/)).toBeVisible();

  expect(calls.filter((item) => item.path === "/api/finops/assistant/query")).toHaveLength(1);
  expect(calls.some((item) => /\/approve$|\/execute$|\/submit$/.test(item.path))).toBe(false);
  await expect(page.locator(".finops-drawer-backdrop")).toHaveCount(0);
});

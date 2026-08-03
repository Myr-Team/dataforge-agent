import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("metric-aware assistant remains compact, contextual and evidence-bound", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();

  const cacheMetric = page.locator(".finops-metric").filter({ hasText: "缓存收益" });
  await cacheMetric.getByRole("button", { name: "问 AI" }).click();

  const dialog = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("缓存收益").first()).toBeVisible();
  await expect(dialog.getByText("结论", { exact: true })).toBeVisible();
  await expect(dialog.getByText("依据", { exact: true })).toBeVisible();
  await expect(dialog.getByText("建议", { exact: true })).toBeVisible();

  expect(calls.filter((item) => item.path === "/api/finops/assistant/query")).toHaveLength(1);
  const submitted = JSON.parse(calls.find((item) => item.path === "/api/finops/assistant/query").body);
  expect(submitted.question).toContain("缓存收益");
  expect(calls.some((item) => /\/approve$|\/execute$|\/submit$/.test(item.path))).toBe(false);
  await expect(page.locator(".finops-drawer-backdrop")).toHaveCount(0);
});

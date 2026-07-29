import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("operations management is immediately discoverable and supports metric drilldown", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });

  await expect(page.getByRole("button", { name: "运营管理" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "运行记录" }).first()).toBeVisible();
  await page.getByRole("button", { name: "运营管理" }).first().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();
  await expect(page.locator(".finops-live")).toContainText("更新");

  const tokenMetric = page.locator(".finops-metric").filter({ hasText: "Token" });
  await tokenMetric.focus();
  await expect(tokenMetric.locator(".finops-metric-tooltip")).toHaveCSS("opacity", "0");
  const tokenHelp = tokenMetric.locator(".finops-help-trigger");
  await expect(tokenHelp).toBeVisible();
  await tokenHelp.focus();
  await expect(tokenMetric.locator(".finops-metric-tooltip")).toHaveCSS("opacity", "1");
  await expect(tokenMetric.locator(".finops-metric-tooltip")).toContainText("缓存输入");

  await expect(page.locator(".finops-trend-event")).toHaveCount(0);
  const latestTrend = page.locator(".finops-trend-column").filter({ hasText: "752 Token" });
  await expect(latestTrend).toHaveCount(1);
  await latestTrend.hover();
  await expect(latestTrend.locator(".finops-trend-tooltip")).toContainText("1");

  await page.getByText("Commerce", { exact: true }).last().click();
  await expect(page.getByRole("button", { name: /移除部门筛选 Commerce/ })).toBeVisible();

  await expect(page.getByText("数据可信度")).toBeVisible();
  await expect(page.getByText("计价覆盖")).toBeVisible();
  await page.getByRole("button", { name: "关联官方模型价格" }).click();
  await expect(page.getByRole("dialog", { name: "模型分配与官方价格" })).toBeVisible();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await expect(page.getByText("审计 Agent")).toBeVisible();
  await expect(page.getByLabel("审计 Agent主要模型")).toHaveValue("terra");
  const terraPrice = page.getByLabel("GPT-5.6 Terra官方价格记录");
  await expect(terraPrice.locator('option[value="azure-openai:gpt-5.6-sol:global-standard:global"]')).toContainText("GPT-5.6 Sol");
  await expect(terraPrice.locator('option[value="azure-openai:gpt-5.6-terra:global-standard:global"]')).toContainText("GPT-5.6 Terra");
  await expect(terraPrice.locator('option[value="azure-openai:gpt-5.6-luna:global-standard:global"]')).toContainText("GPT-5.6 Luna");
  await expect(page.locator(".routing-price-status.unpriced")).toHaveCount(1);
  await page.screenshot({
    path: path.join(outputDir, "operations-model-settings-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭模型配置" }).click();
  await page.screenshot({
    path: path.join(outputDir, "operations-management-overview-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "成本分析" }).click();
  await expect(page.getByText("成本趋势")).toBeVisible();
  await expect(page.getByText("Agent 成本结构")).toBeVisible();
  await expect(page.getByText("模型成本结构")).toBeVisible();

  await page.getByRole("button", { name: "效能与 ROI" }).click();
  await expect(page.getByText("ROI 证据漏斗")).toBeVisible();
  await expect(page.getByText("证据不足").first()).toBeVisible();

  await page.getByRole("button", { name: "风险与优化" }).click();
  await expect(page.getByText("优化机会队列")).toBeVisible();
  await expect(page.getByText("建议 · 需人工审批")).toBeVisible();

  await page.screenshot({
    path: path.join(outputDir, "operations-management-desktop.png"),
    fullPage: true,
  });
});


test("operations management stays usable on mobile without a full-screen AI drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).last().click();

  await page.getByRole("button", { name: "运营 AI" }).click();
  const dialog = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(dialog).toBeVisible();
  const bounds = await dialog.boundingBox();
  expect(bounds.width).toBeLessThanOrEqual(362);
  expect(bounds.height).toBeLessThan(790);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-management-mobile.png"),
    fullPage: true,
  });
});


test("settings opens the same persisted Agent model configuration", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  const modelCard = page.locator(".set-cfg").filter({ hasText: "模型与生成" });
  await modelCard.getByRole("button", { name: "配置模型与生成" }).click();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await expect(page.getByLabel("审计 Agent主要模型")).toHaveValue("terra");
  await expect(page.locator(".side-drawer.wide")).toBeVisible();
});

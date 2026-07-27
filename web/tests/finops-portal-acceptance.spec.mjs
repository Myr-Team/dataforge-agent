import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openOperations(page) {
  await page.getByRole("button", { name: "运营管理" }).first().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();
}


test("trend chart switches metric, unit and tooltip in sync", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const legend = page.locator(".finops-trend-legend");
  const scale = page.locator(".finops-trend-scale");
  const trendSwitch = page.locator(".finops-trend-switch");

  await expect(legend).toContainText("输入");

  await trendSwitch.getByRole("button", { name: "成本" }).click();
  await expect(legend).toContainText("估算成本");
  await expect(scale.locator("span").first()).toContainText("$");

  await trendSwitch.getByRole("button", { name: "P95" }).click();
  await expect(legend).toContainText("P95 延迟");
  await expect(scale.locator("span").first()).toContainText(/s|ms/);

  await trendSwitch.getByRole("button", { name: "调用" }).click();
  await expect(legend).toContainText("调用次数");
  const column = page.locator(".finops-trend-column").first();
  await expect(column).toHaveAttribute("aria-label", /次/);
  await column.focus();
  await expect(column.locator(".finops-trend-tooltip")).toContainText("调用次数");
});


test("APIM coverage surfaces unattributed gateway evidence with scope label", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const evidence = page.locator(".finops-gateway-evidence");
  await expect(evidence).toBeVisible();
  await expect(evidence).toContainText("未归属网关证据");
  await expect(evidence.locator(".finops-scope-tag")).toContainText("unattributed");
  await expect(evidence).toContainText("已关联请求");
  await expect(evidence).toContainText("未关联网关错误");
  await expect(evidence).toContainText("4xx 3");
  await expect(evidence).toContainText("5xx 1");
  await expect(evidence).toContainText("数据更新时间");

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-gateway-evidence-desktop.png"),
    fullPage: true,
  });
});


test("bootstrap failure shows a friendly error and recovers on retry", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const control = await installFinOpsMockApi(page, [], { failBootstrap: true });
  await page.goto("/");
  await openOperations(page);

  const errorState = page.locator(".finops-state-error");
  await expect(errorState).toBeVisible();
  await expect(errorState).toContainText("FinOps evidence service is unavailable");

  const bodyText = await page.locator("body").innerText();
  expect(bodyText).not.toContain("Failed to fetch");
  expect(bodyText).not.toContain("req_aaaaaaaaaaaa");
  expect(bodyText).not.toContain("4f8b0f37b5824af5a2ac7ed9129ee70b");

  control.failBootstrap = false;
  await errorState.getByRole("button", { name: "重试" }).click();
  await expect(page.locator(".finops-metric").first()).toBeVisible();
  await expect(page.getByText("数据可信度")).toBeVisible();

  expect(pageErrors).toEqual([]);
});


test("mobile operations layout has no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).last().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();

  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);

  await expect(page.getByRole("button", { name: "成本分析" }).first()).toBeVisible();
  await page.getByRole("button", { name: "成本分析" }).first().click();
  await expect(page.getByText("成本趋势")).toBeVisible();
});

import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("owner drills from operations metric into friendly request evidence", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "运营管理" }).first().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();
  await expect(page.getByText("数据更新中")).not.toBeVisible();

  await page.getByRole("button", { name: "查看证据" }).first().click();
  const dialog = page.getByRole("dialog", { name: /Commerce · 分析运行/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("分析本月销售异常")).toBeVisible();
  await expect(dialog.getByText("已定位主要变化来自华东区域。")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: /req_/ })).toHaveCount(0);

  const technical = dialog.locator("details");
  await expect(technical).not.toHaveAttribute("open", "");
  await technical.locator("summary").click();
  await expect(dialog.getByText("req_aaaaaaaaaaaa")).toBeVisible();

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "finops-evidence-desktop.png"),
    fullPage: true,
  });

  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
});


test("evidence drawer is full-width and keyboard closable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "运营管理" }).last().click();
  await page.getByRole("button", { name: "查看证据" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const width = await dialog.evaluate((node) => node.getBoundingClientRect().width);
  expect(width).toBeGreaterThanOrEqual(389);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "finops-evidence-mobile.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
});

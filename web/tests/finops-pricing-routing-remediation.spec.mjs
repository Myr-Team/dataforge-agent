import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openModelSettings(page) {
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).first().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();
  await page.getByRole("button", { name: "关联官方模型价格" }).click();
  await expect(page.getByRole("dialog", { name: "模型分配与官方价格" })).toBeVisible();
}


test("stale model routing save surfaces a conflict and reloads the latest revision", async ({ page }) => {
  await installFinOpsMockApi(page);
  // A concurrent owner already advanced the policy, so the server rejects the stale write.
  await page.route("**/api/workspaces/demo-corpus/governance/model-routing", async (route) => {
    if (route.request().method() === "PUT") {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "model routing policy revision conflict: expected base_revision 3",
        }),
      });
      return;
    }
    await route.fallback();
  });

  await openModelSettings(page);
  await page.getByRole("button", { name: "保存模型分配" }).click();

  await expect(page.getByRole("alert")).toContainText("已被其他管理员更新");

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "model-routing-revision-conflict.png"),
    fullPage: true,
  });
});


test("removing a wrong price mapping restores the unpriced state", async ({ page }) => {
  await installFinOpsMockApi(page);
  let deleteCalls = 0;
  await page.route("**/api/finops/pricing/mappings/*", async (route) => {
    if (route.request().method() === "DELETE") {
      deleteCalls += 1;
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fallback();
  });

  await openModelSettings(page);

  // The mock ships GPT-5.1 as mapped (已计价) and Terra unpriced.
  await expect(page.locator(".routing-price-status.unpriced")).toHaveCount(1);
  await page.getByRole("button", { name: "解除GPT-5.1官方价格关联" }).click();

  await expect(page.getByText("恢复未计价")).toBeVisible();
  await expect(page.locator(".routing-price-status.unpriced")).toHaveCount(2);
  expect(deleteCalls).toBe(1);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "price-mapping-restore-unpriced.png"),
    fullPage: true,
  });
});

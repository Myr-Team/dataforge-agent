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

async function openProviderSettings(page) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await page.locator(".set-cfg").filter({ hasText: "模型与生成" }).getByRole("button", { name: "管理" }).click();
  await page.getByRole("button", { name: "模型提供商" }).click();
  await expect(page.getByTestId("provider-connections-page")).toBeVisible();
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

test("Bedrock credentials clear after safe save and discovery remains outside Agent routing", async ({ page }) => {
  await installFinOpsMockApi(page);
  await openProviderSettings(page);

  const createForm = page.locator(".provider-connections > .bedrock-connection-form");
  await expect(createForm).toBeVisible();
  await createForm.getByLabel("Access Key ID").fill("not-a-real-access-key");
  await createForm.getByLabel("Secret Access Key").fill("not-a-real-secret");
  await createForm.getByLabel("Session Token（可选）").fill("not-a-real-session-token");
  await createForm.getByRole("button", { name: "保存并测试连接" }).click();

  await expect(createForm.getByLabel("Access Key ID")).toHaveValue("");
  await expect(createForm.getByLabel("Secret Access Key")).toHaveValue("");
  await expect(createForm.getByLabel("Session Token（可选）")).toHaveValue("");
  await expect(page.getByText("配置测试可用", { exact: true })).toBeVisible();
  await expect(page.getByText("尚未进入 Agent 路由", { exact: true })).toBeVisible();

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "bedrock-provider-desktop-connected.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "Agent 模型" }).click();
  await expect(page.getByText("AWS Bedrock", { exact: true })).toHaveCount(0);
});

test("Bedrock provider layout remains usable on mobile and safely presents conflicts", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.route("**/api/model-providers", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "do not expose" }) });
      return;
    }
    await route.fallback();
  });
  await openProviderSettings(page);

  const createForm = page.locator(".provider-connections > .bedrock-connection-form");
  await createForm.getByLabel("Access Key ID").fill("not-a-real-access-key");
  await createForm.getByLabel("Secret Access Key").fill("not-a-real-secret");
  await createForm.getByRole("button", { name: "保存并测试连接" }).click();

  await expect(page.getByRole("alert")).toContainText("已被其他管理员更新");
  await expect(page.getByRole("alert")).not.toContainText("do not expose");
  await expect(createForm.getByLabel("Access Key ID")).toHaveValue("");
  await expect(createForm.getByLabel("Secret Access Key")).toHaveValue("");
  await page.getByRole("alert").scrollIntoViewIfNeeded();
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "bedrock-provider-mobile-conflict.png"),
    fullPage: true,
  });
});

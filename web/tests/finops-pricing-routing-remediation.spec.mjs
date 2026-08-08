import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openModelSettings(page) {
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await expect(page.getByRole("heading", { name: "成本管理" })).toBeVisible();
  await page.getByRole("button", { name: "关联官方模型价格" }).click();
  await expect(page.getByRole("dialog", { name: "模型分配与官方价格" })).toBeVisible();
}

async function openProviderSettings(page) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await page.locator(".set-cfg").filter({ hasText: "模型与生成" }).getByRole("button", { name: "配置模型与生成" }).click();
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
  const calls = [];
  const control = await installFinOpsMockApi(page, calls);
  await openProviderSettings(page);

  const createForm = page.locator(".provider-connections > .bedrock-connection-form");
  await expect(createForm).toBeVisible();
  await createForm.getByLabel("Access Key ID").fill("create-access-marker");
  await createForm.getByLabel("Secret Access Key").fill("create-secret-marker");
  await createForm.getByLabel("Session Token（可选）").fill("create-session-marker");
  await createForm.getByRole("button", { name: "保存并测试连接" }).click();

  assert.deepEqual(JSON.parse(calls.find((call) => call.method === "POST" && call.path === "/api/model-providers").body), {
    provider_type: "aws_bedrock",
    display_name: "AWS Bedrock",
    region: "ap-southeast-1",
    access_key_id: "create-access-marker",
    secret_access_key: "create-secret-marker",
    session_token: "create-session-marker",
  });
  await expect(createForm.getByLabel("Access Key ID")).toHaveValue("");
  await expect(createForm.getByLabel("Secret Access Key")).toHaveValue("");
  await expect(createForm.getByLabel("Session Token（可选）")).toHaveValue("");
  await expect(page.getByText("配置测试可用", { exact: true })).toBeVisible();
  await expect(page.getByText("尚未进入 Agent 路由", { exact: true })).toBeVisible();

  const rotateForm = page.locator(".provider-card .bedrock-connection-form");
  await rotateForm.getByLabel("Access Key ID").fill("rotate-access-marker");
  await rotateForm.getByLabel("Secret Access Key").fill("rotate-secret-marker");
  await rotateForm.getByLabel("Session Token（可选）").fill("rotate-session-marker");
  await rotateForm.getByRole("button", { name: "更新并测试连接" }).click();
  assert.deepEqual(JSON.parse(calls.find((call) => call.method === "POST" && call.path.endsWith("/rotate-secret")).body), {
    provider_type: "aws_bedrock",
    access_key_id: "rotate-access-marker",
    secret_access_key: "rotate-secret-marker",
    session_token: "rotate-session-marker",
    base_revision: 1,
  });
  await expect(rotateForm.getByLabel("Access Key ID")).toHaveValue("");
  await expect(rotateForm.getByLabel("Secret Access Key")).toHaveValue("");
  await expect(rotateForm.getByLabel("Session Token（可选）")).toHaveValue("");
  expect(JSON.stringify(control.providerItems)).not.toMatch(/(?:create|rotate)-(?:access|secret|session)-marker/);

  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(storage).not.toMatch(/(?:create|rotate)-(?:access|secret|session)-marker/);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "bedrock-provider-desktop-connected.png"),
    fullPage: true,
  });

  await page.reload();
  await openProviderSettings(page);
  await expect(page.getByText("AWS Bedrock", { exact: true })).toBeVisible();
  const reloadedDom = await page.locator("body").innerText();
  expect(reloadedDom).not.toMatch(/(?:create|rotate)-(?:access|secret|session)-marker/);
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
  await createForm.getByLabel("Access Key ID").fill("retry-access-marker");
  await createForm.getByLabel("Secret Access Key").fill("retry-secret-marker");
  await createForm.getByRole("button", { name: "保存并测试连接" }).click();

  await expect(page.getByRole("alert")).toContainText("已被其他管理员更新");
  await expect(page.getByRole("alert")).not.toContainText("do not expose");
  await expect(createForm.getByLabel("Access Key ID")).toHaveValue("retry-access-marker");
  await expect(createForm.getByLabel("Secret Access Key")).toHaveValue("retry-secret-marker");
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.getByRole("alert").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: path.join(outputDir, "bedrock-provider-mobile-conflict.png"),
    fullPage: false,
    mask: [
      createForm.getByLabel("Access Key ID"),
      createForm.getByLabel("Secret Access Key"),
      createForm.getByLabel("Session Token（可选）"),
    ],
    maskColor: "#ffffff",
  });
});

test("Bedrock save copy remains neutral until the refreshed record is connected", async ({ page }) => {
  await installFinOpsMockApi(page, [], { bedrockConnectionState: "degraded" });
  await openProviderSettings(page);
  const createForm = page.locator(".provider-connections > .bedrock-connection-form");
  await createForm.getByLabel("Access Key ID").fill("neutral-access-marker");
  await createForm.getByLabel("Secret Access Key").fill("neutral-secret-marker");
  await createForm.getByRole("button", { name: "保存并测试连接" }).click();

  await expect(page.getByRole("status")).toContainText("测试结果已刷新");
  await expect(page.getByRole("status")).not.toContainText("配置测试可用");
});

test("missing DeepSeek credential is re-entered without exposing secret material", async ({ page }) => {
  const keyMarker = "deepseek-private-key-marker";
  await installFinOpsMockApi(page, [], {
    providerItems: [{
      provider_id: "provider_deepseek",
      provider_type: "deepseek",
      display_name: "DeepSeek 原厂",
      base_url: "https://api.deepseek.com",
      connection_state: "invalid",
      governance_state: "pending",
      secret_status: "missing",
      connection_stage: "secret_read",
      stage_durations_ms: { secret_read: 3 },
      safe_error_category: "provider_secret_missing",
      revision: 1,
      available_models: [],
    }],
  });
  await openProviderSettings(page);

  await expect(page.getByText("需要重新录入 Key", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "检测连接" })).toBeDisabled();
  await page.getByLabel("重新录入 Key").fill(keyMarker);
  await page.getByRole("button", { name: "更换凭据" }).click();

  await expect(page.getByText("已安全保存", { exact: true })).toBeVisible();
  await expect(page.getByText("全部检测完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "检测连接" })).toBeEnabled();
  expect(await page.locator("body").innerText()).not.toContain(keyMarker);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(keyMarker);
});

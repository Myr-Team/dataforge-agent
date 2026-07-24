import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


test("embedded agents remain evidence-bound and create drafts only", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "运营驾驶舱" }).first().click();
  await page.getByRole("button", { name: "成本与预算" }).click();

  const finopsCard = page.locator(".finops-agent-card").filter({ hasText: "FinOps Agent" });
  await expect(finopsCard.getByText("成本变化来自主分析流程")).toBeVisible();
  await expect(finopsCard.getByText(/分析于/)).toBeVisible();
  await expect(page.locator(".finops-live")).toContainText("更新");

  await finopsCard.getByRole("button", { name: /Commerce 工作区贡献/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");

  await finopsCard.getByRole("button", { name: "重新分析" }).click();
  await expect(finopsCard.getByText(/分析已提交|已有分析结果/)).toBeVisible();
  await finopsCard.getByRole("button", { name: /创建治理草案/ }).click();
  await expect(finopsCard.getByText("治理草案已创建，尚未提交审批")).toBeVisible();

  expect(calls.filter((item) => item.path === "/api/finops/insights/analyze" && item.method === "POST")).toHaveLength(1);
  expect(calls.filter((item) => item.path === "/api/finops/actions" && item.method === "POST")).toHaveLength(1);
  expect(calls.some((item) => /\/approve$|\/execute$|\/submit$/.test(item.path))).toBe(false);

  await page.getByRole("button", { name: "风险与优化" }).click();
  await expect(page.getByText("FinOps Agent").last()).toBeVisible();
  await expect(page.getByText("ROI Agent").last()).toBeVisible();
  await expect(page.getByText("已验证结果事件不足")).toBeVisible();

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "finops-insight-agents.png"),
    fullPage: true,
  });
});

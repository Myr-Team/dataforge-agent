import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openOperations(page) {
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await expect(page.getByRole("heading", { name: "成本管理" })).toBeVisible();
}


async function setDocumentHidden(page, hidden) {
  await page.evaluate((nextHidden) => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => nextHidden,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
}


test("decision navigation stays cache first and refreshes only the visible tab at ten minutes", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-31T08:00:00Z") });
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  await expect(page.getByRole("heading", { name: "风险矩阵" })).toBeVisible();
  await openOperations(page);
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();

  expect(control.calls.roiDecision).toBe(1);
  expect(control.calls.riskDecision).toBe(1);

  await page.clock.runFor(599_999);
  expect(control.calls.roiDecision).toBe(1);
  expect(control.calls.riskDecision).toBe(1);
  await page.clock.runFor(1);
  await setDocumentHidden(page, false);
  await expect.poll(() => control.calls.roiDecision).toBe(2);
  expect(control.calls.riskDecision).toBe(1);

  await setDocumentHidden(page, true);
  await page.clock.runFor(600_000);
  expect(control.calls.roiDecision).toBe(2);
  expect(control.calls.riskDecision).toBe(1);
});


test("failed ROI revalidation keeps the prior decision and manual refresh stays tab scoped", async ({ page }) => {
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByText("$3,000.00").first()).toBeVisible();

  const riskCalls = control.calls.riskDecision;
  const roiCalls = control.calls.roiDecision;
  control.failRoiRefresh = true;
  await page.locator(".finops-live").getByRole("button", { name: "刷新运营数据" }).click();

  await expect.poll(() => control.calls.roiDecision).toBe(roiCalls + 1);
  await expect(page.getByText("$3,000.00").first()).toBeVisible();
  await expect(page.getByText("更新失败，当前继续展示最近一次成功结果。")).toBeVisible();
  expect(control.calls.riskDecision).toBe(riskCalls);
});


test("seeded ROI cache is interactive within 300ms without a full-page skeleton", async ({ page }) => {
  const control = await installFinOpsMockApi(page, [], { decisionDelayMs: 350 });
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByRole("button", { name: "调整测算参数" })).toBeVisible();
  const coldLatencyMs = control.timings.roiDecision.at(-1);

  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  await expect(page.getByRole("heading", { name: "风险矩阵" })).toBeVisible();
  const roiCallsBeforeCachedNavigation = control.calls.roiDecision;
  const interactiveMs = await page.evaluate(async () => {
    const visibleButton = (label) => [...document.querySelectorAll("button")]
      .filter((node) => node.textContent?.trim() === label)
      .find((node) => {
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      });
    const waitForVisibleButton = (label) => new Promise((resolve, reject) => {
      const initial = visibleButton(label);
      if (initial) {
        resolve(initial);
        return;
      }
      let timeoutId;
      const observer = new MutationObserver(() => {
        const match = visibleButton(label);
        if (!match) return;
        observer.disconnect();
        clearTimeout(timeoutId);
        resolve(match);
      });
      observer.observe(document.body, { childList: true, subtree: true, attributes: true });
      timeoutId = setTimeout(() => {
        observer.disconnect();
        reject(new Error(`button did not become visible: ${label}`));
      }, 1_000);
    });

    const started = performance.now();
    visibleButton("成本管理")?.click();
    const roiTab = await waitForVisibleButton("效能与 ROI");
    roiTab.click();
    await waitForVisibleButton("调整测算参数");
    return performance.now() - started;
  });
  await expect(page.getByRole("button", { name: "调整测算参数" })).toBeVisible();

  expect(coldLatencyMs).toBeGreaterThanOrEqual(300);
  expect(interactiveMs).toBeLessThan(300);
  expect(control.calls.roiDecision).toBe(roiCallsBeforeCachedNavigation);
  await expect(page.locator(".finops-decision-roi-loading")).toHaveCount(0);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-roi-cache-first-desktop.png"),
    fullPage: true,
  });
});

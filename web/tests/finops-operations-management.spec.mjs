import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsDemoCompletenessApi, installFinOpsMockApi } from "./finopsMockApi.mjs";


const DEMO_FORBIDDEN_EMPTY = /未接入|暂不可用|Failed to fetch|待接入|未记录|当前范围没有可展示的记录/;


async function expectDistinctGeometry(locator, dimension = "height") {
  const values = await locator.evaluateAll((nodes, measuredDimension) => nodes
    .map((node) => Math.round(node.getBoundingClientRect()[measuredDimension]))
    .filter((value) => value > 0), dimension);
  expect(values.length).toBeGreaterThan(1);
  expect(new Set(values).size).toBeGreaterThan(1);
}


async function expectDemoSurfaceComplete(page) {
  const content = page.locator(".finops-content");
  const body = await content.innerText();
  expect(body).not.toMatch(DEMO_FORBIDDEN_EMPTY);
  await expect(content.locator(".finops-empty, .finops-decision-empty, .finops-decision-page-empty")).toHaveCount(0);
}


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

  const tokenMetric = page.getByRole("article", { name: /^Token / });
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
  await page.getByRole("button", { name: "清除全部" }).click();
  await expect(page.getByRole("button", { name: /移除部门筛选 Commerce/ })).toHaveCount(0);

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
  await page.screenshot({
    path: path.join(outputDir, "operations-cost-analysis-desktop.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "效能与 ROI" }).click();
  await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();
  await expect(page.getByText("测算显示具备投入价值，业务结果仍需验证")).toBeVisible();
  await page.screenshot({
    path: path.join(outputDir, "operations-roi-desktop.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "风险与优化" }).click();
  await expect(page.getByRole("heading", { name: "优化组合" })).toBeVisible();
  await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);
  await expect(page.getByText("最新 AI 解读 · 已保存证据")).toBeVisible();
  await expect(page.getByText("优先处理慢响应并验证缓存策略")).toBeVisible();

  await page.screenshot({
    path: path.join(outputDir, "operations-management-desktop.png"),
    fullPage: true,
  });
});


for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "1366", width: 1366, height: 900 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`ROI and risk decision BI stays readable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const control = await installFinOpsMockApi(page);
    await page.goto("/");
    await page.getByRole("button", { name: "运营管理" }).last().click();

    const headerBefore = await page.locator(".finops-head").boundingBox();
    await page.getByRole("button", { name: "效能与 ROI" }).click();
    await expect(page.getByText("测算显示具备投入价值，业务结果仍需验证")).toBeVisible();
    await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "证据成熟度" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "平台自动确认与业务侧补充验证" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "已验证 ROI" })).toContainText("结果待验证");
    await expect(page.locator(".finops-decision-roi-metric")).toHaveCount(4);

    const bridgeWidths = await page.locator(".finops-decision-value-bar").evaluateAll((bars) => (
      bars.map((bar) => Math.round(bar.getBoundingClientRect().width)).filter((value) => value > 0)
    ));
    expect(new Set(bridgeWidths).size).toBeGreaterThan(1);
    const help = page.getByRole("button", { name: "月度收益说明" });
    const tooltipId = await help.getAttribute("aria-describedby");
    const helpTooltip = page.locator(`#${tooltipId}`);
    await help.hover();
    await expect(helpTooltip).toBeVisible();
    await expect(helpTooltip).not.toBeEmpty();
    await help.focus();
    await expect(helpTooltip).toBeVisible();
    await expect(page.getByText("$0.00039")).toBeVisible();
    await expect(page.getByText("$0.00046")).toBeVisible();
    await expect(page.getByText("$0.00048")).toBeVisible();
    await expect(page.locator(".finops-decision-roi-wide")).not.toContainText("缺少样本或价格");

    control.delayNextRoiRefreshMs = 250;
    await page.locator(".finops-live").getByRole("button", { name: "刷新" }).click();
    await expect(page.locator(".finops-live")).toContainText("正在更新");
    const headerUpdating = await page.locator(".finops-head").boundingBox();
    expect(Math.round(headerUpdating.height)).toBe(Math.round(headerBefore.height));
    await expect(page.locator(".finops-live")).not.toContainText("正在更新");

    const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
    await mkdir(outputDir, { recursive: true });
    await page.screenshot({
      path: path.join(outputDir, `operations-roi-${viewport.name}.png`),
      fullPage: true,
    });

    await page.getByRole("button", { name: "风险与优化" }).click();
    await expect(page.getByRole("heading", { name: "风险矩阵" })).toBeVisible();
    await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);
    const pointSizes = await page.locator(".finops-decision-matrix-point > button").evaluateAll((points) => (
      points.map((point) => Math.round(point.getBoundingClientRect().width))
    ));
    expect(new Set(pointSizes).size).toBeGreaterThan(1);
    await expect(page.locator(".finops-live > i")).toHaveCount(0);

    const contentText = await page.locator(".finops-content").innerText();
    expect(contentText).not.toMatch(/未接入|暂不可用|Failed to fetch/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({
      path: path.join(outputDir, `operations-risk-${viewport.name}.png`),
      fullPage: true,
    });
  });
}


test("demo completeness fixture fills every visible metric card chart table and queue across all four views", async ({ page }) => {
  const control = await installFinOpsDemoCompletenessApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).first().click();

  await page.getByRole("button", { name: "运营总览" }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-metric")).toHaveCount(8);
  const overviewMetricValues = await page.locator(".finops-metric > strong").allTextContents();
  expect(new Set(overviewMetricValues).size).toBeGreaterThan(5);
  await expect(page.locator(".finops-trend-column")).toHaveCount(3);
  await expectDistinctGeometry(page.locator(".finops-trend-stack"), "height");
  await expect(page.locator(".finops-trust-grid article")).toHaveCount(3);
  expect(new Set(await page.locator(".finops-trust-grid article > strong").allTextContents()).size).toBe(3);
  await expect(page.locator(".finops-insights > div")).toHaveCount(4);
  await expect(page.locator(".finops-table tbody tr")).toHaveCount(2);
  for (const panel of await page.locator(".finops-panel").all()) {
    await expect(panel.locator(".finops-panel-body")).not.toBeEmpty();
  }

  await page.getByRole("button", { name: "成本分析" }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-metric")).toHaveCount(8);
  await expect(page.locator(".finops-trend-column")).toHaveCount(3);
  await expectDistinctGeometry(page.locator(".finops-trend-stack"), "height");
  await expect(page.locator(".finops-table")).toHaveCount(2);
  for (const table of await page.locator(".finops-table").all()) {
    const rows = table.locator("tbody tr");
    expect(await rows.count()).toBeGreaterThanOrEqual(2);
    const dimensions = await rows.locator("td:first-child").allTextContents();
    expect(new Set(dimensions).size).toBe(dimensions.length);
  }
  const workspacePanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "专案成本归因" }) });
  await expect(workspacePanel.locator("tbody tr")).toHaveCount(3);
  const agentPanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "Agent 成本归因" }) });
  const modelPanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "模型成本归因" }) });
  await expect(agentPanel.locator(".finops-bar-row")).toHaveCount(3);
  await expect(modelPanel.locator(".finops-bar-row")).toHaveCount(3);
  await expectDistinctGeometry(agentPanel.locator(".finops-bar-row i"), "width");
  await expectDistinctGeometry(modelPanel.locator(".finops-bar-row i"), "width");
  const doughnuts = page.getByRole("img", { name: /成本结构/ });
  await expect(doughnuts).toHaveCount(2);
  for (const doughnut of await doughnuts.all()) await expect(doughnut).toContainText("3");

  await page.getByRole("button", { name: "效能与 ROI" }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-decision-roi-metric")).toHaveCount(4);
  const roiValues = await page.locator(".finops-decision-roi-metric > strong").allTextContents();
  expect(new Set(roiValues).size).toBe(4);
  await expect(page.locator(".finops-decision-value-row")).toHaveCount(3);
  await expectDistinctGeometry(page.locator(".finops-decision-value-bar"), "width");
  await expect(page.locator(".finops-decision-maturity-stages li")).toHaveCount(4);
  await expect(page.locator(".finops-decision-roi-trend tbody tr")).toHaveCount(3);
  expect(new Set(await page.locator(".finops-decision-roi-trend tbody td:nth-child(3)").allTextContents()).size).toBe(3);
  await expect(page.locator(".finops-decision-roi-capability")).toBeVisible();

  await page.getByRole("button", { name: "风险与优化" }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-decision-risk-domains article")).toHaveCount(4);
  await expect(page.locator(".finops-decision-matrix-point > button")).toHaveCount(4);
  await expectDistinctGeometry(page.locator(".finops-decision-matrix-point > button"), "width");
  await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);
  await expect(page.locator(".finops-decision-portfolio-point > button")).toHaveCount(4);
  await expectDistinctGeometry(page.locator(".finops-decision-portfolio-point > button"), "width");
  await expect(page.getByRole("list", { name: "优化机会优先列表" }).getByRole("button")).toHaveCount(4);
  await expect(page.locator(".finops-decision-risk-chain-stages li")).toHaveCount(5);
  await expect(page.locator(".finops-decision-risk-evidence-card")).toHaveCount(1);
  await expect(page.locator(".finops-decision-risk-insight")).not.toBeEmpty();
  await expect(page.locator(".finops-decision-risk-governance")).not.toBeEmpty();

  expect(control.calls.bootstrap).toBeGreaterThan(0);
  expect(control.calls.roiDecision).toBe(1);
  expect(control.calls.riskDecision).toBe(1);
});


test("cost attribution tables fit desktop cards and remain horizontally operable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).last().click();
  await page.getByRole("button", { name: "成本分析" }).click();

  for (const title of ["部门成本归因", "专案成本归因"]) {
    const panel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: title }) });
    const scroll = panel.locator(".finops-table-scroll");
    const desktop = await scroll.evaluate((node) => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth }));
    expect(desktop.scrollWidth).toBeLessThanOrEqual(desktop.clientWidth + 1);
    const lastHeader = panel.getByRole("columnheader", { name: "缓存命中率" });
    await expect(lastHeader).toBeVisible();
    const panelBox = await panel.boundingBox();
    const headerBox = await lastHeader.boundingBox();
    expect(headerBox.x + headerBox.width).toBeLessThanOrEqual(panelBox.x + panelBox.width);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  const projectScroll = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "专案成本归因" }) }).locator(".finops-table-scroll");
  await expect(projectScroll).toHaveAttribute("tabindex", "0");
  const mobile = await projectScroll.evaluate((node) => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth }));
  expect(mobile.scrollWidth).toBeGreaterThan(mobile.clientWidth);
  const scrolled = await projectScroll.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
    return node.scrollLeft;
  });
  expect(scrolled).toBeGreaterThan(0);
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

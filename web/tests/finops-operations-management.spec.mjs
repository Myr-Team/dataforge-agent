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


async function expectNoOverlap(locator) {
  const boxes = (await locator.evaluateAll((nodes) => nodes.map((node) => {
    const box = node.getBoundingClientRect();
    return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
  })));
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      const overlapWidth = Math.min(boxes[left].right, boxes[right].right) - Math.max(boxes[left].left, boxes[right].left);
      const overlapHeight = Math.min(boxes[left].bottom, boxes[right].bottom) - Math.max(boxes[left].top, boxes[right].top);
      expect(overlapWidth > 0 && overlapHeight > 0).toBe(false);
    }
  }
}


async function expectDemoSurfaceComplete(page) {
  const content = page.locator(".finops-content");
  const body = await content.innerText();
  expect(body).not.toMatch(DEMO_FORBIDDEN_EMPTY);
  await expect(content.locator(".finops-empty, .finops-decision-empty, .finops-decision-page-empty")).toHaveCount(0);
}


test("stalled capability check becomes one retryable operations state", async ({ page }) => {
  await installFinOpsMockApi(page, [], { capabilityDelayMs: 9_000 });
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();

  await expect(page.getByText("正在核验运营管理权限")).toBeVisible();
  const retryState = page.getByRole("status", { name: "运营管理权限服务状态" });
  await expect(retryState).toContainText("权限服务暂时不可用", { timeout: 12_000 });
  await expect(retryState.getByRole("button", { name: "重新检查" })).toHaveCount(1);
  await expect(page.getByText("当前账户无权访问运营管理")).toHaveCount(0);
  await expect(page.getByText("正在核验运营管理权限")).toHaveCount(0);
});


test("operations management is immediately discoverable and supports metric drilldown", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });

  await expect(page.getByRole("button", { name: "成本管理" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "运行记录" }).first()).toBeVisible();
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await expect(page.getByRole("heading", { name: "成本管理" })).toBeVisible();
  await expect(page.locator(".finops-live")).toContainText("更新");

  const cacheMetric = page.getByRole("article", { name: /^缓存收益 / });
  await cacheMetric.focus();
  await expect(page.locator(".finops-metric-tooltip-content")).toHaveCount(0);
  const cacheHelp = cacheMetric.locator(".finops-help-trigger");
  await expect(cacheHelp).toBeVisible();
  await cacheHelp.focus();
  await expect(page.locator(".finops-metric-tooltip-content")).toBeVisible();
  await expect(page.locator(".finops-metric-tooltip-content")).toContainText("缓存命中");

  await expect(page.locator(".finops-trend-event")).toHaveCount(0);
  await page.locator(".finops-trend-switch").getByRole("button", { name: "Token" }).click();
  const latestTrend = page.locator(".finops-trend-column").filter({ hasText: "752 Token" });
  await expect(latestTrend).toHaveCount(1);
  await latestTrend.hover();
  const trendTooltip = page.locator(".finops-trend-tooltip-content");
  await expect(trendTooltip).toContainText("1");
  await expect(page.locator(".finops-viewport-tooltip")).toHaveCount(1);
  const tooltipBox = await trendTooltip.boundingBox();
  const viewport = page.viewportSize();
  expect(tooltipBox.x).toBeGreaterThanOrEqual(0);
  expect(tooltipBox.x + tooltipBox.width).toBeLessThanOrEqual(viewport.width);
  expect(tooltipBox.y).toBeGreaterThanOrEqual(0);
  expect(tooltipBox.y + tooltipBox.height).toBeLessThanOrEqual(viewport.height);
  await page.screenshot({
    path: path.join(outputDir, "operations-trend-tooltip-desktop.png"),
    fullPage: false,
  });

  await page.getByLabel("部门筛选", { exact: true }).selectOption("Commerce");
  await expect(page.getByRole("button", { name: /移除部门筛选 Commerce/ })).toBeVisible();
  await page.getByRole("button", { name: "清除全部" }).click();
  await expect(page.getByRole("button", { name: /移除部门筛选 Commerce/ })).toHaveCount(0);

  await expect(page.getByRole("region", { name: "运营决策概览" }).locator(".finops-metric")).toHaveCount(4);
  await expect(page.getByRole("img", { name: "部门估算成本占比" })).toBeVisible();
  await expect(page.locator(".finops-executive-attention-item")).toHaveCount(3);
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
  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expect(page.getByText("成本趋势")).toBeVisible();
  await expect(page.locator(".finops-cost-summary")).toBeVisible();
  await expect(page.locator(".finops-content > .finops-metrics")).toHaveCount(0);
  await expect(page.getByText("Agent 成本结构")).toHaveCount(0);
  await expect(page.getByText("模型成本结构")).toHaveCount(0);
  await page.screenshot({
    path: path.join(outputDir, "operations-cost-analysis-desktop.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();
  await expect(page.getByText("AI 运营总投入").first()).toBeVisible();
  await expect(page.getByText("测算显示具备投入价值，业务结果仍需验证")).toBeVisible();
  await page.screenshot({
    path: path.join(outputDir, "operations-roi-desktop.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  const ruleDisclosure = page.locator(".finops-risk-scan-disclosure");
  await expect(ruleDisclosure).not.toHaveAttribute("open", "");
  await expect(page.getByText("判定规则", { exact: true })).toBeVisible();
  await expect(page.locator(".finops-risk-scan-rules")).toBeHidden();
  await expect(page.locator(".finops-risk-scan-summary")).toContainText("146");
  await expect(page.getByRole("heading", { name: "风险矩阵" })).toBeVisible();
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
    await page.getByRole("button", { name: "成本管理" }).last().click();

    const headerBefore = await page.locator(".finops-head").boundingBox();
    await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
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

    await page.getByRole("button", { name: "风险与优化", exact: true }).click();
    await expect(page.getByText("判定规则", { exact: true })).toBeVisible();
    await expect(page.locator(".finops-risk-scan-rules")).toBeHidden();
    await expect(page.getByRole("heading", { name: "风险矩阵" })).toBeVisible();
    await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);
    const riskRows = page.locator(".finops-decision-risk-row");
    await expect(riskRows).toHaveCount(4);
    await expectNoOverlap(riskRows);
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


test("risk scan reruns safely and binds evidence plus AI to the selected rule", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();

  await page.locator(".finops-risk-scan-disclosure > summary").click();
  const rules = page.locator(".finops-risk-scan-rules");
  await expect(rules.locator("li")).toHaveCount(7);
  await page.getByRole("button", { name: "重新扫描" }).click();
  await expect(page.getByRole("button", { name: "重新扫描" })).toBeEnabled();
  expect(calls.filter((item) => item.method === "POST" && item.path === "/api/finops/risk/scans")).toHaveLength(1);

  const errorRule = rules.locator("li").filter({ hasText: "调用失败率" });
  await errorRule.getByRole("button", { name: "查看证据" }).click();
  const evidence = page.getByRole("dialog");
  await expect(evidence.getByText("提取高价值客户机会并生成摘要")).toBeVisible();
  await expect(evidence.getByText("失败", { exact: true }).first()).toBeVisible();
  await page.keyboard.press("Escape");

  await errorRule.getByRole("button", { name: "问 AI" }).click();
  const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(assistant.getByText("调用失败率").first()).toBeVisible();
  await expect(assistant.getByText("结论", { exact: true })).toBeVisible();
  const assistantCall = calls.find((item) => item.path === "/api/finops/assistant/query");
  expect(JSON.parse(assistantCall.body).question).toContain("调用失败率");
});


for (const viewport of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "1366", width: 1366, height: 768 },
  { name: "intermediate", width: 820, height: 1180 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`executive overview stays proportional and unclipped at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await installFinOpsDemoCompletenessApi(page);
    await page.goto("/");
    await page.getByRole("button", { name: "成本管理" }).last().click();

    const overview = page.getByRole("region", { name: "运营决策概览" });
    await expect(overview.locator(".finops-metric")).toHaveCount(4);
    await expect(overview.locator(".finops-executive-attention-item")).toHaveCount(3);
    await expectNoOverlap(overview.locator(".finops-executive-decision-grid > .finops-panel"));

    const slices = overview.locator(".finops-executive-donut .segment");
    await expect(slices).toHaveCount(4);
    const dashArrays = await slices.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("stroke-dasharray")));
    expect(new Set(dashArrays).size).toBeGreaterThan(1);
    await slices.last().focus();
    await expect(slices.last()).toBeFocused();
    const tooltipId = await slices.last().getAttribute("aria-describedby");
    const tooltip = page.locator(`#${tooltipId}`);
    await expect(tooltip).toBeVisible();
    await expect(page.locator(".finops-viewport-tooltip")).toHaveCount(1);
    await expect(tooltip).toContainText("估算成本");
    const bounds = await tooltip.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
    await page.locator(".finops-head h1").click();
    await expect(tooltip).toHaveCount(0);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
    await mkdir(outputDir, { recursive: true });
    await page.screenshot({
      path: path.join(outputDir, `operations-executive-overview-${viewport.name}.png`),
      fullPage: true,
    });
  });
}


test("demo completeness fixture fills every visible metric card chart table and queue across all four views", async ({ page }) => {
  const control = await installFinOpsDemoCompletenessApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();

  await page.getByRole("button", { name: "运营总览", exact: true }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-metric")).toHaveCount(4);
  const overviewMetricValues = await page.locator(".finops-metric > strong").allTextContents();
  expect(new Set(overviewMetricValues).size).toBe(4);
  await expect(page.locator(".finops-trend-column")).toHaveCount(3);
  await expectDistinctGeometry(page.locator(".finops-trend-stack"), "height");
  await expect(page.locator(".finops-executive-donut .segment")).toHaveCount(4);
  const donutShares = await page.locator(".finops-executive-donut .segment").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("stroke-dasharray")));
  expect(new Set(donutShares).size).toBeGreaterThan(1);
  await expect(page.locator(".finops-executive-attention-item")).toHaveCount(3);
  await expect(page.locator(".finops-executive-drilldowns > button")).toHaveCount(3);
  for (const panel of await page.locator(".finops-panel").all()) {
    await expect(panel.locator(".finops-panel-body")).not.toBeEmpty();
  }

  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-metric")).toHaveCount(0);
  await expect(page.locator(".finops-cost-summary")).toContainText("计价覆盖");
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
  await expect(doughnuts).toHaveCount(0);

  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
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

  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-decision-risk-domains article")).toHaveCount(4);
  await expect(page.locator(".finops-decision-risk-row")).toHaveCount(4);
  await expectNoOverlap(page.locator(".finops-decision-risk-row"));
  await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);
  await expect(page.locator(".finops-decision-opportunity-track > i")).toHaveCount(4);
  await expectDistinctGeometry(page.locator(".finops-decision-opportunity-track > i"), "width");
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
  await page.getByRole("button", { name: "成本管理" }).last().click();
  await page.getByRole("button", { name: "成本分析", exact: true }).click();

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
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).last().click();

  await expect.poll(() => calls.some((call) => call.path === "/api/finops/assistant/conversations")).toBe(true);

  await page.getByRole("button", { name: "运营 AI" }).click();
  const dialog = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(dialog).toBeVisible();
  const bounds = await dialog.boundingBox();
  expect(bounds.width).toBeLessThanOrEqual(362);
  expect(bounds.height).toBeLessThan(790);
  await expect(dialog).toContainText("上次分析已保留，可继续针对当前指标提问。");

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

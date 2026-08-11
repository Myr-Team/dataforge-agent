import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { bootstrapPayload, installFinOpsDemoCompletenessApi, installFinOpsMockApi } from "./finopsMockApi.mjs";


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


function bottomEdge(box) {
  return box.y + box.height;
}


test("trend bars share the zero baseline and retain it through hover and focus", async ({ page }) => {
  const shortTrend = {
    ...bootstrapPayload.trend,
    items: bootstrapPayload.trend.items.slice(0, 7),
  };
  await installFinOpsMockApi(page, [], { trendPayload: shortTrend });
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.locator(".finops-trend-switch").getByRole("button", { name: "Token" }).click();

  const chart = page.locator(".finops-trend-chart");
  const viewport = chart.locator(".finops-trend-viewport");
  const baseline = chart.locator(".finops-trend-gridlines i").last();
  const column = chart.locator(".finops-trend-column").first();
  const stack = column.locator(".finops-trend-stack.has-value");
  await expect(stack).toBeVisible();

  const [initialBox, baselineBox] = await Promise.all([stack.boundingBox(), baseline.boundingBox()]);
  expect(initialBox).not.toBeNull();
  expect(baselineBox).not.toBeNull();
  expect(Math.abs(bottomEdge(initialBox) - baselineBox.y)).toBeLessThanOrEqual(1);
  const shortOverflow = await viewport.evaluate((node) => node.scrollWidth - node.clientWidth);
  expect(shortOverflow).toBeLessThanOrEqual(1);

  await column.hover();
  const [hoverBox, hoverBaselineBox] = await Promise.all([stack.boundingBox(), baseline.boundingBox()]);
  expect(hoverBox).not.toBeNull();
  expect(hoverBaselineBox).not.toBeNull();
  expect(Math.abs(bottomEdge(hoverBox) - hoverBaselineBox.y)).toBeLessThanOrEqual(1);

  await column.focus();
  const [focusBox, focusBaselineBox] = await Promise.all([stack.boundingBox(), baseline.boundingBox()]);
  expect(focusBox).not.toBeNull();
  expect(focusBaselineBox).not.toBeNull();
  expect(Math.abs(bottomEdge(focusBox) - focusBaselineBox.y)).toBeLessThanOrEqual(1);
  const tooltip = page.locator(".finops-trend-tooltip-content");
  await expect(tooltip).toContainText("2026-08-01");
  await expect(tooltip).toContainText("缓存命中");
  await expect(tooltip).toContainText("缓存未命中");
  await expect(tooltip).toContainText("绕过缓存");
  await expect(tooltip).toContainText("避免 Token");
  await expect(tooltip).toContainText("估算节省");
});


test("trend viewport scrolls only when a narrow viewport receives a long range", async ({ page }) => {
  const longTrend = {
    ...bootstrapPayload.trend,
    items: Array.from({ length: 20 }, (_, index) => {
      const source = bootstrapPayload.trend.items[index % bootstrapPayload.trend.items.length];
      return {
        ...source,
        bucket: `2026-07-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
      };
    }),
  };
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page, [], { trendPayload: longTrend });
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  const viewport = page.locator(".finops-trend-chart .finops-trend-viewport");
  await expect(viewport).toBeVisible();
  const longOverflow = await viewport.evaluate((node) => node.scrollWidth - node.clientWidth);
  expect(longOverflow).toBeGreaterThan(1);
});


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


test("dashboard pending renders a skeleton then the real workspace", async ({ page }) => {
  await installFinOpsMockApi(page, [], { dashboardDelayMs: 1_200 });
  await page.goto("/");

  await expect(page.locator(".dashboard-stage-loading")).toBeVisible({ timeout: 4_000 });
  await expect(page.getByRole("heading", { name: "Commerce" })).toBeVisible({ timeout: 6_000 });
  await expect(page.locator(".dashboard-stage-loading")).toHaveCount(0);
});


test("dashboard failure becomes a retryable content state", async ({ page }) => {
  const control = await installFinOpsMockApi(page, [], { dashboardUnavailable: true });
  await page.goto("/");

  const state = page.getByRole("alert", { name: "工作区加载失败" });
  await expect(state).toBeVisible({ timeout: 6_000 });
  await expect(state).toContainText("工作区数据未完整加载");
  control.dashboardUnavailable = false;
  control.failDashboardFallback = false;
  await state.getByRole("button", { name: "重新加载" }).click();
  await expect(page.getByRole("heading", { name: "Commerce" })).toBeVisible({ timeout: 6_000 });
  expect(control.calls.dashboard).toBeGreaterThanOrEqual(2);
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
  const latestTrend = page.locator(".finops-trend-column").last();
  await expect(latestTrend).toBeVisible();
  await latestTrend.hover();
  const trendTooltip = page.locator(".finops-trend-tooltip-content");
  await expect(trendTooltip).toContainText("2,051,580");
  await expect(trendTooltip).toContainText("Token");
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

  await page.getByLabel("部门筛选", { exact: true }).selectOption("Operations");
  await expect(page.getByRole("button", { name: /移除部门筛选 Operations/ })).toBeVisible();
  await page.getByRole("button", { name: "清除全部" }).click();
  await expect(page.getByRole("button", { name: /移除部门筛选 Operations/ })).toHaveCount(0);

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
  await expect(page.locator(".finops-risk-scan-history").getByRole("button")).toHaveCount(2);
  const olderScan = page.locator(".finops-risk-scan-history").getByRole("button").nth(1);
  await olderScan.click();
  await expect(olderScan).toHaveAttribute("aria-pressed", "true");
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
    await expect(page.getByRole("heading", { name: "运营自动化测算" })).toBeVisible();
    await expect(page.locator(".finops-decision-roi-case-assumptions dl")).toHaveCount(7);
    await expect(page.getByText("业务结果验证前不计为已实现 ROI")).toBeVisible();
    await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "证据成熟度" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "平台自动确认与业务侧补充验证" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "已验证 ROI" })).toContainText("结果待验证");
    await expect(page.locator(".finops-decision-roi-metric")).toHaveCount(4);

    const valueFormula = page.locator(".finops-decision-value-formula");
    await expect(valueFormula.locator(".finops-decision-value-term")).toHaveCount(3);
    await expect(valueFormula.locator(".finops-decision-value-operator")).toHaveCount(2);
    await expect(valueFormula).toHaveAccessibleName("月度收益减去 AI 运营总投入等于月度净收益");
    await expect(valueFormula).toContainText("月度收益");
    await expect(valueFormula).toContainText("AI 运营总投入");
    await expect(valueFormula).toContainText("月度净收益");
    await expect(valueFormula).toContainText("$3,000.00");
    await expect(valueFormula).toContainText("$1,150.00");
    await expect(valueFormula).toContainText("$1,850.00");
    await expect(page.locator(".finops-decision-value-result-strip")).toContainText("160.9%");
    await expect(page.locator(".finops-decision-value-result-strip")).toContainText("预计回收周期");
    await expect(page.locator(".finops-decision-value-track")).toHaveCount(0);
    const help = page.getByRole("button", { name: "月度收益说明" });
    const tooltipId = await help.getAttribute("aria-describedby");
    const helpTooltip = page.locator(`#${tooltipId}`);
    await help.hover();
    await expect(helpTooltip).toBeVisible();
    await expect(helpTooltip).not.toBeEmpty();
    await help.focus();
    await expect(helpTooltip).toBeVisible();
    await expect(page.getByText("$0.1706")).toBeVisible();
    await expect(page.getByText("$0.2143")).toBeVisible();
    await expect(page.getByText("$0.1735")).toBeVisible();
    await expect(page.locator(".finops-decision-roi-analysis-grid")).not.toContainText("缺少样本或价格");

    control.delayNextRoiRefreshMs = 250;
    await page.locator(".finops-live").getByRole("button", { name: "刷新运营数据" }).click();
    await expect(page.locator(".finops-live")).toContainText("更新中");
    const headerUpdating = await page.locator(".finops-head").boundingBox();
    expect(Math.round(headerUpdating.height)).toBe(Math.round(headerBefore.height));
    await expect(page.locator(".finops-live")).not.toContainText("更新中");

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
    const recommendation = page.locator(".finops-decision-risk-recommendation p");
    await expect(recommendation).toBeVisible();
    const recommendationBox = await recommendation.evaluate((node) => ({
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      overflow: getComputedStyle(node).overflow,
      whiteSpace: getComputedStyle(node).whiteSpace,
    }));
    expect(recommendationBox.scrollHeight).toBeLessThanOrEqual(recommendationBox.clientHeight + 1);
    expect(recommendationBox.overflow).not.toBe("hidden");
    expect(recommendationBox.whiteSpace).not.toBe("nowrap");
    await expect(page.locator(".finops-live > i")).toHaveCount(0);

    const contentText = await page.locator(".finops-content").innerText();
    expect(contentText).not.toMatch(/未接入|暂不可用|Failed to fetch/);
    expect(contentText).toContain("评估样本量");
    expect(contentText).toContain("运营严重度");
    expect(contentText).not.toMatch(/真实影响范围|业务影响/);
    await expect(page.locator(".finops-decision-risk-evidence-card")).toContainText("响应时延 · 6,200 毫秒");
    await page.getByRole("list", { name: "风险优先事项" }).getByRole("button", { name: /缓存效率优化/ }).click();
    await expect(page.locator(".finops-decision-risk-evidence-card")).toContainText("缓存状态 · 缓存未命中");
    await page.getByRole("list", { name: "风险优先事项" }).getByRole("button", { name: /计价覆盖补齐/ }).click();
    await expect(page.locator(".finops-decision-risk-evidence-card")).toContainText("计价状态 · 未计价");
    await page.getByRole("list", { name: "风险优先事项" }).getByRole("button", { name: /调用成功率改善/ }).click();
    await expect(page.locator(".finops-decision-risk-evidence-card")).toContainText("调用状态 · 调用失败");
    await expect(page.locator(".finops-decision-risk-chain")).not.toContainText(/gateway_coverage|cache_state|failed|miss|unmanaged/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    const riskChain = page.locator(".finops-decision-risk-chain");
    await riskChain.scrollIntoViewIfNeeded();
    await riskChain.evaluate((node) => node.scrollIntoView({ block: "start", inline: "nearest" }));
    const riskChainBox = await riskChain.boundingBox();
    expect(riskChainBox).not.toBeNull();
    expect(riskChainBox.x).toBeGreaterThanOrEqual(0);
    expect(riskChainBox.x + riskChainBox.width).toBeLessThanOrEqual(viewport.width);
    expect(riskChainBox.y).toBeGreaterThanOrEqual(-1);
    expect(riskChainBox.y).toBeLessThan(viewport.height);
    if (viewport.name === "desktop" || viewport.name === "mobile") {
      await page.screenshot({
        path: path.join(outputDir, `finops-risk-stage-${viewport.name}.png`),
      });
    }
    await page.screenshot({
      path: path.join(outputDir, `operations-risk-${viewport.name}.png`),
      fullPage: true,
    });
  });
}


test("mobile AI launcher follows risk content without covering it", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "\u6210\u672c\u7ba1\u7406" }).last().click();
  await page.getByRole("button", { name: "\u98ce\u9669\u4e0e\u4f18\u5316", exact: true }).click();

  const pageSurface = page.locator(".finops-page");
  const launcher = page.locator(".finops-ai-launcher");
  const riskChain = page.locator(".finops-decision-risk-chain");
  await riskChain.scrollIntoViewIfNeeded();
  await expect(launcher).toBeVisible();
  await expect.poll(() => pageSurface.evaluate((node) => getComputedStyle(node).position)).toBe("relative");
  await expect.poll(() => launcher.evaluate((node) => getComputedStyle(node).position)).toBe("absolute");
  const [launcherBox, riskChainBox] = await Promise.all([launcher.boundingBox(), riskChain.boundingBox()]);
  expect(launcherBox).not.toBeNull();
  expect(riskChainBox).not.toBeNull();
  const overlapsRiskChain = launcherBox.left < riskChainBox.right
    && launcherBox.right > riskChainBox.left
    && launcherBox.top < riskChainBox.bottom
    && launcherBox.bottom > riskChainBox.top;
  expect(overlapsRiskChain).toBe(false);
  await launcher.scrollIntoViewIfNeeded();
  await expect(launcher).toBeInViewport();
});


test("risk scan reruns safely and binds evidence plus AI to the selected rule", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();

  await page.locator(".finops-risk-scan-disclosure > summary").click();
  const rules = page.locator(".finops-risk-scan-rules");
  await expect(rules.locator("li")).toHaveCount(7);
  const priorities = page.getByRole("list", { name: "风险优先事项" });
  await expect(priorities.getByRole("button")).toHaveCount(4);
  await priorities.getByRole("button", { name: /调用成功率改善/ }).click();
  await page.getByRole("button", { name: "重新扫描" }).click();
  await expect(page.getByRole("button", { name: "重新扫描" })).toBeEnabled();
  await expect(priorities.getByRole("button")).toHaveCount(6);
  await expect(priorities.getByRole("button", { name: /响应时延优化/ })).toHaveAttribute("aria-pressed", "true");
  expect(calls.filter((item) => item.method === "POST" && item.path === "/api/finops/risk/scans")).toHaveLength(1);
  const decisionCalls = calls.filter((item) => item.method === "GET" && item.path === "/api/finops/risk/decision");
  expect(decisionCalls.filter((item) => !item.search.includes("refresh=1")).length).toBeGreaterThanOrEqual(1);
  expect(decisionCalls.filter((item) => item.search.includes("refresh=1"))).toHaveLength(1);

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


test("operations AI keeps latency cache pricing and error evidence item-specific", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  await page.locator(".finops-risk-scan-disclosure > summary").click();
  const rules = page.locator(".finops-risk-scan-rules");
  const cases = [
    { label: "响应时延", policy: "p95_latency", requestRef: "req_slow_000001", operation: "批量分析" },
    { label: "缓存效率", policy: "cache_hit_rate", requestRef: "req_cache_000001", operation: "重复分析" },
    { label: "计价覆盖", policy: "unpriced_requests", requestRef: "req_unpriced_001", operation: "模型评审" },
    { label: "调用失败率", policy: "error_rate", requestRef: "req_error_000001", operation: "机会提取" },
  ];

  for (const item of cases) {
    const rule = rules.locator("li").filter({ hasText: item.label });
    const before = calls.filter((call) => call.path === "/api/finops/assistant/query").length;
    await rule.getByRole("button", { name: "问 AI" }).click();
    const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
    await expect(assistant).toContainText(`${item.label}当前需要关注`);
    await expect.poll(() => calls.filter((call) => call.path === "/api/finops/assistant/query").length).toBe(before + 1);
    const assistantCall = calls.filter((call) => call.path === "/api/finops/assistant/query").at(-1);
    const submitted = JSON.parse(assistantCall.body);
    expect(submitted.metric_context.policy_type).toBe(item.policy);
    expect(submitted.metric_context.evidence_refs).toEqual([item.requestRef]);

    await assistant.locator("article.assistant").last().getByRole("button", { name: "查看证据" }).click();
    const drawer = page.locator(".finops-drawer");
    await expect(drawer).toContainText(item.operation);
    expect(calls.filter((call) => call.path === `/api/finops/requests/${item.requestRef}`)).toHaveLength(1);
    await drawer.getByRole("button", { name: "关闭请求证据" }).click();
    await assistant.getByRole("button", { name: "关闭运营 AI" }).click();
  }

  const rejected = await page.evaluate(async () => {
    const response = await fetch("/api/finops/assistant/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: "尝试使用不属于时延规则的证据。",
        metric_context: {
          metric_id: "risk_p95_latency",
          label: "响应时延",
          value: 6200,
          unit: "ms",
          window: { from: "2026-07-01T00:00:00Z", to: "2026-07-25T00:00:00Z" },
          filters: { workspace_id: "demo-corpus" },
          data_status: "complete",
          evidence_state: "observed",
          policy_type: "p95_latency",
          evidence_refs: ["req_cache_000001"],
        },
      }),
    });
    return response.json();
  });
  expect(rejected.status).toBe("insufficient_data");
  expect(rejected.evidence_refs).toEqual([]);
});


test("risk scan keeps its completed summary when priority refresh fails and can retry", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls, { riskDecisionRefreshFailures: 1 });
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();

  await page.getByRole("button", { name: "重新扫描" }).click();
  await expect(page.locator(".finops-risk-scan-summary")).toContainText("146");
  const warning = page.getByRole("status").filter({ hasText: "更新失败" });
  await expect(warning).toContainText("当前继续展示最近一次成功结果");
  await expect(warning.getByRole("button", { name: "重新更新" })).toBeVisible();
  await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(4);

  await warning.getByRole("button", { name: "重新更新" }).click();
  await expect(page.getByRole("list", { name: "风险优先事项" }).getByRole("button")).toHaveCount(6);
  await expect(warning).toHaveCount(0);
  expect(calls.filter((item) => item.method === "POST" && item.path === "/api/finops/risk/scans")).toHaveLength(1);
});


test("operations AI hides validation internals and lets the user retry", async ({ page }) => {
  await installFinOpsMockApi(page, [], { assistantValidationFailures: 1 });
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();
  await page.locator(".finops-risk-scan-disclosure > summary").click();
  await page.locator(".finops-risk-scan-rules li").first().getByRole("button", { name: "问 AI" }).click();

  const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(assistant).toContainText("当前分析未完成，请重试");
  await expect(assistant).not.toContainText("string_pattern_mismatch");
  await expect(assistant).not.toContainText("metric_context");
  await assistant.getByRole("button", { name: "重试本次提问" }).click();
  await expect(assistant.getByText("结论", { exact: true })).toBeVisible();
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
  await expect(page.locator(".finops-trend-column")).toHaveCount(11);
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
  await expect(page.locator(".finops-trend-column")).toHaveCount(11);
  await expectDistinctGeometry(page.locator(".finops-trend-stack"), "height");
  await expect(page.locator(".finops-table")).toHaveCount(2);
  for (const table of await page.locator(".finops-table").all()) {
    const rows = table.locator("tbody tr");
    expect(await rows.count()).toBeGreaterThanOrEqual(1);
    const dimensions = await rows.locator("td:first-child").allTextContents();
    expect(new Set(dimensions).size).toBe(dimensions.length);
  }
  const departmentPanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "部门成本归因" }) });
  await expect(departmentPanel.locator("tbody tr")).toHaveCount(4);
  const workspacePanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "专案成本归因" }) });
  await expect(workspacePanel.locator("tbody tr")).toHaveCount(1);
  const agentPanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "Agent 成本归因" }) });
  const modelPanel = page.locator(".finops-panel").filter({ has: page.getByRole("heading", { name: "模型用量与缓存结构" }) });
  await expect(agentPanel.locator(".finops-bar-row")).toHaveCount(6);
  await expect(modelPanel.locator(".finops-model-usage-row")).toHaveCount(4);
  await expectDistinctGeometry(agentPanel.locator(".finops-bar-row i"), "width");
  await expect(modelPanel.locator(".finops-model-token-track")).toHaveCount(4);
  await expect(modelPanel).toContainText("deepseek-v4-flash");
  await expect(modelPanel).toContainText("缓存输入");
  const doughnuts = page.getByRole("img", { name: /成本结构/ });
  await expect(doughnuts).toHaveCount(0);

  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expectDemoSurfaceComplete(page);
  await expect(page.locator(".finops-decision-roi-metric")).toHaveCount(4);
  const roiValues = await page.locator(".finops-decision-roi-metric > strong").allTextContents();
  expect(new Set(roiValues).size).toBe(4);
  await expect(page.locator(".finops-decision-value-term")).toHaveCount(3);
  await expect(page.locator(".finops-decision-value-operator")).toHaveCount(2);
  await expect(page.locator(".finops-decision-value-result-strip")).toBeVisible();
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
  const stageList = page.getByRole("list", { name: "治理判断阶段" });
  const stageItems = stageList.locator("li");
  await expect(stageItems).toHaveCount(4);
  const connectors = await stageItems.evaluateAll((nodes) => nodes.map((node) => (
    getComputedStyle(node, "::after").content
  )));
  expect(connectors).toEqual(["none", "none", "none", "none"]);
  await expectNoOverlap(stageItems);
  const stageBox = await stageList.boundingBox();
  expect(stageBox.height).toBeLessThanOrEqual(86);
  await expect(page.locator(".finops-decision-risk-chain")).not.toContainText(
    /gateway_coverage|app_observed|unmanaged|unknown|provider_5xx/,
  );
  await expect(page.locator(".finops-decision-risk-recommendation")).not.toBeEmpty();
  await expect(page.locator(".finops-decision-risk-facts > div")).toHaveCount(4);
  await expect(page.locator(".finops-decision-risk-evidence-card")).toHaveCount(1);
  await expect(page.locator(".finops-decision-risk-insight")).not.toBeEmpty();
  await expect(page.locator(".finops-decision-risk-governance")).not.toBeEmpty();

  expect(control.calls.bootstrap).toBeGreaterThan(0);
  expect(control.calls.roiDecision).toBe(1);
  expect(control.calls.riskDecision).toBe(1);
});


test("ROI maturity stages open stage-specific request evidence", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();

  const actions = page.getByLabel("按阶段查看证据");
  await expect(actions.getByText("可打开的请求证据", { exact: true })).toBeVisible();
  const cases = [
    { stage: "投入", requestRef: "req_priced_000001", operation: "成本归因" },
    { stage: "使用", requestRef: "req_cache_000001", operation: "重复分析" },
    { stage: "产出", requestRef: "req_slow_000001", operation: "批量分析" },
    { stage: "业务结果", requestRef: "req_outcome_000001", operation: "业务结果复核" },
  ];

  for (const item of cases) {
    await actions.getByRole("button", { name: new RegExp(`^${item.stage} · 1 条$`) }).click();
    const drawer = page.locator(".finops-drawer");
    await expect(drawer).toContainText(item.operation);
    expect(calls.filter((call) => call.path === `/api/finops/requests/${item.requestRef}`)).toHaveLength(1);
    await drawer.getByRole("button", { name: "关闭请求证据" }).click();
  }
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


test("refresh state, risk footer, and cost assistant stay presentation ready", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await installFinOpsDemoCompletenessApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).last().click();

  const refresh = page.locator(".finops-live").getByRole("button", { name: "刷新运营数据" });
  await expect(refresh).toContainText("刷新");
  const refreshBox = await refresh.boundingBox();
  expect(refreshBox.width).toBeGreaterThanOrEqual(72);
  expect(refreshBox.height).toBeGreaterThanOrEqual(28);

  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await page.getByRole("button", { name: "打开运营 AI" }).click();
  const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
  await expect(assistant.getByRole("button", { name: "本月估算成本主要由哪些部门和模型贡献？" })).toBeVisible();
  await assistant.getByRole("button", { name: "本月估算成本主要由哪些部门和模型贡献？" }).click();
  await expect(assistant.locator(".finops-ai-answer-sections").last()).toContainText("$493.88");
  await expect(assistant.locator(".finops-ai-answer-sections").last()).toContainText("gpt-5.1");
  await expect(assistant.locator(".finops-ai-answer-sections").last()).toContainText("不等于云平台实际账单");
  await expect(assistant.locator(".finops-ai-generation").last()).toContainText("DeepSeek V4 Flash");
  await expect(assistant.locator(".finops-ai-generation").last()).toContainText("模型输入缓存 66.7%");
  await expect(assistant.locator(".finops-ai-knowledge-citations").last()).toContainText("内部方法参考");
  await expect(assistant.locator(".finops-ai-knowledge-citations").last()).toContainText("DataForge 成本与计价方法");
  await assistant.getByRole("button", { name: "关闭运营 AI" }).click();

  await page.getByRole("button", { name: "风险与优化" }).last().click();
  const footer = page.locator(".finops-decision-risk-footer-grid");
  await expect(footer).toBeVisible();
  for (const width of [1366, 1024, 820, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await footer.evaluate((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
      cardWidths: [...node.children].map((child) => child.getBoundingClientRect().width),
    }));
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.cardWidths.every((value) => value <= geometry.clientWidth + 1)).toBe(true);
  }
});


test("operations management stays usable on mobile without a full-screen AI drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).last().click();

  await expect.poll(() => calls.some((call) => call.path === "/api/finops/assistant/bootstrap")).toBe(true);

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
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  const modelCard = page.locator(".set-cfg").filter({ hasText: "模型与生成" });
  await modelCard.getByRole("button", { name: "配置模型与生成" }).click();
  await expect(page.getByText("Agent 模型分配")).toBeVisible();
  await expect(page.getByLabel("审计 Agent主要模型")).toHaveValue("terra");
  await expect(page.getByLabel("FinOps 分析 Agent主要模型")).toHaveValue("terra");
  await expect(page.getByLabel("ROI 分析 Agent主要模型")).toHaveValue("terra");
  await expect(page.getByLabel("快速回答主要模型")).toHaveValue("");
  await expect(page.getByLabel("深入分析主要模型")).toHaveValue("terra");
  await page.getByRole("button", { name: "保存模型分配" }).click();
  await expect.poll(() => calls.filter((call) => (
    call.method === "PUT"
    && call.path === "/api/workspaces/demo-corpus/governance/model-routing"
  )).length).toBe(1);
  const write = calls.find((call) => (
    call.method === "PUT"
    && call.path === "/api/workspaces/demo-corpus/governance/model-routing"
  ));
  const submitted = JSON.parse(write.body);
  expect(submitted.base_revision).toBe(3);
  expect(submitted.agent_assignments["df-finops-analyst"]).toEqual({
    primary_route_id: "terra",
    fallback_route_id: "analysis",
  });
  expect(submitted.agent_assignments["df-roi-analyst"]).toEqual({
    primary_route_id: "terra",
    fallback_route_id: "analysis",
  });
  await expect(page.locator(".side-drawer.wide")).toBeVisible();
  await page.getByRole("button", { name: "服务状态" }).click();
  await expect(page.getByRole("heading", { name: "关键服务状态" })).toBeVisible();
  await expect(page.getByText("当前可用")).toBeVisible();
  await expect(page.getByText("入口调用对账")).toBeVisible();
  await expect(page.getByText("尚未运行")).toBeVisible();
  await expect.poll(() => calls.filter((call) => call.path === "/api/service-readiness").length).toBe(1);
  const readinessText = await page.locator(".service-readiness-page").innerText();
  expect(readinessText).not.toMatch(/subscription|tenant_id|endpoint|secret|resource_id/i);
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "settings-service-readiness-desktop.png"),
    fullPage: true,
  });
});

test("settings summary reflects the persisted DeepSeek workspace route", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.route("**/api/workspaces/demo-corpus/governance/model-routing", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        workspace_id: "demo-corpus",
        default_route: "analysis",
        routes: [
          {
            id: "analysis",
            deployment: "gpt-5.1",
            label: "GPT-5.1",
            capabilities: ["analysis", "chat"],
          },
          {
            id: "ds_deepseek-primary_deepseek-v4-flash",
            deployment: "deepseek-v4-flash",
            model_id: "deepseek-v4-flash",
            provider_id: "deepseek-primary",
            provider_type: "deepseek",
            provider_label: "DeepSeek 原厂",
            label: "DeepSeek V4 Flash",
            capabilities: ["analysis", "chat"],
            selectable: true,
          },
        ],
        policy: {
          revision: 4,
          default_route_id: "ds_deepseek-primary_deepseek-v4-flash",
          assignments: {},
          agent_assignments: {},
        },
        price_card: { state: "configured", revision: 2, currency: "USD" },
      }),
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  const modelCard = page.locator(".set-cfg").filter({ hasText: "模型与生成" });
  await expect(modelCard).toContainText("DeepSeek V4 Flash");
  await expect(modelCard).toContainText("DeepSeek 原厂 · 工作区策略 · v4");

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-model-summary-deepseek-desktop.png"),
    fullPage: true,
  });
});

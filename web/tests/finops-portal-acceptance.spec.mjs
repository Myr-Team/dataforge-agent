import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openOperations(page) {
  await page.getByRole("button", { name: "成本管理" }).first().click();
  await expect(page.getByRole("heading", { name: "成本管理" })).toBeVisible();
}

async function openMemberBudgets(page) {
  await page.getByRole("button", { name: "设置" }).first().click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();
  await expect(page.getByRole("heading", { name: "成员成本预算" })).toBeVisible();
}


test("trend chart switches metric, unit and tooltip in sync", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const legend = page.locator(".finops-trend-legend");
  const scale = page.locator(".finops-trend-scale");
  const trendSwitch = page.locator(".finops-trend-switch");

  await expect(legend).toContainText("估算成本");
  await expect(scale.locator("span").first()).toContainText("$");
  const costBars = page.locator(".finops-trend-stack");
  const firstCostBar = await costBars.nth(0).boundingBox();
  const secondCostBar = await costBars.nth(1).boundingBox();
  expect(firstCostBar).not.toBeNull();
  expect(secondCostBar).not.toBeNull();
  expect(secondCostBar.height).toBeGreaterThan(firstCostBar.height);
  expect(secondCostBar.height - firstCostBar.height).toBeGreaterThan(5);

  await trendSwitch.getByRole("button", { name: "Token" }).click();
  await expect(legend).toContainText("输入");

  await trendSwitch.getByRole("button", { name: "P95" }).click();
  await expect(legend).toContainText("P95 延迟");
  await expect(scale.locator("span").first()).toContainText(/s|ms/);

  await trendSwitch.getByRole("button", { name: "调用" }).click();
  await expect(legend).toContainText("调用次数");
  const column = page.locator(".finops-trend-column").first();
  await expect(column).toHaveAttribute("aria-label", /次/);
  await column.focus();
  await expect(page.locator(".finops-trend-tooltip-content")).toContainText("调用次数");
  await expect(page.locator(".finops-trend-tooltip-content")).toContainText("缓存命中");
  await expect(page.locator(".finops-metric").filter({ hasText: "缓存收益" })).toContainText("命中率");
});


test("executive cost drilldown preserves the current filters and uses the compact cost page", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const overview = page.getByRole("region", { name: "运营决策概览" });
  await page.getByLabel("部门筛选", { exact: true }).selectOption("Commerce");
  await page.getByLabel("Agent 筛选", { exact: true }).selectOption("分析协调 Agent");
  await overview.getByRole("button", { name: /成本分析.*成本来自哪里/ }).click();

  await expect(page.getByRole("button", { name: "成本分析", exact: true })).toHaveClass(/active/);
  await expect(page.getByLabel("部门筛选", { exact: true })).toHaveValue("Commerce");
  await expect(page.getByLabel("Agent 筛选", { exact: true })).toHaveValue("分析协调 Agent");
  await expect(page.locator(".finops-cost-summary")).toBeVisible();
  await expect(page.locator(".finops-content > .finops-metrics")).toHaveCount(0);
});


test("ROI parameters create a new DataForge scenario revision", async ({ page }) => {
  const calls = [];
  const control = await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();

  await expect(page.getByRole("heading", { name: "测算显示具备投入价值，业务结果仍需验证" })).toBeVisible();
  const initialDecisionCalls = control.calls.roiDecision;
  await page.getByRole("button", { name: "调整测算参数" }).click();
  const dialog = page.getByRole("dialog", { name: "调整 ROI 测算参数" });
  await expect(dialog.getByText("当前模型成本 $0.0269 / 月")).toBeVisible();
  await dialog.getByLabel("每月节省工时").fill("48");
  await dialog.getByRole("button", { name: "保存新版本" }).click();

  await expect(dialog).toHaveCount(0);
  await expect.poll(() => control.calls.roiDecision).toBeGreaterThan(initialDecisionCalls);
  await expect(page.getByRole("heading", { name: "测算显示具备投入价值，业务结果仍需验证" })).toBeVisible();
  const write = calls.find((call) => (
    call.path === "/api/workspaces/demo-corpus/governance/scenarios"
    && call.method === "POST"
  ));
  expect(write).toBeTruthy();
  expect(JSON.parse(write.body)).toMatchObject({
    hours_saved: 48,
    hourly_value: 50,
    avoided_loss_or_revenue: 1000,
    implementation_cost: 6000,
    monthly_fixed_cost: 200,
    model_cost: 0.0269,
    evaluation_months: 12,
    previous_id: "roi_scenario_demo0001",
    base_revision: 1,
  });
});


test("overview hides infrastructure reconciliation and keeps pricing coverage in cost analysis", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const overview = page.getByRole("region", { name: "运营决策概览" });
  await expect(overview).not.toContainText("APIM");
  await expect(overview).not.toContainText("网关");
  await expect(overview).not.toContainText("unattributed");
  await expect(overview.locator(".finops-metric")).toHaveCount(4);

  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expect(page.locator(".finops-cost-summary")).toContainText("计价覆盖 96.7%");
  await expect(page.locator(".finops-cost-summary")).toContainText("2 次未计价");

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-cost-pricing-coverage-desktop.png"),
    fullPage: true,
  });
});


test("visible decision refresh waits ten minutes and pauses while hidden", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-30T08:00:00Z") });
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
  await expect(page.getByRole("heading", { name: "价值桥" })).toBeVisible();

  const initialCount = control.calls.roiDecision;
  await page.clock.runFor(599_999);
  expect(control.calls.roiDecision).toBe(initialCount);
  await page.clock.runFor(1);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => control.calls.roiDecision).toBe(initialCount + 1);

  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(600_000);
  expect(control.calls.roiDecision).toBe(initialCount + 1);

  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => control.calls.roiDecision).toBe(initialCount + 2);
});


test("detail refresh failure preserves the last successful page", async ({ page }) => {
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expect(page.getByText("成本趋势")).toBeVisible();

  control.failDetail = true;
  await page.locator(".finops-live").getByRole("button", { name: "刷新" }).click();

  await expect(page.getByText("成本趋势")).toBeVisible();
  await expect(page.locator(".finops-inline-error")).toContainText(
    "更新失败，已保留上次数据",
  );
});


test("risk evidence is distinct and remediation 409 requires reload and a second review", async ({ page }) => {
  const control = await installFinOpsMockApi(page, [], { remediationReviewConflictOnce: true });
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "风险与优化", exact: true }).click();

  const priorities = page.getByRole("list", { name: "风险优先事项" });
  await priorities.getByRole("button", { name: /响应时延优化/ }).click();
  await expect(page.getByText("6,200 ms", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("分析已完成，但模型响应阶段耗时偏高。")).toBeVisible();
  await priorities.getByRole("button", { name: /缓存效率优化/ }).click();
  await expect(page.getByText("缓存未命中", { exact: true })).toBeVisible();
  await expect(page.getByText("本次请求未命中结果缓存，已重新执行分析。")).toBeVisible();
  await priorities.getByRole("button", { name: /计价覆盖补齐/ }).click();
  await expect(page.getByText("评审已完成，当前模型尚未关联价目。")).toBeVisible();
  await priorities.getByRole("button", { name: /调用成功率改善/ }).click();
  await expect(page.getByText("provider_5xx")).toBeVisible();

  await priorities.getByRole("button", { name: /缓存效率优化/ }).click();
  await page.getByRole("button", { name: "查看整改方案" }).click();
  const panel = page.getByRole("dialog", { name: "整改草案" });
  await expect(panel.getByText("不会直接执行生产变更")).toBeVisible();
  await panel.getByRole("button", { name: "保存整改草案" }).click();
  await expect(panel.getByText("可转为审批动作草案").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "候选执行" })).toHaveCount(0);

  await panel.getByRole("button", { name: "复核草案" }).click();
  await expect(panel.getByText("方案已更新，请重新复核")).toBeVisible();
  await panel.getByRole("button", { name: "复核草案" }).click();
  await expect(panel.getByText("已复核", { exact: true }).first()).toBeVisible();
  expect(control.calls.remediationCreate).toBe(1);
  expect(control.calls.remediationReview).toBe(2);

  const header = await page.locator(".topbar").boundingBox();
  const surface = await panel.boundingBox();
  expect(surface.y).toBeGreaterThanOrEqual(header.y + header.height);
  expect(surface.x + surface.width).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth));

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-remediation-reviewed-desktop.png"),
    fullPage: true,
  });
});


for (const viewport of [
  { name: "1366", width: 1366, height: 900 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`remediation panel stays reachable scrollable and restores focus at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await installFinOpsMockApi(page);
    await page.goto("/");
    await openOperations(page);
    await page.getByRole("button", { name: "风险与优化", exact: true }).click();
    const priorities = page.getByRole("list", { name: "风险优先事项" });
    await priorities.getByRole("button", { name: /缓存效率优化/ }).click();
    const trigger = page.getByRole("button", { name: "查看整改方案" });
    await trigger.focus();
    await trigger.click();

    const panel = page.getByRole("dialog", { name: "整改草案" });
    const close = panel.getByRole("button", { name: "关闭整改草案" });
    await expect(panel).toBeVisible();
    await expect(close).toBeEnabled();
    await expect(close).toBeFocused();
    await panel.getByRole("button", { name: "保存整改草案" }).click();
    await expect(panel.locator(".finops-remediation-actions")).toBeVisible();
    const [topbar, bounds] = await Promise.all([
      page.locator(".topbar").boundingBox(),
      panel.boundingBox(),
    ]);
    expect(bounds.y).toBeGreaterThanOrEqual(topbar.y + topbar.height - 1);
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);

    const scroll = await panel.evaluate((node) => ({
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
    }));
    expect(scroll.scrollHeight).toBeGreaterThan(scroll.clientHeight);
    const bottom = await panel.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
      return node.scrollTop;
    });
    expect(bottom).toBeGreaterThan(0);
    await expect(panel.locator(".finops-remediation-actions")).toBeInViewport();

    await close.click();
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
}


test("detail failure after a range change never labels old data as the new range", async ({ page }) => {
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);
  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expect(page.getByText("成本趋势")).toBeVisible();

  control.failDetail = true;
  await page.locator(".finops-date-range input").first().fill("2026-06-01");

  await expect(page.locator(".finops-state-error")).toBeVisible();
  await expect(page.getByText("成本趋势")).toHaveCount(0);
  await expect(page.locator(".finops-inline-error")).toHaveCount(0);
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
  await expect(page.getByRole("img", { name: "部门估算成本占比" })).toBeVisible();

  expect(pageErrors).toEqual([]);
});


test("mobile operations layout has no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "成本管理" }).last().click();
  await expect(page.getByRole("heading", { name: "成本管理" })).toBeVisible();

  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);

  await expect(page.getByRole("button", { name: "成本分析", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "成本分析", exact: true }).click();
  await expect(page.getByText("成本趋势")).toBeVisible();
});


test("settings exposes a compact budget entry and dedicated desktop page", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();

  const entry = page.locator(".member-budget-entry");
  await expect(entry).toContainText("成本预算与提醒");
  await expect(entry).toContainText("1 位接近预算");
  await expect(entry).toContainText("邮件已配置");
  await expect(entry.getByRole("button", { name: "配置成本预算与提醒" })).toBeVisible();

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "task6-member-budget-entry-desktop.png"),
    fullPage: true,
  });

  await entry.getByRole("button", { name: "配置成本预算与提醒" }).click();
  await expect(page.getByRole("heading", { name: "成员成本预算" })).toBeVisible();
  await expect(page.getByText("本月估算成本")).toBeVisible();
  await expect(page.getByText("$190.00").first()).toBeVisible();
  await expect(page.locator(".member-budget-summary-card").filter({ hasText: "已配置成员" }).locator("strong")).toHaveText("2");
  await expect(page.getByText("90% 已计价").first()).toBeVisible();
  const formerSubtitle = page.locator(".member-budget-table").getByText("预算主体已停用 · 预算已停用 · 未归属部门");
  await expect(formerSubtitle).toBeVisible();
  await expect(formerSubtitle).toHaveAttribute("title", "预算主体已停用 · 预算已停用 · 未归属部门");
  await expect(formerSubtitle).toHaveAttribute("aria-label", "预算主体已停用 · 预算已停用 · 未归属部门");

  const body = await page.locator("body").innerText();
  expect(body).not.toContain("member-safe");
  expect(body).not.toContain("tenant-raw");
  expect(body).not.toContain("actor-raw");
  expect(body).not.toContain("operation_id");
  expect(body).not.toMatch(/endpoint=|access[_ -]?key|secret[_ -]?access/i);
  expect(pageErrors).toEqual([]);

  await page.screenshot({
    path: path.join(outputDir, "task6-member-budget-page-desktop.png"),
    fullPage: true,
  });
});


test("workspace administrator permission state is explicit and hides budget evidence", async ({ page }) => {
  await installFinOpsMockApi(page, [], {
    memberBudgetAccessState: "permission_required",
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();

  const entry = page.locator(".member-budget-entry");
  await expect(entry).toContainText("需要工作区管理员权限");
  await expect(entry).toContainText("预算与提醒已受限");
  await expect(entry).not.toContainText("不可用");
  const permissionAction = entry.getByRole("button", { name: "查看成本预算权限说明" });
  await expect(permissionAction).toBeEnabled();
  await permissionAction.click();

  await expect(page.getByRole("heading", { name: "成员成本预算" })).toBeVisible();
  await expect(page.getByText("需要工作区管理员权限")).toBeVisible();
  await expect(page.getByText("请由当前工作区的 Owner 或 Admin 打开并配置成员预算。")).toBeVisible();
  await expect(page.locator(".member-budget-table")).toHaveCount(0);
  await expect(page.locator(".member-budget-summary-card")).toHaveCount(0);
  await expect(page.getByText("$190.00")).toHaveCount(0);
  await expect(page.getByText("成员预算暂时不可用")).toHaveCount(0);
});


test("member budget edit preserves decimal amount, thresholds and conflict reload", async ({ page }) => {
  const calls = [];
  const control = await installFinOpsMockApi(page, calls, { memberBudgetConflictOnce: true });
  await page.goto("/");
  await openMemberBudgets(page);

  await page.getByRole("button", { name: "编辑 Finance Admin 预算" }).click();
  const dialog = page.getByRole("dialog", { name: "编辑成员预算" });
  await expect(dialog.getByLabel("月度预算（USD）")).toHaveValue("200");
  await dialog.getByLabel("月度预算（USD）").fill("200.50");
  await expect(dialog.getByLabel("提醒阈值")).toHaveValue("80, 95, 100");
  await dialog.getByRole("button", { name: "保存预算" }).click();

  await expect(page.getByText("配置已更新，正在重新载入")).toBeVisible();
  await expect(page.getByRole("heading", { name: "成员成本预算" })).toBeVisible();
  expect(control.memberBudgetConflictOnce).toBe(false);

  await page.getByRole("button", { name: "编辑 Finance Admin 预算" }).click();
  await page.getByRole("dialog", { name: "编辑成员预算" }).getByLabel("月度预算（USD）").fill("200.50");
  await page.getByRole("dialog", { name: "编辑成员预算" }).getByRole("button", { name: "保存预算" }).click();
  await expect(page.getByText("预算已保存")).toBeVisible();

  const writes = calls.filter((call) => call.path === "/api/finops/member-budgets/budget-safe" && call.method === "PATCH");
  expect(JSON.parse(writes.at(-1).body)).toEqual({
    amount_usd: 200.5,
    thresholds_pct: [80, 95, 100],
    enabled: true,
    base_revision: 3,
  });
});


test("mail settings configure and test use safe states", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls);
  await page.goto("/");
  await openMemberBudgets(page);

  await page.getByRole("button", { name: "配置邮件" }).click();
  const dialog = page.getByRole("dialog", { name: "邮件提醒设置" });
  await expect(dialog.getByText("收件地址保存在服务端配置中")).toBeVisible();
  await dialog.getByLabel("管理员收件邮箱").fill("finance-owner@example.test");
  await dialog.getByRole("button", { name: "保存邮件设置" }).click();
  await expect(page.getByText("邮件设置已保存")).toBeVisible();

  await page.getByRole("button", { name: "发送测试邮件" }).click();
  await expect(page.getByText("测试邮件已发送")).toBeVisible();
  const testCall = calls.find((call) => call.path === "/api/finops/notification-settings/test-email");
  expect(testCall).toBeTruthy();
  expect(testCall.body).toBe("{}");
  const saveCall = calls.find((call) => call.path === "/api/finops/notification-settings" && call.method === "PUT");
  expect(JSON.parse(saveCall.body).recipient_email).toBe("finance-owner@example.test");
});


test("test email maps not configured and permission failures to safe guidance", async ({ page }) => {
  const control = await installFinOpsMockApi(page);
  await page.goto("/");
  await openMemberBudgets(page);

  control.memberBudgetEmailState = "not_configured";
  await page.getByRole("button", { name: "发送测试邮件" }).click();
  await expect(page.getByText("邮件服务尚未配置")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("must-not-surface");

  control.memberBudgetEmailState = "permission_required";
  await page.getByRole("button", { name: "发送测试邮件" }).click();
  await expect(page.getByText("托管身份缺少邮件发送权限")).toBeVisible();
  await expect(page.getByText("自动发送默认关闭")).toBeVisible();
});


test("member budget mobile layout keeps actions first and has no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).last().click();
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();

  await expect(page.locator(".member-budget-page-actions")).toBeVisible();
  await expect(page.locator(".member-budget-mobile-list")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "task6-member-budget-page-mobile.png"),
    fullPage: true,
  });
});


test("member budget failure and empty states stay truthful", async ({ page }) => {
  const control = await installFinOpsMockApi(page, [], { memberBudgetFailure: true });
  await page.goto("/");
  await openMemberBudgets(page);

  await expect(page.getByText("成员预算暂时不可用")).toBeVisible();
  await expect(page.locator(".member-budget-summary-card strong")).toHaveText(["不可用", "不可用", "不可用", "不可用"]);
  await expect(page.locator("body")).not.toContainText("Failed to fetch");

  control.memberBudgetFailure = false;
  control.memberBudgetEmpty = true;
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByText("尚未设置成员预算")).toBeVisible();
  await expect(page.getByText("$0.00")).toHaveCount(0);
  await expect(page.locator(".member-budget-alerts")).toContainText("暂无提醒记录");
  await expect(page.locator(".member-budget-alerts")).not.toContainText("提醒记录暂时不可用");
});


test("disabled active budgets disable, edit and re-enable without duplicate create choices", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls, { memberBudgetActiveDisabled: true });
  await page.goto("/");
  await openMemberBudgets(page);

  await page.getByRole("button", { name: "设置成员预算" }).click();
  const createDialog = page.getByRole("dialog", { name: "设置成员预算" });
  await expect(createDialog.getByLabel("预算成员").getByRole("option")).toHaveText(["IT Operator · 成员"]);
  await createDialog.getByRole("button", { name: "关闭" }).click();

  const financeEdit = page.getByRole("button", { name: "编辑 Finance Admin 预算" });
  await financeEdit.click();
  const editDialog = page.getByRole("dialog", { name: "编辑成员预算" });
  await editDialog.getByRole("button", { name: "停用预算" }).click();
  await editDialog.getByRole("button", { name: "确认停用" }).click();
  await expect(page.getByText("预算已停用", { exact: true }).first()).toBeVisible();
  expect(calls.some((call) => call.method === "POST" && call.path === "/api/finops/member-budgets/budget-safe/disable")).toBe(true);

  await page.getByRole("button", { name: "编辑 Finance Admin 预算" }).click();
  const reenableDialog = page.getByRole("dialog", { name: "编辑成员预算" });
  await expect(reenableDialog.getByLabel("启用本月预算")).not.toBeChecked();
  await reenableDialog.getByLabel("启用本月预算").check();
  await reenableDialog.getByRole("button", { name: "保存预算" }).click();
  await expect(page.getByText("预算已保存")).toBeVisible();
  const lastPatch = calls.filter((call) => call.method === "PATCH" && call.path === "/api/finops/member-budgets/budget-safe").at(-1);
  expect(JSON.parse(lastPatch.body).enabled).toBe(true);
});


test("settings home budget badges refresh after child mail mutation and return", async ({ page }) => {
  await installFinOpsMockApi(page, [], { memberBudgetNotificationState: "not_configured" });
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();
  await expect(page.locator(".member-budget-entry")).toContainText("邮件未配置");
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();

  await page.getByRole("button", { name: "配置邮件" }).click();
  const mailDialog = page.getByRole("dialog", { name: "邮件提醒设置" });
  await mailDialog.getByLabel("管理员收件邮箱").fill("admin@example.test");
  await mailDialog.getByRole("button", { name: "保存邮件设置" }).click();
  await expect(page.getByText("邮件设置已保存")).toBeVisible();
  await page.getByRole("button", { name: "返回设置" }).click();

  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.locator(".member-budget-entry")).toContainText("邮件已配置");
});


test("settings home names tenant FinOps permission for a notification authorization failure", async ({ page }) => {
  await installFinOpsMockApi(page, [], {
    memberBudgetNotificationState: "permission_required",
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).first().click();

  const entry = page.locator(".member-budget-entry");
  await expect(entry).toContainText("1 位接近预算");
  await expect(entry).toContainText("需要组织 FinOps 管理员权限");
  await expect(entry).not.toContainText("邮件状态不可用");
  await expect(page.getByRole("button", { name: "配置成本预算与提醒" })).toBeEnabled();
});


test("notification and alert service availability stay independent in the page", async ({ page }) => {
  await installFinOpsMockApi(page, [], {
    memberBudgetNotificationState: "unavailable",
    memberBudgetAlertsState: "unavailable",
  });
  await page.goto("/");
  await openMemberBudgets(page);

  await expect(page.locator(".member-budget-mail-strip")).toContainText("邮件状态不可用");
  await expect(page.locator(".member-budget-mail-strip")).not.toContainText("尚未配置");
  await expect(page.locator(".member-budget-alerts")).toContainText("提醒记录暂时不可用");
  await expect(page.locator(".member-budget-summary-card").filter({ hasText: "已发送提醒" }).locator("strong")).toHaveText("不可用");
});


test("disabled email configuration is honest and cannot open configuration actions", async ({ page }) => {
  await installFinOpsMockApi(page, [], {
    memberBudgetNotificationState: "disabled",
  });
  await page.goto("/");
  await openMemberBudgets(page);

  await expect(page.locator(".member-budget-mail-strip")).toContainText("邮件配置未启用");
  await expect(page.getByRole("button", { name: "配置邮件" })).toBeDisabled();
  await expect(page.locator(".member-budget-mail-strip").getByRole("button", { name: "配置" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "发送测试邮件" })).toBeDisabled();
  await expect(page.getByRole("dialog", { name: "邮件提醒配置" })).toHaveCount(0);
});


test("notification authorization failure names tenant FinOps administrator permission", async ({ page }) => {
  await installFinOpsMockApi(page, [], {
    memberBudgetNotificationState: "permission_required",
  });
  await page.goto("/");
  await openMemberBudgets(page);

  await expect(page.locator(".member-budget-mail-strip")).toContainText("需要组织 FinOps 管理员权限");
  await expect(page.getByRole("button", { name: "配置邮件" })).toBeDisabled();
  await expect(page.locator(".member-budget-mail-strip").getByRole("button", { name: "配置" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "发送测试邮件" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "设置成员预算" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "编辑 Finance Admin 预算" })).toBeEnabled();
});


test("member budget modal traps focus, makes background inert and restores the trigger", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openMemberBudgets(page);

  const trigger = page.getByRole("button", { name: "编辑 Finance Admin 预算" });
  await trigger.focus();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "编辑成员预算" });
  const amount = dialog.getByLabel("月度预算（USD）");
  await expect(amount).toBeFocused();
  await expect(page.locator("#root")).toHaveAttribute("inert", "");

  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "保存预算" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(amount).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page.locator("#root")).not.toHaveAttribute("inert", "");
});


test("two-column metric help tooltips stay inside intermediate mobile viewports", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openMemberBudgets(page);

  for (const width of [390, 431, 500, 600, 720]) {
    await page.setViewportSize({ width, height: 900 });
    const helps = page.locator(".member-budget-summary-card .member-budget-help");
    await expect(helps).toHaveCount(4);
    for (let index = 0; index < 4; index += 1) {
      await helps.nth(index).focus();
      const tooltip = helps.nth(index).locator(".member-budget-tooltip");
      await expect(tooltip).toBeVisible();
      const box = await tooltip.boundingBox();
      expect(box.x, `tooltip ${index + 1} at ${width}px starts inside viewport`).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, `tooltip ${index + 1} at ${width}px ends inside viewport`).toBeLessThanOrEqual(width);
    }
  }
});

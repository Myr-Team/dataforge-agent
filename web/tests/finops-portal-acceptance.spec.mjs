import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "playwright/test";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";


async function openOperations(page) {
  await page.getByRole("button", { name: "运营管理" }).first().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();
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

  await expect(legend).toContainText("输入");

  await trendSwitch.getByRole("button", { name: "成本" }).click();
  await expect(legend).toContainText("估算成本");
  await expect(scale.locator("span").first()).toContainText("$");

  await trendSwitch.getByRole("button", { name: "P95" }).click();
  await expect(legend).toContainText("P95 延迟");
  await expect(scale.locator("span").first()).toContainText(/s|ms/);

  await trendSwitch.getByRole("button", { name: "调用" }).click();
  await expect(legend).toContainText("调用次数");
  const column = page.locator(".finops-trend-column").first();
  await expect(column).toHaveAttribute("aria-label", /次/);
  await column.focus();
  await expect(column.locator(".finops-trend-tooltip")).toContainText("调用次数");
});


test("APIM coverage surfaces unattributed gateway evidence with scope label", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.goto("/");
  await openOperations(page);

  const evidence = page.locator(".finops-gateway-evidence");
  await expect(evidence).toBeVisible();
  await expect(evidence).toContainText("未归属网关证据");
  await expect(evidence.locator(".finops-scope-tag")).toContainText("unattributed");
  await expect(evidence).toContainText("已关联请求");
  await expect(evidence).toContainText("未关联网关错误");
  await expect(evidence).toContainText("4xx 3");
  await expect(evidence).toContainText("5xx 1");
  await expect(evidence).toContainText("数据更新时间");

  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({
    path: path.join(outputDir, "operations-gateway-evidence-desktop.png"),
    fullPage: true,
  });
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
  await expect(page.getByText("数据可信度")).toBeVisible();

  expect(pageErrors).toEqual([]);
});


test("mobile operations layout has no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "运营管理" }).last().click();
  await expect(page.getByRole("heading", { name: "运营管理" })).toBeVisible();

  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);

  await expect(page.getByRole("button", { name: "成本分析" }).first()).toBeVisible();
  await page.getByRole("button", { name: "成本分析" }).first().click();
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
  await expect(page.locator(".member-budget-table").getByText("身份已停用 · 预算已停用 · 未归属部门")).toBeVisible();

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
  await expect(dialog.getByText("收件地址由 Entra 管理")).toBeVisible();
  await dialog.getByLabel("管理员").selectOption("member-safe");
  await dialog.getByRole("button", { name: "保存邮件设置" }).click();
  await expect(page.getByText("邮件设置已保存")).toBeVisible();

  await page.getByRole("button", { name: "发送测试邮件" }).click();
  await expect(page.getByText("测试邮件已发送")).toBeVisible();
  const testCall = calls.find((call) => call.path === "/api/finops/notification-settings/test-email");
  expect(testCall).toBeTruthy();
  expect(testCall.body).toBe("{}");
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
});


test("disabled active budgets disable, edit and re-enable without duplicate create choices", async ({ page }) => {
  const calls = [];
  await installFinOpsMockApi(page, calls, { memberBudgetActiveDisabled: true });
  await page.goto("/");
  await openMemberBudgets(page);

  await page.getByRole("button", { name: "设置成员预算" }).click();
  const createDialog = page.getByRole("dialog", { name: "设置成员预算" });
  await expect(createDialog.getByLabel("Entra 成员").getByRole("option")).toHaveText(["IT Operator · 成员"]);
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
  await page.getByRole("dialog", { name: "邮件提醒设置" }).getByRole("button", { name: "保存邮件设置" }).click();
  await expect(page.getByText("邮件设置已保存")).toBeVisible();
  await page.getByRole("button", { name: "返回设置" }).click();

  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.locator(".member-budget-entry")).toContainText("邮件已配置");
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
  await expect(dialog.getByRole("button", { name: "确认停用" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "保存预算" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(amount).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page.locator("#root")).not.toHaveAttribute("inert", "");
});


test("mobile metric help tooltips stay inside the viewport for odd and even cards", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFinOpsMockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).last().click();
  await page.getByRole("button", { name: "配置成本预算与提醒" }).click();

  const helps = page.locator(".member-budget-summary-card .member-budget-help");
  for (const index of [0, 1]) {
    await helps.nth(index).focus();
    const tooltip = helps.nth(index).locator(".member-budget-tooltip");
    await expect(tooltip).toBeVisible();
    const box = await tooltip.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
  }
});

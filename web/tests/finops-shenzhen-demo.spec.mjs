import { expect, test } from "playwright/test";
import {
  installFinOpsShenzhenDemoApi,
  SHENZHEN_CANONICAL_DIGEST,
  SHENZHEN_POLICY_REFS,
  SHENZHEN_REFS,
  SHENZHEN_SUMMARY,
} from "./finopsShenzhenDemoMock.mjs";

const POLICY_LABELS = [
  ["error_rate", "调用失败率"],
  ["p95_latency", "响应时延"],
  ["daily_cost_budget", "成本预算"],
  ["token_spike", "Token 异常增长"],
  ["apim_coverage", "统一入口治理覆盖"],
  ["unpriced_requests", "计价覆盖"],
  ["cache_hit_rate", "缓存效率"],
];

async function expectNoHorizontalClipping(page, selector) {
  const geometry = await page.locator(selector).evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      left: rect.left,
      right: rect.right,
      width: rect.width,
    };
  });
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth + 2);
  expect(geometry.left).toBeGreaterThanOrEqual(-1);
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.width).toBeGreaterThan(0);
}

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`Shenzhen demo evidence closes overview to Trace on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const calls = [];
    await installFinOpsShenzhenDemoApi(page, calls);
    await page.goto("/");
    await page.getByRole("button", { name: "成本管理" }).last().click();
    const finops = page.locator(".finops-content");
    await expect(finops).toContainText(SHENZHEN_SUMMARY.requests.toLocaleString("en-US"));
    await expect(finops).toContainText("$206.40");
    await expect(finops).not.toContainText(/2,404|销售|客户/);
    await expectNoHorizontalClipping(page, ".finops-content");

    await page.getByRole("button", { name: "成本分析", exact: true }).click();
    await expect(finops).toContainText("请求级价目估算");
    await expect(finops).toContainText("未计价请求160");
    await expect(finops).toContainText("gpt-5.6-terra");
    await expect(finops).toContainText("site-selection-unpriced-adapter");
    await expect(page.getByRole("button", { name: "维护计价映射" })).toBeVisible();
    await expectNoHorizontalClipping(page, ".finops-content");

    await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
    await expect(finops).toContainText("293.3%");
    await expect(finops).toContainText("演示验证结果 · 合成数据");
    await expect(finops).toContainText("96 个分析任务 · 78 份报告 · 18 项证据审阅 · 已审阅节省 174.6h");
    await expect(finops).toContainText("证据清单 96 个任务 · 78 份报告 · 18 项审阅");
    await expect(finops).toContainText("2026-07-12T12:00:00Z 至 2026-08-11T12:00:00Z · USD");
    await expect(finops).toContainText("synthetic_site_selection_outcome_reviewer");
    await expect(finops).toContainText("synthetic_site_selection_finance_reviewer");
    await expect(finops).toContainText(SHENZHEN_REFS.attempt);
    await expectNoHorizontalClipping(page, ".finops-content");

    await page.getByRole("button", { name: "风险与优化", exact: true }).click();
    await expect(finops).toContainText("深圳选址");
    const disclosure = page.locator("details.finops-risk-scan-disclosure");
    await disclosure.locator("summary").click();
    const rules = disclosure.locator(".finops-risk-scan-rules > li");
    await expect(rules).toHaveCount(7);
    for (const [policyType, label] of POLICY_LABELS) {
      const rule = rules.filter({ hasText: label });
      await expect(rule).toHaveCount(1);
      await expect(rule).toContainText("样本");
      await rule.getByRole("button", { name: "问 AI" }).click();
      const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
      await expect(assistant).toContainText(label);
      await expect.poll(() => calls.filter((item) => item.path === "/api/finops/assistant/query").length).toBeGreaterThan(0);
      const submitted = JSON.parse(calls.filter((item) => item.path === "/api/finops/assistant/query").at(-1).body);
      expect(submitted.metric_context.policy_type).toBe(policyType);
      expect(submitted.metric_context.evidence_refs).toEqual(SHENZHEN_POLICY_REFS[policyType].slice(0, 3));
      await assistant.getByRole("button", { name: "关闭运营 AI" }).click();
    }
    const uniqueEvidenceRefs = new Set(Object.values(SHENZHEN_POLICY_REFS).flat());
    expect(uniqueEvidenceRefs.size).toBeGreaterThanOrEqual(29);
    await rules.first().getByRole("button", { name: "查看证据" }).click();
    const evidenceDrawer = page.locator(".finops-drawer-backdrop");
    await expect(evidenceDrawer).toContainText(SHENZHEN_POLICY_REFS.error_rate[0]);
    await expect(evidenceDrawer).toContainText("技术信息（按需查看）");
    await expect(evidenceDrawer).toContainText("MAF 运行");
    await evidenceDrawer.getByRole("button", { name: /关闭/ }).click();
    await expectNoHorizontalClipping(page, ".finops-content");

    await page.getByRole("button", { name: "运行记录" }).last().click();
    await expect(page.getByRole("main")).toContainText("运行记录 · 可观测性");
    await page.locator(`[title="${SHENZHEN_REFS.run} · 查看 Trace"]`).click();
    let traceExplorer = page.getByTestId("run-trace-explorer");
    await expect(traceExplorer).toContainText("运行 Trace 详情");
    await expect(traceExplorer).toContainText("深圳选址评估");
    await expect(traceExplorer.getByLabel("模型与缓存证据")).toContainText("结果缓存");
    await expect(traceExplorer.getByLabel("模型与缓存证据")).toContainText("未命中");
    await expect(traceExplorer.getByLabel("模型与缓存证据")).toContainText("模型侧 Token 缓存");
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.request);
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.correlation);
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.attempt);
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText("synthetic_demo");
    const traceJson = traceExplorer.locator(".trace-json-panel pre");
    await expect(traceJson).toBeVisible();
    await expect(traceJson).toHaveCSS("overflow-y", "auto");
    await expect(traceJson).toHaveCSS("color", "rgb(23, 32, 51)");
    await expect(traceJson).not.toContainText(/authorization|api[-_]?key|secret/i);
    await expectNoHorizontalClipping(page, "[data-testid=run-trace-explorer]");

    await page.getByRole("button", { name: "返回运行记录" }).click();
    await page.locator(`[title="${SHENZHEN_REFS.hit_run} · 查看 Trace"]`).click();
    traceExplorer = page.getByTestId("run-trace-explorer");
    await expect(traceExplorer.getByLabel("模型与缓存证据")).toContainText("命中");
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.result);
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.hit_request);
    await expectNoHorizontalClipping(page, "[data-testid=run-trace-explorer]");

    const requestDetail = await page.evaluate(async (requestRef) => (await fetch(`/api/finops/requests/${requestRef}`)).json(), SHENZHEN_REFS.request);
    expect(requestDetail.metrics.estimated_cost.official_price_key).toBe("deepseek:deepseek-v4-flash:official");
    expect(requestDetail.metrics.provider_cache.evidence_state).toBe("synthetic");
    expect(requestDetail.technical_refs).toMatchObject({ request_ref: SHENZHEN_REFS.request, run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt });

    const trace = await page.evaluate(async (runId) => (await fetch(`/api/runs/${runId}/trace`)).json(), SHENZHEN_REFS.run);
    expect(trace[0].data).toMatchObject({ provenance: "synthetic_demo", request_ref: SHENZHEN_REFS.request, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt, route_evidence: "synthetic" });
    expect(trace[0].data.result_cache.state).toBe("miss");

    const catalog = await page.evaluate(async () => (await fetch("/api/finops/pricing/catalog")).json());
    const mappings = await page.evaluate(async () => (await fetch("/api/finops/pricing/mappings")).json());
    expect(catalog.items.map((item) => item.provider)).toEqual(expect.arrayContaining(["azure_foundry", "deepseek"]));
    expect(mappings.items).toContainEqual(expect.objectContaining({ official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", mapping_revision: 1 }));

    const hitDetail = await page.evaluate(async (requestRef) => (await fetch(`/api/finops/requests/${requestRef}`)).json(), SHENZHEN_REFS.hit_request);
    expect(hitDetail.metrics.result_cache).toMatchObject({ state: "hit", source_result_version: SHENZHEN_REFS.result });
    expect(SHENZHEN_CANONICAL_DIGEST).toMatch(/^[a-f0-9]{64}$/);
  });
}

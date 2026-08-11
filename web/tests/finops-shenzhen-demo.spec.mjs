import { expect, test } from "playwright/test";
import { installFinOpsShenzhenDemoApi, SHENZHEN_REFS } from "./finopsShenzhenDemoMock.mjs";

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`Shenzhen demo evidence closes overview to Trace on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const calls = [];
    await installFinOpsShenzhenDemoApi(page, calls);
    await page.goto("/");
    await page.getByRole("button", { name: "成本管理" }).last().click();
    await expect(page.locator(".finops-content")).toContainText("2,480");
    await expect(page.locator(".finops-content")).toContainText("$206.40");
    await page.getByRole("button", { name: "成本分析", exact: true }).click();
    await expect(page.locator(".finops-content")).toContainText("请求级价目估算");
    await expect(page.locator(".finops-content")).toContainText("未计价请求160");
    await expect(page.getByRole("button", { name: "维护计价映射" })).toBeVisible();
    await expect(page.locator(".finops-content")).not.toContainText(/2,404|销售|客户/);

    await page.getByRole("button", { name: "效能与 ROI", exact: true }).click();
    await expect(page.locator(".finops-content")).toContainText("293.3%");
    await expect(page.locator(".finops-content")).toContainText("演示验证结果 · 合成数据");
    await expect(page.locator(".finops-content")).toContainText("96 个分析任务 · 78 份报告 · 18 项证据审阅 · 已审阅节省 174.6h");
    await expect(page.locator(".finops-content")).toContainText(SHENZHEN_REFS.attempt);

    await page.getByRole("button", { name: "风险与优化", exact: true }).click();
    await expect(page.locator(".finops-content")).toContainText("深圳选址");
    await page.getByRole("button", { name: "查看证据" }).first().click();
    await expect(page.locator(".finops-content")).toContainText(SHENZHEN_REFS.request);
    await expect(page.locator(".finops-content")).toContainText(SHENZHEN_REFS.run);
    await expect(page.locator(".finops-drawer-backdrop")).toContainText("缓存判定未命中");
    await page.locator(".finops-drawer-backdrop").getByRole("button", { name: /关闭/ }).click();
    await page.getByRole("button", { name: "问 AI" }).first().click();
    const assistant = page.getByRole("dialog", { name: "运营指标 AI 助手" });
    await expect(assistant).toContainText("深圳选址的未计价请求需要补齐价格映射");
    await expect.poll(() => calls.some((item) => item.path === "/api/finops/assistant/query")).toBe(true);
    const submitted = JSON.parse(calls.filter((item) => item.path === "/api/finops/assistant/query").at(-1).body);
    expect(submitted.metric_context.evidence_refs).toContain(SHENZHEN_REFS.request);

    await page.getByRole("button", { name: "运行记录" }).last().click();
    await expect(page.getByRole("main")).toContainText("运行记录 · 可观测性");
    await page.getByRole("button", { name: "深圳选址 · 合成运行" }).click();
    const traceExplorer = page.getByTestId("run-trace-explorer");
    await expect(traceExplorer).toContainText("运行 Trace 详情");
    await expect(traceExplorer).toContainText("深圳选址 · 合成运行");
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
    await traceExplorer.getByLabel("Trace 事件列表").getByRole("button").nth(1).click();
    await expect(traceExplorer.getByLabel("模型与缓存证据")).toContainText("命中");
    await expect(traceExplorer.locator(".trace-json-panel")).toContainText(SHENZHEN_REFS.result);
    await page.getByRole("button", { name: "返回运行记录" }).click();
    await expect(page.getByRole("main")).toContainText("运行记录 · 可观测性");

    const requestDetail = await page.evaluate(async (requestRef) => (await fetch(`/api/finops/requests/${requestRef}`)).json(), SHENZHEN_REFS.request);
    expect(requestDetail.metrics.estimated_cost.official_price_key).toContain("gpt-5.6-terra");
    expect(requestDetail.metrics.provider_cache.evidence_state).toBe("synthetic");
    expect(requestDetail.technical_refs).toMatchObject({ request_ref: SHENZHEN_REFS.request, run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt });

    const trace = await page.evaluate(async (runId) => (await fetch(`/api/runs/${runId}/trace`)).json(), SHENZHEN_REFS.run);
    expect(trace[0].data).toMatchObject({ provenance: "synthetic_demo", request_ref: SHENZHEN_REFS.request, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt, route_evidence: "synthetic" });
    expect(trace[0].data.result_cache.state).toBe("miss");

    const catalog = await page.evaluate(async () => (await fetch("/api/finops/pricing/catalog")).json());
    const mappings = await page.evaluate(async () => (await fetch("/api/finops/pricing/mappings")).json());
    expect(catalog.items.map((item) => item.provider)).toEqual(expect.arrayContaining(["azure_foundry", "deepseek"]));
    expect(mappings.items[0]).toMatchObject({ official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", mapping_revision: 1 });

    const hitDetail = await page.evaluate(async () => (await fetch("/api/finops/requests/req_shenzhen_00000002")).json());
    expect(hitDetail.metrics.result_cache).toMatchObject({ state: "hit", source_result_version: SHENZHEN_REFS.result });
  });
}

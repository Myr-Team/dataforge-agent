import { expect, test } from "playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { installFinOpsMockApi } from "./finopsMockApi.mjs";

const RUN_ID = "run-trace-demo";

test("settings opens identity and access directly and completes group selection", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.route("**/api/identity-governance**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/identity-governance/groups") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ connected: true, permission_state: "granted", groups: [{ id: "group-private", display_name: "Finance Operations" }] }),
      });
      return;
    }
    if (url.pathname === "/api/identity-governance" && route.request().method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          mappings: [],
          mapping_count: 0,
          graph_connection: { state: "token_available", token_source: "delegated" },
          permissions: { "User.ReadBasic.All": "verification_required", "GroupMember.Read.All": "verification_required" },
          membership_resolution: { claims: "enabled", overage_fallback: "enabled", failure_mode: "explicit_membership_only" },
        }),
      });
      return;
    }
    if (url.pathname === "/api/identity-governance/group-mappings" && route.request().method() === "POST") {
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ mapping_id: "mapping-safe", display_name: "Finance Operations", role: "viewer", workspace_ids: ["demo-corpus"], revision: 1 }) });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("button", { name: "管理身份与访问" }).click();

  await expect(page.getByTestId("identity-access-page")).toBeVisible();
  await expect(page.getByText("身份信息暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("登录令牌可用，等待目录查询验证", { exact: true })).toBeVisible();
  await page.getByPlaceholder("搜索组名称，例如 Finance").fill("Finance");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("option", { name: /Finance Operations/ }).click();
  await expect(page.getByRole("button", { name: "建立组映射" })).toBeEnabled();
});

test("run records open a same-page trace explorer with readable non-wrapping JSON", async ({ page }) => {
  await installFinOpsMockApi(page);
  await page.route("**/api/workspaces/demo-corpus/dashboard", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      workspace_id: "demo-corpus",
      workspace: { workspace_id: "demo-corpus", name: "Commerce" },
      workspaces: [{ workspace_id: "demo-corpus", name: "Commerce" }],
      runs: [{ run_id: RUN_ID, title: "External Agent 分析", status: "completed", completed_at: "2026-08-11T08:00:00Z" }],
      conversations: [],
      health: { ok: true },
    }),
  }));
  await page.route(`**/api/runs/${RUN_ID}/summary`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ run_id: RUN_ID, status: "completed", title: "External Agent 分析", agent_count: 1, evidence: { trace: "run.steps" } }),
  }));
  await page.route(`**/api/runs/${RUN_ID}/trace`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify([{
      index: 0,
      time: "2026-08-11T08:00:00Z",
      event: "model_response",
      status: "completed",
      duration_ms: 842,
      detail: {
        agent_reference: { name: "customer-agent", type: "agent_reference" },
        model: "deepseek-v4-flash",
        deployment: "deepseek-v4-flash",
        provider_type: "deepseek",
        gateway_coverage: "app_observed",
        result_cache: { state: "miss", eligible: true, reason: "eligible", policy_revision: 3 },
        provider_cache: { state: "partial_hit", hit_tokens: 640, miss_tokens: 160, hit_rate_pct: 80, evidence_state: "observed" },
        result: { summary: "已完成外部 Agent 调用" },
      },
      source: "run_store.steps",
    }]),
  }));

  await page.goto("/");
  await page.getByRole("button", { name: "运行记录" }).click();
  await page.getByRole("button", { name: "Trace 详情" }).click();

  await expect(page.getByTestId("run-trace-explorer")).toBeVisible();
  await expect(page.getByText("External Agent", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".trace-json-panel pre")).toContainText("deepseek-v4-flash");
  await expect(page.locator(".trace-model-evidence")).toContainText("DeepSeek");
  await expect(page.locator(".trace-model-evidence")).toContainText("结果缓存未命中");
  await expect(page.locator(".trace-model-evidence")).toContainText("Token 缓存部分命中");
  await expect(page.locator(".trace-model-evidence")).toContainText("80%");
  await expect(page.locator(".trace-json-panel pre")).toHaveCSS("white-space", "pre");
  await expect(page.locator(".trace-json-panel pre")).toHaveCSS("color", "rgb(23, 32, 51)");
  const outputDir = path.resolve(process.cwd(), "..", "output", "playwright");
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, "run-trace-cache-evidence-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "返回运行记录" }).click();
  await expect(page.getByRole("heading", { name: "运行记录 · 可观测性" })).toBeVisible();
});

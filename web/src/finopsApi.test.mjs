import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildFinOpsQuery,
  closeFinOpsRemediationDraft,
  createFinOpsRemediationDraft,
  createIdentityGroupMapping,
  createModelProvider,
  deleteFinOpsOfficialPriceMapping,
  disableMemberBudget,
  disableIdentityGroupMapping,
  disableModelProvider,
  loadFinOpsBootstrap,
  loadFinOpsRemediationDraft,
  loadFinOpsRemediationDrafts,
  loadFinOpsRiskDecision,
  loadLatestFinOpsRiskScan,
  loadFinOpsRoiDecision,
  loadFinOpsOfficialPriceCatalog,
  loadFinOpsOfficialPriceMappings,
  loadMemberBudgetAlerts,
  loadMemberBudgetMembers,
  loadMemberBudgets,
  loadMemberBudgetNotification,
  loadIdentityGovernance,
  loadModelProviders,
  queryFinOpsAssistant,
  runFinOpsRiskScan,
  promoteFinOpsRemediationDraft,
  reviewFinOpsRemediationDraft,
  rotateModelProviderSecret,
  saveMemberBudget,
  saveMemberBudgetNotification,
  sendMemberBudgetTestEmail,
  searchIdentityGovernanceGroups,
  updateIdentityGroupMapping,
  updateFinOpsOfficialPriceMapping,
  updateWorkspaceModelRouting,
  toUserFacingRequestError,
} from "./api.js";


test("buildFinOpsQuery emits only supported non-empty filters", () => {
  const query = buildFinOpsQuery({
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-24T00:00:00Z",
    workspaceId: "ws-a",
    departmentId: "",
    agentId: "df-coordinator",
    actorRef: null,
    model: "gpt-5-mini",
    ignored: "must-not-pass",
  });
  const params = new URLSearchParams(query);

  assert.equal(params.get("workspace_id"), "ws-a");
  assert.equal(params.get("agent_id"), "df-coordinator");
  assert.equal(params.get("model"), "gpt-5-mini");
  assert.equal(params.has("department_id"), false);
  assert.equal(params.has("ignored"), false);
});


test("loadFinOpsBootstrap calls the bounded bootstrap endpoint", async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return {
      ok: true,
      json: async () => ({ overview: { metrics: { requests: 1 } } }),
    };
  };

  try {
    await loadFinOpsBootstrap({ workspaceId: "ws-a", from: "2026-07-01T00:00:00Z" });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.match(requestedUrl, /^\/api\/finops\/bootstrap\?/);
  assert.match(requestedUrl, /workspace_id=ws-a/);
});

test("decision loaders append only the bounded refresh query and preserve abort signals", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return { ok: true, json: async () => ({ decision: { state: "ready" } }) };
  };

  try {
    await loadFinOpsRoiDecision(
      { workspaceId: "ws-a", model: "gpt-safe", refresh: "must-not-pass" },
      { signal: controller.signal, refresh: true },
    );
    await loadFinOpsRiskDecision(
      { workspaceId: "ws-a" },
      { signal: controller.signal, refresh: false },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls[0].url, "/api/finops/roi/decision?workspace_id=ws-a&model=gpt-safe&refresh=1");
  assert.equal(calls[1].url, "/api/finops/risk/decision?workspace_id=ws-a");
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal("refresh" in calls[0].options, false);
  assert.deepEqual(Object.keys(calls[0].options.headers).sort(), ["Accept"]);
});

test("risk scan clients preserve the selected scope and send a strict read-only scan payload", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url: String(url),
      method: options.method || "GET",
      body: options.body ? JSON.parse(options.body) : null,
      signal: options.signal,
    });
    return { ok: true, json: async () => ({ status: "completed", findings: [] }) };
  };

  const filters = {
    workspaceId: "ws-a",
    from: "2026-07-01T00:00:00Z",
    to: "2026-08-01T00:00:00Z",
    departmentId: "Finance",
    agentId: "agent-a",
    model: "gpt-safe",
    ignored: "must-not-pass",
  };
  try {
    await loadLatestFinOpsRiskScan(filters, { signal: controller.signal });
    await runFinOpsRiskScan({ ...filters, actorRef: "actor-safe", execute: true }, { signal: controller.signal });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(
    calls[0].url,
    "/api/finops/risk/scans/latest?from=2026-07-01T00%3A00%3A00Z&to=2026-08-01T00%3A00%3A00Z&department_id=Finance&workspace_id=ws-a&agent_id=agent-a&model=gpt-safe",
  );
  assert.equal(calls[1].url, "/api/finops/risk/scans");
  assert.equal(calls[1].method, "POST");
  assert.deepEqual(calls[1].body, {
    workspace_id: "ws-a",
    from: "2026-07-01T00:00:00Z",
    to: "2026-08-01T00:00:00Z",
    department_id: "Finance",
    agent_id: "agent-a",
    actor_ref: "actor-safe",
    model: "gpt-safe",
  });
  assert.equal(calls[0].signal, controller.signal);
  assert.equal(calls[1].signal, controller.signal);
});

test("remediation clients use encoded endpoints and send only strict server payloads", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls = [];
  const requestOptions = [];
  globalThis.fetch = async (url, options = {}) => {
    requestOptions.push(options);
    calls.push({
      url: String(url),
      method: options.method || "GET",
      body: options.body ? JSON.parse(options.body) : null,
    });
    return { ok: true, json: async () => ({ draft: { revision: 2 } }) };
  };

  try {
    const safeOptions = {
      signal: controller.signal,
      refresh: true,
      headers: { "X-Must-Not-Pass": "secret-marker" },
      credentials: "include",
    };
    await loadFinOpsRemediationDrafts(
      { workspaceId: "ws/a", model: "must-not-pass" },
      safeOptions,
    );
    await loadFinOpsRemediationDraft("draft/a", safeOptions);
    await createFinOpsRemediationDraft({
      workspaceId: "ws/a",
      sourceOpportunityId: "opp/a",
      baseVersion: "cache-policy-v4",
      proposedChanges: [{ arbitrary: true }],
      tenantRef: "must-not-pass",
    }, safeOptions);
    await reviewFinOpsRemediationDraft("draft/a", {
      baseRevision: 1,
      reason: "reviewed",
      actorRef: "must-not-pass",
    }, safeOptions);
    await promoteFinOpsRemediationDraft("draft/a", {
      base_revision: 2,
      reason: "promote",
      execute: true,
    }, safeOptions);
    await closeFinOpsRemediationDraft("draft/a", {
      baseRevision: 3,
      ignored: "must-not-pass",
    }, safeOptions);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls, [
    { url: "/api/finops/remediation-drafts?workspace_id=ws%2Fa&refresh=1", method: "GET", body: null },
    { url: "/api/finops/remediation-drafts/draft%2Fa?refresh=1", method: "GET", body: null },
    {
      url: "/api/finops/remediation-drafts?refresh=1",
      method: "POST",
      body: {
        workspace_id: "ws/a",
        source_opportunity_id: "opp/a",
        base_version: "cache-policy-v4",
      },
    },
    {
      url: "/api/finops/remediation-drafts/draft%2Fa/review?refresh=1",
      method: "POST",
      body: { base_revision: 1, reason: "reviewed" },
    },
    {
      url: "/api/finops/remediation-drafts/draft%2Fa/promote?refresh=1",
      method: "POST",
      body: { base_revision: 2, reason: "promote" },
    },
    {
      url: "/api/finops/remediation-drafts/draft%2Fa/close?refresh=1",
      method: "POST",
      body: { base_revision: 3 },
    },
  ]);
  for (const options of requestOptions) {
    assert.equal(options.signal, controller.signal);
    assert.equal("refresh" in options, false);
    assert.equal("credentials" in options, false);
    assert.equal(options.headers["X-Must-Not-Pass"], undefined);
  }
});

test("remediation clients preserve typed HTTP errors from request", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 409,
    statusText: "Conflict",
    json: async () => ({ detail: "remediation revision conflict" }),
  });

  try {
    await assert.rejects(
      reviewFinOpsRemediationDraft("draft-a", { baseRevision: 1 }),
      (error) => error.status === 409 && error.message === "remediation revision conflict",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("network failure reports an expired login only after an auth probe", async () => {
  const error = await toUserFacingRequestError(
    new TypeError("Failed to fetch"),
    async () => ({ authenticated: false }),
  );

  assert.equal(error.message, "登录已失效，请刷新后重新登录");
  assert.equal(error.code, "auth_session_expired");
});

test("network failure remains a service message when auth is still valid", async () => {
  const error = await toUserFacingRequestError(
    new TypeError("Failed to fetch"),
    async () => ({ authenticated: true }),
  );

  assert.equal(error.message, "暂时无法连接服务，请稍后重试");
  assert.equal(error.code, "service_unreachable");
});

test("metric-aware assistant sends only the typed request body", async () => {
  const originalFetch = globalThis.fetch;
  let captured = null;
  globalThis.fetch = async (url, options) => {
    captured = { url: String(url), options };
    return {
      ok: true,
      json: async () => ({
        status: "ready",
        answer: "已按当前指标分析。",
        evidence_refs: ["req_safe"],
        evidence_state: "observed",
        suggested_questions: [],
      }),
    };
  };

  try {
    await queryFinOpsAssistant({
      question: "为什么变化？",
      metric_context: {
        metric_id: "estimated_cost",
        label: "估算成本",
        value: 0.01,
        unit: "USD",
        window: { from: "2026-07-01T00:00:00Z", to: "2026-07-26T00:00:00Z" },
        filters: { workspace_id: "ws-a" },
        data_status: "partial",
        evidence_state: "estimated",
      },
      history: [],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(captured.url, "/api/finops/assistant/query");
  assert.equal(captured.options.method, "POST");
  assert.equal(JSON.parse(captured.options.body).metric_context.metric_id, "estimated_cost");
});


test("planning APIs use bounded native endpoints", async () => {
  const source = await readFile(new URL("./api.js", import.meta.url), "utf8");

  assert.match(source, /loadFinOpsBudgets/);
  assert.match(source, /loadFinOpsSavedViews/);
  assert.match(source, /createFinOpsSavedView/);
  assert.match(source, /finops\/export\.csv/);
});

test("official pricing APIs use the server-owned catalog and typed mapping body", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return { ok: true, json: async () => ({ items: [] }) };
  };

  try {
    await loadFinOpsOfficialPriceCatalog();
    await loadFinOpsOfficialPriceMappings();
    await updateFinOpsOfficialPriceMapping("gpt-5.6-terra", {
      officialPriceKey: "azure-openai:gpt-5.1:global-standard:global",
      baseRevision: 2,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls.map((item) => item.url), [
    "/api/finops/pricing/catalog",
    "/api/finops/pricing/mappings",
    "/api/finops/pricing/mappings/gpt-5.6-terra",
  ]);
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    official_price_key: "azure-openai:gpt-5.1:global-standard:global",
    base_revision: 2,
  });
});

test("deleting a wrong mapping issues a DELETE and tolerates a 204 body", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method });
    return { ok: true, status: 204 };
  };

  let result;
  try {
    result = await deleteFinOpsOfficialPriceMapping("gpt-5.6-terra");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls[0].url, "/api/finops/pricing/mappings/gpt-5.6-terra");
  assert.equal(calls[0].method, "DELETE");
  assert.deepEqual(result, {});
});

test("model routing save carries base_revision for optimistic concurrency", async () => {
  const originalFetch = globalThis.fetch;
  let captured = null;
  globalThis.fetch = async (url, options = {}) => {
    captured = { url: String(url), options };
    return { ok: true, json: async () => ({ policy: { revision: 4 } }) };
  };

  try {
    await updateWorkspaceModelRouting("ws-a", {
      assignments: {},
      agent_assignments: {},
      base_revision: 3,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(captured.url, "/api/workspaces/ws-a/governance/model-routing");
  assert.equal(captured.options.method, "PUT");
  assert.equal(JSON.parse(captured.options.body).base_revision, 3);
});

test("member budget queries use bounded organization endpoints", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return { ok: true, json: async () => ({ items: [] }) };
  };

  try {
    await loadMemberBudgets("ws-a");
    await loadMemberBudgetMembers("ws-a");
    await loadMemberBudgetNotification("ws-a");
    await loadMemberBudgetAlerts("ws-a");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls, [
    "/api/finops/member-budgets?workspace_id=ws-a&limit=100",
    "/api/finops/member-budget-members?workspace_id=ws-a&limit=100",
    "/api/finops/notification-settings?workspace_id=ws-a",
    "/api/finops/budget-alerts?workspace_id=ws-a&limit=50",
  ]);
});

test("member budget writes send only typed fields and base revision", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method, body: JSON.parse(options.body || "{}") });
    return { ok: true, json: async () => ({ item: { revision: 4 } }) };
  };

  try {
    await saveMemberBudget({
      workspaceId: "ws-a",
      budgetId: "budget/a",
      memberRef: "member-safe",
      amountUsd: 200.5,
      thresholdsPct: [80, 95, 100],
      enabled: true,
      baseRevision: 3,
      ignored: "must-not-pass",
    });
    await saveMemberBudgetNotification({
      workspaceId: "ws-a",
      recipientEmail: "demo-admin@example.test",
      senderDisplayName: "DataForge",
      subjectTemplate: "{{member_name}} 预算提醒",
      bodyTemplate: "{{estimated_spend}} / {{budget_amount}}",
      enabled: true,
      baseRevision: 2,
    });
    await sendMemberBudgetTestEmail("ws-a");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls[0], {
    url: "/api/finops/member-budgets/budget%2Fa?workspace_id=ws-a",
    method: "PATCH",
    body: {
      amount_usd: 200.5,
      thresholds_pct: [80, 95, 100],
      enabled: true,
      base_revision: 3,
    },
  });
  assert.deepEqual(calls[1], {
    url: "/api/finops/notification-settings?workspace_id=ws-a",
    method: "PUT",
    body: {
      recipient_email: "demo-admin@example.test",
      sender_display_name: "DataForge",
      subject_template: "{{member_name}} 预算提醒",
      body_template: "{{estimated_spend}} / {{budget_amount}}",
      enabled: true,
      base_revision: 2,
    },
  });
  assert.deepEqual(calls[2], {
    url: "/api/finops/notification-settings/test-email?workspace_id=ws-a",
    method: "POST",
    body: {},
  });
});

test("disabling a member budget uses the typed revisioned endpoint", async () => {
  const originalFetch = globalThis.fetch;
  let captured = null;
  globalThis.fetch = async (url, options = {}) => {
    captured = { url: String(url), method: options.method, body: JSON.parse(options.body || "{}") };
    return { ok: true, json: async () => ({ item: { enabled: false, revision: 4 } }) };
  };

  try {
    await disableMemberBudget("ws-a", "budget/a", 3);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(captured, {
    url: "/api/finops/member-budgets/budget%2Fa/disable?workspace_id=ws-a",
    method: "POST",
    body: { base_revision: 3 },
  });
});

test("provider management uses typed endpoints and revisioned writes", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return { ok: true, json: async () => ({ items: [] }) };
  };
  try {
    await loadModelProviders();
    await createModelProvider({
      provider_type: "deepseek",
      display_name: "DeepSeek",
      base_url: "https://api.deepseek.com",
      api_key: "test-key-marker",
    });
    await rotateModelProviderSecret("provider/a", "rotated-key-marker", 4);
    await disableModelProvider("provider/a", 5);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls.map((item) => item.url), [
    "/api/model-providers",
    "/api/model-providers",
    "/api/model-providers/provider%2Fa/rotate-secret",
    "/api/model-providers/provider%2Fa/disable",
  ]);
  assert.equal(JSON.parse(calls[2].options.body).base_revision, 4);
  assert.equal(JSON.parse(calls[3].options.body).base_revision, 5);
});

test("Bedrock create sends credentials once", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (_url, options) => {
    captured = JSON.parse(options.body);
    return new Response(JSON.stringify({ provider_id: "provider_bedrock" }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  };

  try {
    await createModelProvider({
      provider_type: "aws_bedrock",
      display_name: "AWS Bedrock",
      region: "ap-southeast-1",
      access_key_id: "AKIAEXAMPLE",
      secret_access_key: "secret-marker-value",
      session_token: null,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(captured.region, "ap-southeast-1");
  assert.equal(captured.secret_access_key, "secret-marker-value");
});

test("Bedrock rotate sends only the credential bundle and revision", async () => {
  const originalFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (_url, options) => {
    captured = JSON.parse(options.body);
    return new Response(JSON.stringify({ provider_id: "provider_bedrock" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  try {
    await rotateModelProviderSecret("provider_bedrock", {
      provider_type: "aws_bedrock",
      display_name: "must-not-send",
      region: "must-not-send",
      access_key_id: "rotate-key",
      secret_access_key: "rotate-secret-marker",
      session_token: null,
    }, 7);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(captured, {
    provider_type: "aws_bedrock",
    access_key_id: "rotate-key",
    secret_access_key: "rotate-secret-marker",
    session_token: null,
    base_revision: 7,
  });
});

test("identity governance encodes search and uses revisioned mapping actions", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return { ok: true, json: async () => ({ mappings: [], groups: [] }) };
  };
  try {
    await loadIdentityGovernance();
    await searchIdentityGovernanceGroups("Finance & Ops", 8);
    await createIdentityGroupMapping({
      group_id: "group-marker",
      display_name: "Finance",
      role: "viewer",
      workspace_ids: ["ws-a"],
      priority: 100,
    });
    await updateIdentityGroupMapping("mapping/a", { base_revision: 2, role: "editor" });
    await disableIdentityGroupMapping("mapping/a", 3);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls[0].url, "/api/identity-governance");
  assert.equal(calls[1].url, "/api/identity-governance/groups?query=Finance+%26+Ops&limit=8");
  assert.equal(calls[3].url, "/api/identity-governance/group-mappings/mapping%2Fa");
  assert.equal(JSON.parse(calls[3].options.body).base_revision, 2);
  assert.equal(JSON.parse(calls[4].options.body).base_revision, 3);
});

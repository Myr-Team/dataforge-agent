import assert from "node:assert/strict";
import test from "node:test";

import {
  MODEL_AGENT_ROLES,
  MODEL_EXECUTION_KINDS,
  modelRoutingViewModel,
  officialPricePresentation,
} from "./modelRoutingViewModel.js";

test("model routing view exposes allowlisted routes and owner-managed price-card state", () => {
  const view = modelRoutingViewModel({
    default_route: "sol",
    routes: [
      {
        id: "sol",
        deployment: "gpt-5.6-sol",
        model_id: "gpt-5.6-sol",
        provider_id: "azure-foundry",
        provider_type: "azure_foundry",
        label: "GPT-5.6 Sol",
        capabilities: ["chat", "analysis"],
      },
      { id: "luna", deployment: "gpt-5.6-luna", label: "GPT-5.6 Luna", capabilities: ["chat"] },
    ],
    policy: {
      revision: 2,
      assignments: {
        direct_reply: { primary_route_id: "luna", fallback_route_id: "sol" },
      },
      agent_assignments: {
        "df-auditor": { primary_route_id: "sol", fallback_route_id: null },
        "df-finops-analyst": { primary_route_id: "sol", fallback_route_id: "sol" },
        "df-roi-analyst": { primary_route_id: "sol", fallback_route_id: "sol" },
      },
    },
    price_card: {
      state: "configured",
      revision: 4,
      currency: "USD",
      configured_route_ids: ["sol"],
    },
  });

  assert.equal(view.defaultRouteId, "sol");
  assert.equal(view.assignments.direct_reply.primaryRouteId, "luna");
  assert.equal(view.assignments.full_analysis.primaryRouteId, "");
  assert.equal(view.agentAssignments["df-auditor"].primaryRouteId, "sol");
  assert.equal(view.agentAssignments["df-finops-analyst"].primaryRouteId, "sol");
  assert.equal(view.agentAssignments["df-roi-analyst"].fallbackRouteId, "sol");
  assert.equal(view.agentAssignments["df-producer"].primaryRouteId, "");
  assert.equal(view.routes[0].id, "sol");
  assert.equal(view.routes[0].deployment, "gpt-5.6-sol");
  assert.equal(view.routes[0].providerLabel, "Azure Foundry");
  assert.equal(view.routes[0].modelId, "gpt-5.6-sol");
  assert.equal(view.priceCard.statusLabel, "已配置估算参考");
  assert.equal(view.priceCard.configuredRouteCount, 1);
});

test("model settings expose the stable DataForge and operations analyst roles", () => {
  assert.deepEqual(MODEL_AGENT_ROLES.map((item) => item.id), [
    "df-coordinator",
    "df-corpus-analyst",
    "df-market-researcher",
    "df-feasibility-analyst",
    "df-auditor",
    "df-producer",
    "df-finops-analyst",
    "df-roi-analyst",
  ]);
});

test("configured follow-up uses the stable chat capability", () => {
  const followUp = MODEL_EXECUTION_KINDS.find((item) => item.id === "follow_up");
  assert.equal(followUp?.capability, "chat");
});

test("dynamic provider routes preserve server-owned availability and labels", () => {
  const view = modelRoutingViewModel({
    routes: [{
      id: "ds_primary_flash",
      deployment: "deepseek-v4-flash",
      model_id: "deepseek-v4-flash",
      provider_id: "provider_primary",
      provider_type: "deepseek",
      provider_label: "DeepSeek 原厂",
      label: "DeepSeek V4 Flash",
      capabilities: ["chat", "analysis"],
      official_price_key: "deepseek:deepseek-v4-flash:official",
      pricing_state: "priced",
      health_state: "connected",
      governance_state: "pending",
      selectable: false,
      unavailable_reason: "governance_required",
    }],
  });

  assert.equal(view.routes[0].providerLabel, "DeepSeek 原厂");
  assert.equal(view.routes[0].selectable, false);
  assert.equal(view.routes[0].unavailableReason, "governance_required");
  assert.equal(view.routes[0].unavailableLabel, "需先纳入模型路由");
  assert.equal(view.routes[0].officialPriceKey, "deepseek:deepseek-v4-flash:official");
});

test("DeepSeek official price presentation shows cache hit, miss and output rates", () => {
  const price = officialPricePresentation({
    provider: "deepseek",
    display_name: "DeepSeek V4 Flash",
    currency: "USD",
    cached_input_per_million: "0.0028",
    input_per_million: "0.14",
    output_per_million: "0.28",
    revision: "deepseek-2026-07-28-v1",
    source_url: "https://api-docs.deepseek.com/quick_start/pricing/",
  });

  assert.equal(price.label, "DeepSeek V4 Flash · 缓存命中 $0.0028 / 未命中 $0.14 / 输出 $0.28");
  assert.deepEqual(price.rates, [
    { label: "缓存命中", value: "$0.0028", unit: "/ 百万 Token" },
    { label: "缓存未命中", value: "$0.14", unit: "/ 百万 Token" },
    { label: "输出", value: "$0.28", unit: "/ 百万 Token" },
  ]);
  assert.equal(price.revision, "deepseek-2026-07-28-v1");
});

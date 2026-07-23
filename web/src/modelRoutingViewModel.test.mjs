import assert from "node:assert/strict";
import test from "node:test";

import { MODEL_EXECUTION_KINDS, modelRoutingViewModel } from "./modelRoutingViewModel.js";

test("model routing view exposes allowlisted routes and owner-managed price-card state", () => {
  const view = modelRoutingViewModel({
    default_route: "sol",
    routes: [
      { id: "sol", label: "GPT-5.6 Sol", capabilities: ["chat", "analysis"] },
      { id: "luna", label: "GPT-5.6 Luna", capabilities: ["chat"] },
    ],
    policy: {
      revision: 2,
      assignments: {
        direct_reply: { primary_route_id: "luna", fallback_route_id: "sol" },
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
  assert.equal(view.routes[0].id, "sol");
  assert.equal(view.priceCard.statusLabel, "已配置估算参考");
  assert.equal(view.priceCard.configuredRouteCount, 1);
});

test("configured follow-up uses the stable chat capability", () => {
  const followUp = MODEL_EXECUTION_KINDS.find((item) => item.id === "follow_up");
  assert.equal(followUp?.capability, "chat");
});

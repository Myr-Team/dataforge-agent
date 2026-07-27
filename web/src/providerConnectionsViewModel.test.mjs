import assert from "node:assert/strict";
import test from "node:test";

import { providerConnectionsViewModel } from "./providerConnectionsViewModel.js";

test("provider connections expose friendly safe state without secret material", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_safe",
      provider_type: "deepseek",
      display_name: "DeepSeek 原厂",
      base_url: "https://api.deepseek.com",
      connection_state: "connected",
      governance_state: "governed",
      secret_status: "stored",
      revision: 3,
      available_models: [{
        model_id: "deepseek-v4-flash",
        display_name: "DeepSeek V4 Flash",
        capabilities: ["chat", "analysis"],
        support_state: "supported",
        price_key: "deepseek:deepseek-v4-flash:official",
      }],
    }],
  });

  assert.equal(view.items[0].name, "DeepSeek 原厂");
  assert.equal(view.items[0].connectionLabel, "已连接");
  assert.equal(view.items[0].models[0].pricingLabel, "已计价");
  assert.equal(view.items[0].canAssign, true);
  assert.equal(JSON.stringify(view).includes("api_key"), false);
});

test("degraded provider remains visible but cannot be assigned", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_degraded",
      provider_type: "deepseek",
      display_name: "DeepSeek",
      connection_state: "degraded",
      governance_state: "degraded",
      revision: 1,
      available_models: [],
    }],
  });

  assert.equal(view.items[0].canAssign, false);
  assert.equal(view.summary.actionRequired, 1);
});

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

test("missing DeepSeek credential asks for re-entry and disables test", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_missing",
      provider_type: "deepseek",
      display_name: "DeepSeek",
      connection_state: "invalid",
      governance_state: "pending",
      secret_status: "missing",
      connection_stage: "secret_read",
      stage_durations_ms: { secret_read: 3 },
      safe_error_category: "provider_secret_missing",
      revision: 1,
      available_models: [],
    }],
  });
  const item = view.items[0];

  assert.equal(item.credentialLabel, "需要重新录入 Key");
  assert.equal(item.canTest, false);
  assert.equal(item.primaryAction, "rotate_secret");
  assert.equal(item.stageLabel, "读取安全凭据");
  assert.equal(item.safeErrorLabel, "需要重新录入 Key。保存后将重新检测连接。");
});

test("connected DeepSeek exposes staged connection facts without raw errors", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_ready",
      provider_type: "deepseek",
      display_name: "DeepSeek",
      connection_state: "connected",
      governance_state: "governed",
      secret_status: "stored",
      connection_stage: "completed",
      safe_error_category: "provider_unavailable",
      stage_durations_ms: {
        secret_read: 2,
        endpoint_resolution: 4,
        tls_connect: 7,
        provider_auth: 12,
        minimal_inference: 90,
        model_discovery: 16,
      },
      revision: 2,
      available_models: [],
    }],
  });
  const item = view.items[0];

  assert.equal(item.credentialLabel, "已安全保存");
  assert.equal(item.canTest, true);
  assert.equal(item.primaryAction, "test");
  assert.equal(item.stageLabel, "全部检测完成");
  assert.equal(item.totalDurationLabel, "131 ms");
  assert.equal(item.safeErrorLabel, "");
});

test("Bedrock discovery is connected but never assignable", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_bedrock",
      provider_type: "aws_bedrock",
      display_name: "AWS Bedrock",
      region: "ap-southeast-1",
      base_url: "https://bedrock.ap-southeast-1.amazonaws.com",
      connection_state: "connected",
      governance_state: "unmanaged",
      secret_status: "stored",
      revision: 1,
      available_models: [{
        model_id: "anthropic.claude-sonnet-4-20250514-v1:0",
        display_name: "Claude Sonnet 4",
        capabilities: ["text", "streaming"],
        support_state: "unsupported",
        price_key: null,
      }],
    }],
  });

  assert.equal(view.items[0].providerLabel, "AWS Bedrock");
  assert.equal(view.items[0].region, "ap-southeast-1");
  assert.equal(view.items[0].canAssign, false);
});

test("Bedrock remains outside Agent assignment and never exposes unknown error categories", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_bedrock_hostile",
      provider_type: "aws_bedrock",
      display_name: "AWS Bedrock",
      connection_state: "connected",
      governance_state: "governed",
      safe_error_category: "untrusted-marker-category",
      revision: 1,
      available_models: [{
        model_id: "supported-model",
        support_state: "supported",
        price_key: "must-not-matter",
      }],
    }],
  });

  assert.equal(view.items[0].canAssign, false);
  assert.equal(view.items[0].safeErrorLabel, "连接状态异常，请检查配置后重试。");
  assert.equal(JSON.stringify(view).includes("untrusted-marker-category"), false);
});

test("verified pending DeepSeek exposes an explicit audited governance action", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_pending",
      provider_type: "deepseek",
      display_name: "DeepSeek 原厂",
      connection_state: "connected",
      governance_state: "pending",
      secret_status: "stored",
      revision: 4,
      route_eligibility: {
        state: "governance_required",
        selectable: false,
        can_govern: true,
        reason: "governance_required",
        eligible_model_count: 2,
      },
      available_models: [{
        model_id: "deepseek-v4-flash",
        display_name: "DeepSeek V4 Flash",
        support_state: "supported",
        price_key: "deepseek:deepseek-v4-flash:official",
      }],
    }],
  });
  const item = view.items[0];

  assert.equal(item.routeSelectable, false);
  assert.equal(item.canGovern, true);
  assert.equal(item.governanceAction, "govern");
  assert.equal(item.routeReasonLabel, "连接与计价已就绪，可纳入 Agent 模型路由。");
  assert.deepEqual(item.lifecycle.map((step) => step.state), ["complete", "complete", "complete", "current"]);
});

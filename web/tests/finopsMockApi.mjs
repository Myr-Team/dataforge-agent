const NOW = "2026-07-24T06:00:00Z";

export const bootstrapPayload = {
  scope: { workspace_ids: ["demo-corpus"], workspace_count: 1 },
  window: {
    from: "2026-06-24T00:00:00Z",
    to: "2026-07-24T23:59:59Z",
    timezone: "UTC",
  },
  freshness: {
    generated_at: NOW,
    sources: ["dataforge_application", "apim"],
    refresh_after_seconds: 60,
  },
  coverage: {
    observed_requests: 60,
    apim_governed_requests: 58,
    apim_coverage_pct: 96.67,
  },
  currency: "USD",
  data_status: "partial",
  overview: {
    freshness: { generated_at: NOW },
    data_status: "partial",
    trust: {
      pricing: {
        priced_requests: 58,
        unpriced_requests: 2,
        coverage_pct: 96.67,
        state: "partial",
      },
      tokens: {
        known_requests: 60,
        unknown_requests: 0,
        coverage_pct: 100,
        state: "complete",
      },
      apim: {
        app_observed_requests: 60,
        apim_governed_requests: 58,
        unmatched_metric_records: 4,
        coverage_pct: 96.67,
        state: "reconciliation_pending",
        gateway_unmatched: {
          scope: "unattributed",
          window: { from: "2026-06-24T00:00:00Z", to: "2026-07-24T23:59:59Z" },
          linked_requests: 58,
          unmatched_gateway_errors: {
            total: 4,
            client_error_4xx: 3,
            server_error_5xx: 1,
          },
          data_source: "apim_gateway_logs",
          updated_at: "2026-07-24T05:58:00Z",
          note: "网关侧未关联到任何应用运行的 4xx/5xx 聚合证据；无法可靠归属租户或工作区，按 unattributed/system 范围统计，不计入请求账本、错误率或成本。",
        },
      },
    },
    metrics: {
      requests: 60,
      tokens: {
        input: 1750,
        output: 430,
        cached_input: 232,
        reasoning: 75,
        total: 2487,
        known_requests: 60,
        unknown_requests: 0,
      },
      estimated_cost: {
        amount: 0.0269,
        priced_requests: 58,
        unpriced_requests: 2,
        status: "partial",
      },
      budget: {
        amount: 0.1,
        used_amount: 0.0269,
        usage_pct: 26.9,
        status: "partial",
        source: "daily_cost_budget",
      },
      latency: { p50_ms: 1200, p95_ms: 2100, known_requests: 60 },
      error_rate_pct: 6.7,
      success_rate_pct: 93.3,
      cache_hit_rate_pct: 42,
      cache: {
        eligible_requests: 50,
        hit: 21,
        miss: 29,
        bypassed: 8,
        unavailable: 2,
      },
      apim_coverage_pct: 96.67,
    },
  },
  trend: {
    bucket: "day",
    items: [
      {
        bucket: "2026-07-22T00:00:00Z",
        requests: 18,
        tokens: {
          input: 500,
          output: 120,
          cached_input: 80,
          reasoning: 30,
          total: 650,
        },
        estimated_cost: 0.007,
        p95_latency_ms: 1800,
        data_status: "available",
      },
      {
        bucket: "2026-07-23T00:00:00Z",
        requests: 22,
        tokens: {
          input: 690,
          output: 170,
          cached_input: 100,
          reasoning: 45,
          total: 905,
        },
        estimated_cost: 0.0102,
        p95_latency_ms: 2300,
        data_status: "partial",
      },
      {
        bucket: "2026-07-24T00:00:00Z",
        requests: 20,
        tokens: {
          input: 560,
          output: 140,
          cached_input: 52,
          reasoning: null,
          total: 752,
        },
        estimated_cost: 0.0097,
        p95_latency_ms: 2100,
        data_status: "partial",
      },
    ],
  },
  departments: {
    items: [
      {
        key: "Commerce",
        requests: 32,
        tokens: 1240,
        estimated_cost: 0.014,
        error_rate_pct: 3.1,
        p95_latency_ms: 1800,
        data_status: "available",
      },
      {
        key: "Finance",
        requests: 28,
        tokens: 1067,
        estimated_cost: 0.0129,
        error_rate_pct: 10.7,
        p95_latency_ms: 2400,
        data_status: "partial",
      },
    ],
  },
  anomalies: {
    count: 1,
    items: [
      {
        policy_type: "p95_latency",
        title: "响应时间偏高",
        severity: "warning",
        status: "open",
        observed_value: 2100,
        threshold_value: 2000,
        sample_count: 60,
        observed_at: NOW,
        evidence_state: "observed",
      },
    ],
  },
  insights: {
    finops: {
      insight_id: "ins_finops_ready",
      agent_kind: "finops",
      status: "ready",
      title: "成本变化来自主分析流程",
      summary: "主分析流程仍是主要成本驱动，缓存命中存在提升空间。",
      findings: [
        {
          kind: "cost_driver",
          statement: "Commerce 工作区贡献本窗口主要估算成本。",
          evidence_count: 1,
        },
      ],
      evidence_state: "estimated",
      confidence: 0.82,
      generated_at: "2026-07-24T05:55:00Z",
      evidence_gaps: [],
      draft_suggestions: [
        {
          action_type: "cache_policy",
          reason: "重复分析请求适合评估缓存。",
          payload: {
            workspace_id: "demo-corpus",
            enabled: true,
            ttl_seconds: 300,
            base_version: "v1",
          },
        },
      ],
    },
    roi: {
      insight_id: "ins_roi_gap",
      agent_kind: "roi",
      status: "insufficient_data",
      title: "ROI 证据不足",
      summary: "当前证据不足，暂不生成推测性结论。",
      findings: [],
      evidence_gaps: ["已验证结果事件不足"],
      evidence_state: "unavailable",
      confidence: null,
      generated_at: "2026-07-24T05:50:00Z",
      draft_suggestions: [],
    },
  },
  filters: {
    departments: ["Commerce", "Finance"],
    agents: ["df-coordinator"],
    models: ["gpt-5-mini"],
  },
};


export async function installFinOpsMockApi(page, calls = [], options = {}) {
  const control = {
    failBootstrap: Boolean(options.failBootstrap),
    bedrockConnectionState: options.bedrockConnectionState || "connected",
    providerItems: Array.isArray(options.providerItems) ? [...options.providerItems] : [],
    memberBudgetFailure: Boolean(options.memberBudgetFailure),
    memberBudgetEmpty: Boolean(options.memberBudgetEmpty),
    memberBudgetConflictOnce: Boolean(options.memberBudgetConflictOnce),
    memberBudgetEmailState: options.memberBudgetEmailState || "sent",
    memberBudgetActiveDisabled: Boolean(options.memberBudgetActiveDisabled),
    memberBudgetNotificationState: options.memberBudgetNotificationState || "configured",
    memberBudgetAlertsState: options.memberBudgetAlertsState || "available",
    memberBudgetDisabled: false,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    calls.push({ method: request.method(), path, body: request.postData() || "" });
    let body = {};
    let status = 200;

    if (path === "/api/finops/bootstrap" && control.failBootstrap) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "FinOps evidence service is unavailable" }),
      });
      return;
    }

    if (path === "/api/workspaces/demo-corpus/access") {
      body = { allowed: true, role: "owner", workspace_id: "demo-corpus" };
    } else if (path === "/api/workspaces/demo-corpus/governance/capabilities") {
      body = {
        workspace_id: "demo-corpus",
        sections: {
          finops: {
            visible: true,
            permissions: {
              "finops.summary.read": true,
              "finops.cost.read": true,
              "finops.roi.read": true,
              "finops.request_detail.read": true,
              "finops.trace.read": true,
              "finops.action.draft": true,
            },
          },
        },
      };
    } else if (path === "/api/workspaces/demo-corpus/dashboard") {
      body = {
        workspace_id: "demo-corpus",
        workspace: { workspace_id: "demo-corpus", name: "Commerce" },
        workspaces: [{ workspace_id: "demo-corpus", name: "Commerce" }],
        runs: [],
        conversations: [],
        health: { ok: true },
      };
    } else if (path === "/api/workspaces/demo-corpus/tasks") {
      body = { tasks: [] };
    } else if (path === "/api/workspaces/demo-corpus/latest-analysis") {
      body = { artifact: null };
    } else if (path === "/api/observability") {
      body = {};
    } else if (path === "/api/finops/bootstrap") {
      body = bootstrapPayload;
    } else if (path === "/api/finops/member-budgets" && request.method() === "GET") {
      if (control.memberBudgetFailure) {
        status = 503;
        body = { detail: "internal-sql-body-must-not-surface" };
      } else {
        body = {
          items: control.memberBudgetEmpty ? [] : [
            {
              budget_id: "budget-safe",
              member_ref: "member-safe",
              amount_usd: 200,
              thresholds_pct: [80, 95, 100],
              enabled: !control.memberBudgetDisabled,
              revision: 3,
              member: {
                member_ref: "member-safe",
                display_name: "Finance Admin",
                role: "admin",
                identity_state: "active",
                workspace_ids: ["demo-corpus"],
                department_labels: ["Finance"],
              },
              progress: {
                estimated_spend_usd: 190,
                priced_requests: 18,
                total_requests: 20,
                unpriced_requests: 2,
                pricing_coverage_pct: 90,
                primary_model: "gpt-5.6-terra",
              },
              data_status: "partial",
              currency: "USD",
            },
            {
              budget_id: "budget-former",
              member_ref: "member-former",
              amount_usd: 200.5,
              thresholds_pct: [80, 95, 100],
              enabled: false,
              revision: 2,
              member: {
                member_ref: "member-former",
                display_name: "Former member",
                role: "viewer",
                identity_state: control.memberBudgetActiveDisabled ? "active" : "inactive",
                workspace_ids: control.memberBudgetActiveDisabled ? ["demo-corpus"] : [],
                department_labels: control.memberBudgetActiveDisabled ? ["IT"] : [],
              },
              progress: {
                estimated_spend_usd: 0,
                priced_requests: 1,
                total_requests: 1,
                unpriced_requests: 0,
                pricing_coverage_pct: 100,
                primary_model: null,
              },
              data_status: "complete",
              currency: "USD",
            },
          ],
          cursor: { next: null, limit: 100 },
          freshness: "recorded",
          coverage: "request_estimated_cost",
          data_status: control.memberBudgetEmpty ? "complete" : "partial",
          currency: "USD",
        };
      }
    } else if (path === "/api/finops/member-budget-members" && request.method() === "GET") {
      body = {
        items: [
          {
            member_ref: "member-safe",
            display_name: "Finance Admin",
            role: "admin",
            identity_state: "active",
            workspace_ids: ["demo-corpus"],
            department_labels: ["Finance"],
          },
          {
            member_ref: "member-operator",
            display_name: "IT Operator",
            role: "viewer",
            identity_state: "active",
            workspace_ids: ["demo-corpus"],
            department_labels: ["IT"],
          },
          ...(control.memberBudgetActiveDisabled ? [{
            member_ref: "member-former",
            display_name: "Former member",
            role: "viewer",
            identity_state: "active",
            workspace_ids: ["demo-corpus"],
            department_labels: ["IT"],
          }] : []),
        ],
        cursor: { next: null, limit: 100 },
        data_status: "complete",
      };
    } else if (path === "/api/finops/notification-settings" && request.method() === "GET") {
      if (control.memberBudgetNotificationState === "not_configured") {
        status = 404;
        body = { detail: "Not found" };
      } else if (control.memberBudgetNotificationState === "unavailable") {
        status = 503;
        body = { detail: "internal-notification-body-must-not-surface" };
      } else {
        body = {
          item: {
            recipient_actor_ref: "member-safe",
            sender_display_name: "DataForge",
            subject_template: "{{member_name}} 预算提醒",
            body_template: "{{estimated_spend}} / {{budget_amount}}",
            enabled: true,
            revision: 2,
          },
          freshness: "recorded",
          coverage: "request_estimated_cost",
          data_status: "complete",
          currency: "USD",
        };
      }
    } else if (path === "/api/finops/notification-settings" && request.method() === "PUT") {
      control.memberBudgetNotificationState = "configured";
      body = {
        item: {
          recipient_actor_ref: "member-safe",
          sender_display_name: "DataForge",
          subject_template: "{{member_name}} 预算提醒",
          body_template: "{{estimated_spend}} / {{budget_amount}}",
          enabled: true,
          revision: 3,
        },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "complete",
        currency: "USD",
      };
    } else if (path === "/api/finops/notification-settings/test-email" && request.method() === "POST") {
      body = control.memberBudgetEmailState === "sent"
        ? { state: "sent", sent_at: NOW, safe_error_category: null }
        : {
          state: "failed",
          sent_at: null,
          safe_error_category: control.memberBudgetEmailState,
          operation_id: "must-not-surface",
        };
    } else if (path === "/api/finops/budget-alerts" && request.method() === "GET") {
      body = {
        items: control.memberBudgetEmpty || control.memberBudgetAlertsState === "unavailable" ? [] : [{
          alert_id: "alert-safe",
          tenant_ref: "tenant-raw-must-not-surface",
          actor_ref: "actor-raw-must-not-surface",
          budget_id: "budget-safe",
          period_key: "2026-07",
          threshold_pct: 95,
          budget_amount_usd: 200,
          estimated_spend_usd: 190,
          pricing_coverage_pct: 90,
          delivery_state: "sent",
          attempt_count: 1,
          triggered_at: NOW,
          sent_at: NOW,
          updated_at: NOW,
        }],
        cursor: { next: null, limit: 50 },
        data_status: control.memberBudgetAlertsState === "unavailable"
          ? "unavailable"
          : control.memberBudgetEmpty
            ? "complete"
            : "partial",
        currency: "USD",
      };
    } else if (path === "/api/finops/member-budgets/budget-safe/disable" && request.method() === "POST") {
      control.memberBudgetDisabled = true;
      body = {
        item: { budget_id: "budget-safe", enabled: false, revision: 4 },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "partial",
        currency: "USD",
      };
    } else if (path === "/api/finops/member-budgets/budget-safe" && request.method() === "PATCH") {
      if (control.memberBudgetConflictOnce) {
        control.memberBudgetConflictOnce = false;
        status = 409;
        body = { detail: "revision conflict internal body" };
      } else {
        if (request.postDataJSON()?.enabled === true) control.memberBudgetDisabled = false;
        body = {
          item: { budget_id: "budget-safe", revision: 4 },
          freshness: "recorded",
          coverage: "request_estimated_cost",
          data_status: "partial",
          currency: "USD",
        };
      }
    } else if (path === "/api/finops/member-budgets/budget-former" && request.method() === "PATCH") {
      body = {
        item: { budget_id: "budget-former", revision: 3 },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "complete",
        currency: "USD",
      };
    } else if (path === "/api/finops/member-budgets" && request.method() === "POST") {
      status = 200;
      body = {
        item: { budget_id: "budget-created", revision: 1 },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "unavailable",
        currency: "USD",
      };
    } else if (path === "/api/model-providers" && request.method() === "GET") {
      body = { items: control.providerItems };
    } else if (path === "/api/model-providers" && request.method() === "POST") {
      const submitted = request.postDataJSON();
      if (submitted.provider_type === "aws_bedrock") {
        const expectedKeys = ["access_key_id", "display_name", "provider_type", "region", "secret_access_key", "session_token"];
        if (Object.keys(submitted).sort().join(",") !== expectedKeys.join(",")) {
          await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "invalid provider request" }) });
          return;
        }
        const provider = {
          provider_id: "provider_bedrock",
          provider_type: "aws_bedrock",
          display_name: submitted.display_name || "AWS Bedrock",
          region: submitted.region,
          base_url: `https://bedrock.${submitted.region}.amazonaws.com`,
          connection_state: control.bedrockConnectionState,
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
        };
        control.providerItems = [
          ...control.providerItems.filter((item) => item.provider_id !== provider.provider_id),
          provider,
        ];
        status = 201;
        body = { provider_id: provider.provider_id };
      } else {
        status = 201;
        body = { provider_id: "provider_deepseek" };
      }
    } else if (path === "/api/model-providers/provider_bedrock/rotate-secret") {
      const submitted = request.postDataJSON();
      const expectedKeys = ["access_key_id", "base_revision", "provider_type", "secret_access_key", "session_token"];
      if (Object.keys(submitted).sort().join(",") !== expectedKeys.join(",")) {
        await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "invalid rotation request" }) });
        return;
      }
      const existing = control.providerItems.find((item) => item.provider_id === "provider_bedrock");
      if (existing) {
        existing.revision += 1;
      }
      body = { provider_id: "provider_bedrock" };
    } else if (path === "/api/workspaces/demo-corpus/governance/model-routing") {
      body = {
        workspace_id: "demo-corpus",
        default_route: "analysis",
        routes: [
          {
            id: "analysis",
            deployment: "gpt-5.1",
            label: "GPT-5.1",
            capabilities: ["analysis", "chat"],
          },
          {
            id: "terra",
            deployment: "gpt-5.6-terra",
            label: "GPT-5.6 Terra",
            capabilities: ["analysis", "chat"],
          },
        ],
        policy: {
          revision: 3,
          default_route_id: "analysis",
          assignments: {},
          agent_assignments: {
            "df-auditor": {
              primary_route_id: "terra",
              fallback_route_id: "analysis",
            },
          },
        },
        price_card: { state: "not_configured", revision: 0, currency: "USD" },
      };
    } else if (path === "/api/finops/pricing/catalog") {
      body = {
        revision: "azure-retail-2026-07-27",
        currency: "USD",
        items: [
          {
            price_key: "azure-openai:gpt-5.1:global-standard:global",
            official_model: "gpt-5.1",
            display_name: "GPT-5.1 Global Standard",
            input_per_million: 1.25,
            output_per_million: 10,
            cached_input_per_million: 0.125,
            source_url: "https://prices.azure.com/api/retail/prices",
          },
          {
            price_key: "azure-openai:gpt-5.6-sol:global-standard:global",
            official_model: "gpt-5.6-sol",
            display_name: "GPT-5.6 Sol Global Standard",
            input_per_million: 5,
            output_per_million: 30,
            cached_input_per_million: 0.5,
            source_url: "https://prices.azure.com/api/retail/prices",
          },
          {
            price_key: "azure-openai:gpt-5.6-terra:global-standard:global",
            official_model: "gpt-5.6-terra",
            display_name: "GPT-5.6 Terra Global Standard",
            input_per_million: 2.5,
            output_per_million: 15,
            cached_input_per_million: 0.25,
            source_url: "https://prices.azure.com/api/retail/prices",
          },
          {
            price_key: "azure-openai:gpt-5.6-luna:global-standard:global",
            official_model: "gpt-5.6-luna",
            display_name: "GPT-5.6 Luna Global Standard",
            input_per_million: 1,
            output_per_million: 6,
            cached_input_per_million: 0.1,
            source_url: "https://prices.azure.com/api/retail/prices",
          },
        ],
        count: 4,
      };
    } else if (path === "/api/finops/pricing/mappings") {
      body = {
        items: [{
          deployment: "gpt-5.1",
          official_price_key: "azure-openai:gpt-5.1:global-standard:global",
          mapping_revision: 1,
        }],
        count: 1,
      };
    } else if (path === "/api/finops/requests") {
      body = {
        ...bootstrapPayload,
        items: [
          {
            request_ref: "req_aaaaaaaaaaaa",
            occurred_at: "2026-07-24T02:42:00Z",
            workspace_id: "demo-corpus",
          },
        ],
        count: 1,
        next_cursor: null,
      };
    } else if (path === "/api/finops/requests/req_aaaaaaaaaaaa") {
      body = {
        ...bootstrapPayload,
        display: {
          name: "Commerce · 分析运行 · 7月24日 10:42",
          operation: "分析运行",
          occurred_at: "2026-07-24T02:42:00Z",
        },
        status: "succeeded",
        metrics: {
          latency_ms: 1300,
          tokens: { input: 10, output: 2, total: 12 },
          cache: { state: "miss", eligible: true },
          estimated_cost: { amount: 0.001, status: "estimated", currency: "USD" },
          gateway_coverage: "apim_governed",
          evidence_state: "observed",
          error_category: null,
        },
        business_request: { text: "分析本月销售异常", status: "recorded" },
        business_response: { text: "已定位主要变化来自华东区域。", status: "recorded" },
        timeline: [
          { stage: "gateway", label: "APIM 网关", status: "observed" },
          { stage: "orchestration", label: "MAF 编排", status: "observed" },
          { stage: "execution", label: "df-coordinator · gpt-5-mini", status: "observed" },
          { stage: "response", label: "完成返回", status: "succeeded", latency_ms: 1300 },
        ],
        technical_refs: {
          request_ref: "req_aaaaaaaaaaaa",
          run_id: "run-a",
          apim_correlation_id: "4f8b0f37b5824af5a2ac7ed9129ee70b",
        },
        links: {
          foundry_trace: "https://ai.azure.com/trace/0123456789abcdef",
        },
      };
    } else if (path === "/api/finops/breakdowns") {
      body = {
        ...bootstrapPayload,
        items: bootstrapPayload.departments.items,
        count: 2,
      };
    } else if (path === "/api/finops/agents") {
      body = {
        ...bootstrapPayload,
        agents: [
          {
            key: "df-coordinator",
            requests: 60,
            tokens: 2307,
            estimated_cost: 0.0269,
            success_rate_pct: 93.3,
            p95_latency_ms: 2100,
          },
        ],
        models: [
          {
            key: "gpt-5-mini",
            requests: 60,
            tokens: 2307,
            estimated_cost: 0.0269,
            success_rate_pct: 93.3,
            p95_latency_ms: 2100,
          },
        ],
      };
    } else if (path === "/api/finops/budgets") {
      body = {
        items: [{
          budget_id: "budget-a",
          name: "Commerce 月度预算",
          scope_type: "workspace",
          scope_id: "demo-corpus",
          amount: 0.1,
          currency: "USD",
          progress: {
            spent_amount: 0.0269,
            usage_pct: 26.9,
            forecast_amount: 0.041,
            forecast_status: "estimated",
            confidence: "partial",
            threshold_state: "normal",
          },
        }],
        count: 1,
      };
    } else if (path === "/api/finops/views") {
      body = request.method() === "POST"
        ? { view: { view_id: "view-a", name: "财务视图" } }
        : { items: [], count: 0 };
    } else if (path === "/api/finops/trends") {
      body = bootstrapPayload.trend;
    } else if (path === "/api/finops/anomalies") {
      body = {
        ...bootstrapPayload,
        items: [
          {
            anomaly_id: "anom-a",
            policy_type: "p95_latency",
            severity: "warning",
            status: "open",
            observed_value: 2100,
            threshold_value: 2000,
            sample_count: 60,
            recommendation: "检查慢请求来源。",
          },
        ],
        count: 1,
      };
    } else if (path === "/api/finops/recommendations") {
      body = { ...bootstrapPayload, items: [], count: 0 };
    } else if (path === "/api/finops/opportunities") {
      body = {
        ...bootstrapPayload,
        items: [{
          opportunity_id: "opp-latency",
          anomaly_id: "anom-a",
          policy_type: "p95_latency",
          title: "响应时延优化",
          recommendation: "定位慢请求与模型路由瓶颈。",
          impact: "medium",
          confidence: "high",
          effort: "high",
          queue_state: "ready",
          sample_count: 60,
          evidence_state: "observed",
          estimated_savings: null,
          action_status: "suggested",
        }],
        count: 1,
      };
    } else if (path === "/api/finops/actions" && request.method() === "GET") {
      body = { ...bootstrapPayload, items: [], count: 0 };
    } else if (path === "/api/finops/actions" && request.method() === "POST") {
      status = 201;
      body = {
        action: { action_id: "act-a", status: "draft", action_type: "cache_policy" },
        actions_enabled: false,
      };
    } else if (path === "/api/finops/insights/analyze") {
      status = 202;
      body = {
        status: "scheduled",
        agent_kind: "finops",
        trigger_fingerprint: "f".repeat(64),
      };
    } else if (path === "/api/finops/assistant/query") {
      body = {
        answer: "当前缓存命中率为 42%，50 次可缓存调用中有 21 次命中。",
        evidence_state: "observed",
        evidence_refs: ["req_aaaaaaaaaaaa"],
        suggested_questions: ["与上一周期相比如何？"],
      };
    } else if (path === "/api/finops/roi/economics") {
      body = {
        workspace_id: "demo-corpus",
        funnel: [
          { id: "investment", label: "投入", value: 0.0269, unit: "USD", status: "estimated" },
          { id: "usage", label: "使用", value: 60, unit: "次调用", status: "observed" },
          { id: "output", label: "产出", value: null, unit: "个产物", status: "unavailable" },
          { id: "outcome", label: "业务结果", value: null, unit: "项已验证结果", status: "not_recorded" },
        ],
        unit_economics: {
          cost_per_successful_request: { label: "每次成功调用成本", value: 0.00048, currency: "USD", status: "estimated" },
          cost_per_analysis: { label: "每次分析成本", value: 0.0269, currency: "USD", status: "estimated" },
          cost_per_artifact: { label: "每个产物成本", value: null, currency: null, status: "unavailable" },
        },
        verified_roi: { status: "not_recorded", value: null },
        evidence_gaps: ["独立验证的业务结果", "可计数的交付产物"],
        scenarios: [],
      };
    } else if (path === "/api/workspaces/demo-corpus/governance/roi") {
      body = {
        workspace_id: "demo-corpus",
        status: "estimated",
        business_value: null,
        cost: { total: 0.0269, currency: "USD", status: "complete" },
        outcome_evidence: {
          status: "not_recorded",
          verified_outcome_event_ids: [],
        },
        lineage_complete: true,
      };
    } else if (path === "/api/workspaces/demo-corpus/governance/cost-value") {
      body = {
        workspace_id: "demo-corpus",
        cost_evidence: { total: 0.0269, currency: "USD", status: "complete" },
        outcome_evidence: { status: "not_recorded", verified_outcome_event_ids: [] },
        realized_roi: { status: "not_recorded", roi_ratio: null },
      };
    } else if (path === "/api/health") {
      body = { ok: true };
    } else if (path === "/api/workspaces") {
      body = { workspaces: [{ workspace_id: "demo-corpus", name: "Commerce" }] };
    } else {
      body = {};
    }

    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return control;
}

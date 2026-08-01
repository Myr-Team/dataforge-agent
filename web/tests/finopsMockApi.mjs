const NOW = new Date().toISOString();

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
    refresh_after_seconds: 300,
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
        avoided_tokens: 1840,
        estimated_savings: 0.0118,
        data_status: "partial",
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
        cache: {
          eligible_requests: 14,
          hit: 5,
          miss: 9,
          bypassed: 4,
          unavailable: 0,
          avoided_tokens: 420,
          estimated_savings: 0.0021,
          data_status: "available",
        },
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
        cache: {
          eligible_requests: 19,
          hit: 8,
          miss: 11,
          bypassed: 2,
          unavailable: 1,
          avoided_tokens: 760,
          estimated_savings: 0.0054,
          data_status: "partial",
        },
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
        cache: {
          eligible_requests: 17,
          hit: 8,
          miss: 9,
          bypassed: 2,
          unavailable: 1,
          avoided_tokens: 660,
          estimated_savings: 0.0043,
          data_status: "partial",
        },
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
        cache_hit_rate_pct: 48.28,
        data_status: "available",
      },
      {
        key: "Finance",
        requests: 28,
        tokens: 1067,
        estimated_cost: 0.0129,
        error_rate_pct: 10.7,
        p95_latency_ms: 2400,
        cache_hit_rate_pct: 34.62,
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
          evidence_refs: ["req_slow_000001"],
        },
      ],
      evidence_refs: ["req_slow_000001"],
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
    workspaces: ["Commerce Insights", "Finance Forecast", "IT Governance"],
    agents: ["分析协调 Agent", "财务洞察 Agent", "风险审阅 Agent"],
    models: ["gpt-5.6-terra", "gpt-5.1", "deepseek-chat"],
  },
};


function demoCompletenessBootstrapPayload() {
  return {
    ...bootstrapPayload,
    overview: {
      ...bootstrapPayload.overview,
      trust: {
        ...bootstrapPayload.overview.trust,
        apim: {
          ...bootstrapPayload.overview.trust.apim,
          apim_governed_requests: 56,
          coverage_pct: 93.33,
        },
      },
    },
    trend: {
      ...bootstrapPayload.trend,
      items: bootstrapPayload.trend.items.map((item, index) => ({
        ...item,
        tokens: {
          ...item.tokens,
          reasoning: item.tokens.reasoning ?? (index + 1) * 16,
        },
      })),
    },
    anomalies: {
      count: 4,
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
          evidence_refs: ["req_slow_000001"],
          evidence_state: "observed",
        },
        {
          policy_type: "cache_hit_rate",
          title: "缓存命中率偏低",
          severity: "warning",
          status: "open",
          observed_value: 42,
          threshold_value: 60,
          sample_count: 50,
          observed_at: NOW,
          evidence_refs: ["req_cache_000001"],
          evidence_state: "observed",
        },
        {
          policy_type: "unpriced_requests",
          title: "计价覆盖需要补齐",
          severity: "warning",
          status: "acknowledged",
          observed_value: 6.2,
          threshold_value: 5,
          sample_count: 30,
          observed_at: NOW,
          evidence_refs: ["req_unpriced_001"],
          evidence_state: "partial",
        },
        {
          policy_type: "error_rate",
          title: "调用成功率需要改善",
          severity: "critical",
          status: "open",
          observed_value: 8.3,
          threshold_value: 5,
          sample_count: 24,
          observed_at: NOW,
          evidence_refs: ["req_error_000001"],
          evidence_state: "observed",
        },
      ],
    },
  };
}


const roiDecisionPayload = {
  scope: { workspace_ids: ["demo-corpus"], workspace_count: 1 },
  window: bootstrapPayload.window,
  freshness: {
    generated_at: NOW,
    query_cache: { provider: "redis", status: "miss" },
  },
  currency: "USD",
  data_status: "partial",
  decision: {
    state: "scenario_positive_unverified",
    title: "测算显示具备投入价值，业务结果仍需验证",
    summary: "情景参数与运行事实严格分开；验证完成前不显示已实现 ROI。",
    evidence_state: "estimated",
  },
  metrics: [
    { id: "monthly_benefit", label: "月度收益", value: 3000, unit: "USD", status: "estimated", explanation: "来自情景测算，非已验证业务结果。" },
    { id: "monthly_total_cost", label: "月度总成本", value: 700.0269, unit: "USD", status: "estimated", explanation: "包含实施摊销、固定成本与当前模型估算成本。" },
    { id: "monthly_net_benefit", label: "月度净收益", value: 2299.9731, unit: "USD", status: "estimated", explanation: "月度收益减去同一情景口径下的月度总成本。" },
    { id: "roi_ratio", label: "ROI 比率", value: 3.285549, unit: "ratio", status: "estimated", explanation: "情景测算比率，不能替代已验证业务结果。" },
  ],
  value_bridge: {
    formula_revision: "dataforge-roi-v1",
    scenario_id: "roi_scenario_demo0001",
    payback_months: 2.142878,
    items: [
      { id: "monthly_benefit", label: "月度收益", value: 3000, unit: "USD", status: "estimated", explanation: "工时价值与避免损失的情景合计。" },
      { id: "monthly_total_cost", label: "月度总成本", value: -700.0269, unit: "USD", status: "estimated", explanation: "作为价值桥的成本扣减项展示。" },
      { id: "monthly_net_benefit", label: "月度净收益", value: 2299.9731, unit: "USD", status: "estimated", explanation: "同一测算周期内的净效益。" },
    ],
  },
  evidence_maturity: {
    score_pct: 75,
    formula_revision: "roi-evidence-maturity-v1",
    stages: [
      { id: "investment", label: "投入", value: 700.0269, unit: "USD", status: "estimated", evidence_count: 58, evidence_refs: ["req_unpriced_001"], complete: true },
      { id: "usage", label: "使用", value: 60, unit: "次调用", status: "observed", evidence_count: 60, evidence_refs: ["req_cache_000001"], complete: true },
      { id: "output", label: "产出", value: 12, unit: "个产物", status: "observed", evidence_count: 12, evidence_refs: ["req_slow_000001"], complete: true },
      { id: "outcome", label: "业务结果", value: 2, unit: "项结果", status: "partial", evidence_count: 2, evidence_refs: ["outcome_demo_001", "outcome_demo_002"], evidence_gap: "业务结果仍需独立复核后才能计入已验证 ROI。", complete: false },
    ],
  },
  unit_economics_trend: [
    { id: "cost-per-call", period: "7月22日", label: "每次成功调用成本", value: 0.00039, unit: "USD", status: "estimated" },
    { id: "cost-per-call", period: "7月23日", label: "每次成功调用成本", value: 0.00046, unit: "USD", status: "estimated" },
    { id: "cost-per-call", period: "7月24日", label: "每次成功调用成本", value: 0.00048, unit: "USD", status: "estimated" },
  ],
  verified_roi: { status: "not_recorded", value: null, currency: "USD" },
  capability_explanation: {
    platform_confirmed: ["调用、Token 与模型成本", "成功率、时延与缓存节省", "运行、分析与产物关联"],
    business_verification: ["节省工时与小时价值", "避免损失或新增收益", "结果负责人审核与确认"],
    governance_boundary: ["估算情景不写成已实现收益", "读取页面不会自动调用 Agent", "缺失证据不补零"],
  },
  scenarios: [{
    scenario_id: "roi_scenario_demo0001",
    status: "estimated",
    result: {
      monthly_benefit: 3000,
      monthly_total_cost: 700.0269,
      monthly_net_benefit: 2299.9731,
      roi_ratio: 3.285549,
      payback_months: 2.142878,
      formula_revision: "dataforge-roi-v1",
    },
  }],
  evidence_gaps: ["业务结果仍需独立验证"],
};


const riskEvidence = [
  {
    request_ref: "req_slow_000001",
    request_name: "Commerce · 批量分析 · 慢响应",
    operation: "批量分析",
    signal: { metric: "响应时延", value: 6200, unit: "ms" },
    latency_ms: 6200,
    cache_state: "miss",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "分析已完成，但模型响应阶段耗时偏高。",
    technical_refs: { request_ref: "req_slow_000001", run_id: "run_slow_000001" },
  },
  {
    request_ref: "req_cache_000001",
    request_name: "Commerce · 重复分析 · 缓存未命中",
    operation: "重复分析",
    signal: { metric: "缓存命中率", value: 18.5, unit: "percent" },
    latency_ms: 1850,
    cache_state: "miss",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "本次请求未命中结果缓存，已重新执行分析。",
    technical_refs: { request_ref: "req_cache_000001", run_id: "run_cache_000001" },
  },
  {
    request_ref: "req_unpriced_001",
    request_name: "Finance · 模型评审 · 尚未计价",
    operation: "模型评审",
    signal: { metric: "未计价请求", value: 6.2, unit: "percent" },
    latency_ms: 1700,
    cache_state: "bypassed",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "评审已完成，当前模型尚未关联价目。",
    technical_refs: { request_ref: "req_unpriced_001", run_id: "run_unpriced_001" },
  },
  {
    request_ref: "req_error_000001",
    request_name: "Commerce · 机会提取 · 调用失败",
    operation: "机会提取",
    signal: { metric: "调用失败率", value: 8.3, unit: "percent" },
    latency_ms: 980,
    cache_state: "bypassed",
    status: "failed",
    error_category: "provider_5xx",
    visible_answer_summary: "调用在模型执行阶段失败，未生成业务侧回答。",
    technical_refs: { request_ref: "req_error_000001", run_id: "run_error_000001" },
  },
];


function riskDecisionPayload(baseVersion = "cache-policy-v1") {
  const opportunities = [
    { opportunity_id: "opp-latency", anomaly_id: "anom-latency", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "p95_latency", risk_domain: "experience", title: "响应时延优化", recommendation: "拆分大批量分析并复核高时延模型路由。", impact: "high", confidence: "high", effort: "high", sample_count: 60, evidence_refs: ["req_slow_000001"], expected_impact: { status: "estimated", value: 1.4, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-cache", anomaly_id: "anom-cache", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "cache_hit_rate", risk_domain: "efficiency", title: "缓存效率优化", recommendation: "统一重复分析的缓存键并复核有效期。", impact: "medium", confidence: "high", effort: "low", sample_count: 54, evidence_refs: ["req_cache_000001"], expected_impact: { status: "estimated", value: 0.0048, currency: "USD" }, base_version: baseVersion },
    { opportunity_id: "opp-unpriced", anomaly_id: "anom-unpriced", anomaly_status: "acknowledged", applicable_actions: ["suppress"], policy_type: "unpriced_requests", risk_domain: "cost", title: "计价覆盖补齐", recommendation: "为新接入模型补齐官方价目映射。", impact: "medium", confidence: "medium", effort: "medium", sample_count: 30, evidence_refs: ["req_unpriced_001"], expected_impact: { status: "estimated", value: 0.0026, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-error", anomaly_id: "anom-error", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "error_rate", risk_domain: "governance", title: "调用成功率改善", recommendation: "按失败类别和调用来源修复错误。", impact: "high", confidence: "medium", effort: "medium", sample_count: 24, evidence_refs: ["req_error_000001"], expected_impact: { status: "estimated", value: 0.0031, currency: "USD" }, base_version: "remediation-template-v1" },
  ];
  const levels = { low: 1, medium: 2, high: 3 };
  return {
    scope: { workspace_ids: ["demo-corpus"], workspace_count: 1 },
    window: bootstrapPayload.window,
    freshness: { generated_at: NOW, query_cache: { provider: "redis", status: "miss" } },
    data_status: "partial",
    decision: { state: "prioritized", title: "已按影响与证据确定优化优先级", summary: "风险以影响、置信度、影响范围和可追溯证据展示，不使用复合风险分数。", evidence_state: "observed" },
    risk_domains: [
      { id: "cost", count: 1 },
      { id: "experience", count: 1 },
      { id: "efficiency", count: 1 },
      { id: "governance", count: 1 },
    ],
    risk_matrix: opportunities.map((item) => ({
      ...item,
      x_confidence: levels[item.confidence],
      y_impact: levels[item.impact],
      x_confidence_state: "observed",
      y_impact_state: "observed",
      bubble_size: item.sample_count,
    })),
    priorities: opportunities,
    optimization_portfolio: opportunities.map((item) => ({
      ...item,
      x_effort: levels[item.effort],
      y_value_impact: levels[item.impact],
      x_effort_state: "observed",
      y_value_impact_state: "observed",
      bubble_size: item.sample_count,
    })),
    portfolio_metadata: { x_axis: "effort", y_axis: "value_impact", size: "affected_scope", color: "risk_domain" },
    selected_evidence_summaries: riskEvidence,
    insight: { title: "优先处理慢响应并验证缓存策略", summary: "高时延影响范围最大；缓存未命中具有可量化的成本改善空间。", status: "observed" },
    drafts: [],
    governance_capability: { read_enabled: true, draft_enabled: true, actions_enabled: false, typed_executors: ["cache_policy"] },
  };
}


function remediationDraft(revision = 1, status = "draft") {
  return {
    draft_id: "remediation-demo-cache",
    workspace_id: "demo-corpus",
    source_opportunity_id: "opp-cache",
    source_anomaly_id: "anom-cache",
    risk_type: "cache_hit_rate",
    title: "缓存效率优化整改草案",
    summary: "在候选范围验证更稳定的缓存有效期，不直接修改生产配置。",
    scope: { workspace_id: "demo-corpus", operation: "重复分析" },
    evidence_refs: ["req_cache_000001"],
    proposed_changes: [
      { field: "ttl_seconds", current_value: 300, candidate_value: 900, rationale: "覆盖演示工作区的高频重复分析窗口。" },
      { field: "enabled", current_value: true, candidate_value: true, rationale: "保持缓存启用，仅调整候选有效期。" },
    ],
    expected_impact: { status: "estimated", amount: 0.0048, unit: "USD", calculation_basis: "基于当前缓存未命中请求与价目表估算。" },
    prerequisites: ["候选环境复现相同分析输入", "保留当前缓存策略版本"],
    risks_and_guardrails: ["结果一致性不得下降", "生产执行保持关闭"],
    verification_plan: [{ metric: "cache_hit_rate_pct", operator: "gte", target: 60, minimum_samples: 20, candidate_window_minutes: 30 }],
    rollback_plan: ["恢复 cache-policy-v1", "清理候选缓存键"],
    action_kind: "cache_policy",
    execution_capability: "typed_action_available",
    base_version: "cache-policy-v1",
    status,
    revision,
    created_at: "2026-07-31T08:00:00Z",
    updated_at: "2026-07-31T08:05:00Z",
  };
}


export async function installFinOpsMockApi(page, calls = [], options = {}) {
  const control = {
    demoCompleteness: Boolean(options.demoCompleteness),
    failBootstrap: Boolean(options.failBootstrap),
    failDetail: Boolean(options.failDetail),
    bedrockConnectionState: options.bedrockConnectionState || "connected",
    providerItems: Array.isArray(options.providerItems) ? [...options.providerItems] : [],
    memberBudgetFailure: Boolean(options.memberBudgetFailure),
    memberBudgetAccessState: options.memberBudgetAccessState || "allowed",
    memberBudgetEmpty: Boolean(options.memberBudgetEmpty),
    memberBudgetConflictOnce: Boolean(options.memberBudgetConflictOnce),
    memberBudgetEmailState: options.memberBudgetEmailState || "sent",
    memberBudgetActiveDisabled: Boolean(options.memberBudgetActiveDisabled),
    memberBudgetNotificationState: options.memberBudgetNotificationState || "configured",
    memberBudgetAlertsState: options.memberBudgetAlertsState || "available",
    memberBudgetRecipientEmail: "demo-admin@example.test",
    memberBudgetTested: true,
    memberBudgetNotificationEnabled: true,
    memberBudgetDisabled: false,
    roiScenarioConflictOnce: Boolean(options.roiScenarioConflictOnce),
    remediationReviewConflictOnce: Boolean(options.remediationReviewConflictOnce),
    failRoiRefresh: Boolean(options.failRoiRefresh),
    decisionDelayMs: Number(options.decisionDelayMs || 0),
    delayNextRoiRefreshMs: 0,
    riskBaseVersion: "cache-policy-v1",
    remediation: null,
    calls: {
      bootstrap: 0,
      roiDecision: 0,
      riskDecision: 0,
      remediationCreate: 0,
      remediationReview: 0,
    },
    timings: { roiDecision: [], riskDecision: [] },
    roiScenarios: [{
      scenario_id: "roi_scenario_demo0001",
      title: "运营自动化测算",
      status: "estimated",
      revision: 1,
      previous_id: null,
      inputs: {
        currency: "USD",
        hours_saved: 40,
        hourly_value: 50,
        avoided_loss_or_revenue: 1000,
        implementation_cost: 6000,
        monthly_fixed_cost: 200,
        model_cost: 0.0269,
        evaluation_months: 12,
        evidence_revision: 1,
      },
      result: {
        status: "estimated",
        currency: "USD",
        monthly_benefit: 3000,
        implementation_amortization: 500,
        monthly_total_cost: 700.0269,
        monthly_net_benefit: 2299.9731,
        roi_ratio: 3.285549,
        payback_months: 2.142878,
        formula_revision: "dataforge-roi-v1",
      },
      formula_revision: "dataforge-roi-v1",
    }],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    calls.push({ method: request.method(), path, body: request.postData() || "" });
    if (path === "/api/finops/bootstrap") control.calls.bootstrap += 1;
    if (path === "/api/finops/roi/decision") control.calls.roiDecision += 1;
    if (path === "/api/finops/risk/decision") control.calls.riskDecision += 1;
    if (path === "/api/finops/remediation-drafts" && request.method() === "POST") control.calls.remediationCreate += 1;
    if (/^\/api\/finops\/remediation-drafts\/[^/]+\/review$/.test(path)) control.calls.remediationReview += 1;
    let body = {};
    let status = 200;

    if (["/api/finops/roi/decision", "/api/finops/risk/decision"].includes(path)) {
      const startedAt = Date.now();
      const refreshDelay = path === "/api/finops/roi/decision" && url.searchParams.get("refresh") === "1"
        ? Number(control.delayNextRoiRefreshMs || 0)
        : 0;
      const delayMs = Math.max(Number(control.decisionDelayMs || 0), refreshDelay);
      control.delayNextRoiRefreshMs = 0;
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
      const timingKey = path.includes("/roi/") ? "roiDecision" : "riskDecision";
      control.timings[timingKey].push(Date.now() - startedAt);
    }

    if (
      path === "/api/finops/roi/decision"
      && control.failRoiRefresh
      && url.searchParams.get("refresh") === "1"
    ) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "ROI decision refresh is temporarily unavailable" }),
      });
      return;
    }

    if (path === "/api/finops/bootstrap" && control.failBootstrap) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "FinOps evidence service is unavailable" }),
      });
      return;
    }
    if (
      control.failDetail
      && [
        "/api/finops/breakdowns",
        "/api/finops/agents",
        "/api/finops/views",
        "/api/finops/anomalies",
        "/api/finops/recommendations",
        "/api/finops/actions",
        "/api/finops/opportunities",
        "/api/finops/roi/economics",
        "/api/finops/roi/decision",
        "/api/finops/risk/decision",
        "/api/workspaces/demo-corpus/governance/roi",
        "/api/workspaces/demo-corpus/governance/cost-value",
      ].includes(path)
    ) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "FinOps detail service is unavailable" }),
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
      body = control.demoCompleteness ? demoCompletenessBootstrapPayload() : bootstrapPayload;
    } else if (
      control.memberBudgetAccessState === "permission_required"
      && request.method() === "GET"
      && [
        "/api/finops/member-budgets",
        "/api/finops/member-budget-members",
        "/api/finops/notification-settings",
        "/api/finops/budget-alerts",
      ].includes(path)
    ) {
      status = 403;
      body = { detail: "Workspace administrator role required" };
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
      if (control.memberBudgetNotificationState === "disabled") {
        status = 404;
        body = { detail: "email_configuration_disabled" };
      } else if (control.memberBudgetNotificationState === "not_configured") {
        status = 404;
        body = { detail: "Not found" };
      } else if (control.memberBudgetNotificationState === "unavailable") {
        status = 503;
        body = { detail: "internal-notification-body-must-not-surface" };
      } else if (control.memberBudgetNotificationState === "permission_required") {
        status = 403;
        body = { detail: "Tenant FinOps administrator role required" };
      } else {
        body = {
          item: {
            recipient_actor_ref: "member-safe",
            recipient_email: control.memberBudgetRecipientEmail,
            sender_display_name: "DataForge",
            subject_template: "{{member_name}} 预算提醒",
            body_template: "{{estimated_spend}} / {{budget_amount}}",
            enabled: control.memberBudgetNotificationEnabled,
            test_email_succeeded_at: control.memberBudgetTested ? NOW : null,
            revision: 2,
          },
          freshness: "recorded",
          coverage: "request_estimated_cost",
          data_status: "complete",
          currency: "USD",
        };
      }
    } else if (path === "/api/finops/notification-settings" && request.method() === "PUT") {
      const payload = request.postDataJSON();
      control.memberBudgetNotificationState = "configured";
      control.memberBudgetRecipientEmail = payload.recipient_email;
      control.memberBudgetNotificationEnabled = payload.enabled === true;
      control.memberBudgetTested = false;
      body = {
        item: {
          recipient_actor_ref: "member-safe",
          recipient_email: control.memberBudgetRecipientEmail,
          sender_display_name: "DataForge",
          subject_template: "{{member_name}} 预算提醒",
          body_template: "{{estimated_spend}} / {{budget_amount}}",
          enabled: control.memberBudgetNotificationEnabled,
          test_email_succeeded_at: null,
          revision: 3,
        },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "complete",
        currency: "USD",
      };
    } else if (path === "/api/finops/notification-settings/test-email" && request.method() === "POST") {
      if (control.memberBudgetEmailState === "sent") control.memberBudgetTested = true;
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
          { stage: "gateway", label: "统一入口", status: "observed" },
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
    } else if (
      path === "/api/finops/requests/req_slow_000001"
      || path === "/api/finops/requests/req_cache_000001"
      || path === "/api/finops/requests/req_unpriced_001"
      || path === "/api/finops/requests/req_error_000001"
    ) {
      const requestRef = path.split("/").at(-1);
      const profiles = {
        req_slow_000001: {
          operation: "批量分析",
          request: "批量分析本周客户反馈并生成归因摘要",
          response: "分析已完成，但模型响应阶段耗时偏高。",
          status: "succeeded",
          latency: 6200,
          cache: "miss",
          cost: 0.0142,
          error: null,
        },
        req_cache_000001: {
          operation: "重复分析",
          request: "重新分析相同数据并复用上次结果",
          response: "本次请求未命中结果缓存，已重新执行分析。",
          status: "succeeded",
          latency: 1850,
          cache: "miss",
          cost: 0.0084,
          error: null,
        },
        req_unpriced_001: {
          operation: "模型评审",
          request: "使用新接入模型评审候选机会",
          response: "评审已完成，当前模型尚未关联价目。",
          status: "succeeded",
          latency: 1700,
          cache: "bypassed",
          cost: null,
          error: null,
        },
        req_error_000001: {
          operation: "机会提取",
          request: "提取高价值客户机会并生成摘要",
          response: null,
          status: "failed",
          latency: 980,
          cache: "bypassed",
          cost: 0.0021,
          error: "provider_5xx",
        },
      };
      const profile = profiles[requestRef];
      body = {
        ...bootstrapPayload,
        display: {
          name: `Commerce · ${profile.operation} · 7月24日 14:10`,
          operation: profile.operation,
          occurred_at: "2026-07-24T06:10:00Z",
        },
        status: profile.status,
        metrics: {
          latency_ms: profile.latency,
          tokens: { input: 1450, output: 320, total: 1770 },
          cache: { state: profile.cache, eligible: profile.cache === "miss" },
          estimated_cost: {
            amount: profile.cost,
            status: profile.cost == null ? "unavailable" : "estimated",
            currency: "USD",
          },
          gateway_coverage: "apim_governed",
          evidence_state: profile.cost == null ? "partial" : "observed",
          error_category: profile.error,
        },
        business_request: { text: profile.request, status: "recorded" },
        business_response: {
          text: profile.response,
          status: profile.response ? "recorded" : "unavailable",
        },
        timeline: [
          { stage: "gateway", label: "统一入口", status: "observed" },
          { stage: "orchestration", label: "DataForge 编排", status: "observed" },
          { stage: "response", label: profile.status === "failed" ? "调用失败" : "完成返回", status: profile.status, latency_ms: profile.latency },
        ],
        technical_refs: {
          request_ref: requestRef,
          run_id: `run_${requestRef}`,
        },
        links: {},
      };
    } else if (path === "/api/finops/breakdowns") {
      const isWorkspace = url.searchParams.get("group_by") === "workspace";
      const workspaceItems = [
        { key: "Commerce Insights", requests: 32, tokens: 1240, estimated_cost: 0.014, error_rate_pct: 3.1, p95_latency_ms: 1800, cache_hit_rate_pct: 48.3, data_status: "available" },
        { key: "Finance Forecast", requests: 18, tokens: 700, estimated_cost: 0.0081, error_rate_pct: 5.6, p95_latency_ms: 2600, cache_hit_rate_pct: 33.3, data_status: "partial" },
        { key: "IT Governance", requests: 10, tokens: 547, estimated_cost: 0.0048, error_rate_pct: 10, p95_latency_ms: 1400, cache_hit_rate_pct: 60, data_status: "partial" },
      ];
      body = {
        ...bootstrapPayload,
        items: isWorkspace ? workspaceItems : bootstrapPayload.departments.items,
        count: isWorkspace ? workspaceItems.length : bootstrapPayload.departments.items.length,
      };
    } else if (path === "/api/finops/agents") {
      body = {
        ...bootstrapPayload,
        agents: [
          { key: "分析协调 Agent", requests: 30, tokens: 1240, estimated_cost: 0.0142, error_rate_pct: 3.3, success_rate_pct: 96.7, p95_latency_ms: 1800, cache_hit_rate_pct: 53.3, data_status: "available" },
          { key: "财务洞察 Agent", requests: 18, tokens: 720, estimated_cost: 0.0081, error_rate_pct: 5.6, success_rate_pct: 94.4, p95_latency_ms: 2400, cache_hit_rate_pct: 38.9, data_status: "partial" },
          { key: "风险审阅 Agent", requests: 12, tokens: 527, estimated_cost: 0.0046, error_rate_pct: 16.7, success_rate_pct: 83.3, p95_latency_ms: 1100, cache_hit_rate_pct: 25, data_status: "partial" },
        ],
        models: [
          { key: "gpt-5.6-terra", requests: 28, tokens: 1300, estimated_cost: 0.0151, error_rate_pct: 3.6, success_rate_pct: 96.4, p95_latency_ms: 2500, cache_hit_rate_pct: 46.4, data_status: "estimated" },
          { key: "gpt-5.1", requests: 20, tokens: 720, estimated_cost: 0.0073, error_rate_pct: 5, success_rate_pct: 95, p95_latency_ms: 1700, cache_hit_rate_pct: 40, data_status: "estimated" },
          { key: "deepseek-chat", requests: 12, tokens: 467, estimated_cost: 0.0045, error_rate_pct: 16.7, success_rate_pct: 83.3, p95_latency_ms: 1200, cache_hit_rate_pct: 33.3, data_status: "estimated" },
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
            anomaly_id: "anom-latency",
            policy_type: "p95_latency",
            severity: "warning",
            status: "open",
            observed_value: 2100,
            threshold_value: 2000,
            sample_count: 60,
            evidence_refs: ["req_slow_000001"],
            recommendation: "检查慢请求来源。",
          },
          {
            anomaly_id: "anom-cache",
            policy_type: "cache_hit_rate",
            severity: "warning",
            status: "open",
            observed_value: 18.5,
            threshold_value: 60,
            sample_count: 54,
            recommendation: "检查重复分析的缓存键与失效窗口。",
            evidence_refs: ["req_cache_000001"],
          },
          {
            anomaly_id: "anom-unpriced",
            policy_type: "unpriced_requests",
            severity: "warning",
            status: "acknowledged",
            observed_value: 6.2,
            threshold_value: 5,
            sample_count: 60,
            recommendation: "为新接入模型补齐官方价目映射。",
            evidence_refs: ["req_unpriced_001"],
          },
          {
            anomaly_id: "anom-error",
            policy_type: "error_rate",
            severity: "critical",
            status: "open",
            observed_value: 8.3,
            threshold_value: 5,
            sample_count: 24,
            recommendation: "按错误类别定位失败调用来源。",
            evidence_refs: ["req_error_000001"],
          },
        ],
        count: 4,
      };
    } else if (path === "/api/finops/recommendations") {
      body = {
        ...bootstrapPayload,
        items: [
          {
            recommendation_id: "rec-latency",
            policy_type: "p95_latency",
            severity: "warning",
            recommendation: "拆分大批量分析并复核高时延模型路由。",
            evidence_refs: ["req_slow_000001"],
          },
          {
            recommendation_id: "rec-cache",
            policy_type: "cache_hit_rate",
            severity: "warning",
            recommendation: "统一重复分析的缓存键并复核有效期。",
            evidence_refs: ["req_cache_000001"],
          },
          {
            recommendation_id: "rec-unpriced",
            policy_type: "unpriced_requests",
            severity: "warning",
            recommendation: "为新接入模型补齐官方价目映射。",
            evidence_refs: ["req_unpriced_001"],
          },
          {
            recommendation_id: "rec-error",
            policy_type: "error_rate",
            severity: "critical",
            recommendation: "按失败类别和调用来源修复错误。",
            evidence_refs: ["req_error_000001"],
          },
        ],
        count: 4,
      };
    } else if (path === "/api/finops/opportunities") {
      body = {
        ...bootstrapPayload,
        items: [{
          opportunity_id: "opp-latency",
          anomaly_id: "anom-latency",
          policy_type: "p95_latency",
          title: "响应时延优化",
          recommendation: "定位慢请求与模型路由瓶颈。",
          impact: "medium",
          confidence: "high",
          effort: "high",
          queue_state: "ready",
          sample_count: 60,
          evidence_state: "observed",
          evidence_refs: ["req_slow_000001"],
          estimated_savings: null,
          action_status: "suggested",
        },
        {
          opportunity_id: "opp-cache",
          anomaly_id: "anom-cache",
          policy_type: "cache_hit_rate",
          title: "缓存效率优化",
          recommendation: "检查重复分析的缓存资格与失效策略。",
          impact: "medium",
          confidence: "high",
          effort: "medium",
          queue_state: "ready",
          sample_count: 54,
          evidence_state: "observed",
          evidence_refs: ["req_cache_000001"],
          estimated_savings: 0.0048,
          action_status: "suggested",
        },
        {
          opportunity_id: "opp-unpriced",
          anomaly_id: "anom-unpriced",
          policy_type: "unpriced_requests",
          title: "计价覆盖补齐",
          recommendation: "为未计价模型关联官方价目。",
          impact: "medium",
          confidence: "high",
          effort: "low",
          queue_state: "ready",
          sample_count: 60,
          evidence_state: "partial",
          evidence_refs: ["req_unpriced_001"],
          estimated_savings: null,
          action_status: "suggested",
        },
        {
          opportunity_id: "opp-error",
          anomaly_id: "anom-error",
          policy_type: "error_rate",
          title: "调用成功率改善",
          recommendation: "按失败类别和调用来源修复错误。",
          impact: "high",
          confidence: "medium",
          effort: "high",
          queue_state: "ready",
          sample_count: 24,
          evidence_state: "observed",
          evidence_refs: ["req_error_000001"],
          estimated_savings: null,
          action_status: "suggested",
        }],
        count: 4,
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
    } else if (path === "/api/finops/roi/decision") {
      body = roiDecisionPayload;
    } else if (path === "/api/finops/risk/decision") {
      body = riskDecisionPayload(control.riskBaseVersion);
    } else if (path === "/api/finops/remediation-drafts" && request.method() === "GET") {
      body = {
        items: control.remediation ? [control.remediation] : [],
        count: control.remediation ? 1 : 0,
      };
    } else if (path === "/api/finops/remediation-drafts" && request.method() === "POST") {
      const submitted = request.postDataJSON();
      if (submitted.base_version !== control.riskBaseVersion) {
        status = 409;
        body = { detail: "base version changed" };
      } else {
        status = 201;
        control.remediation = remediationDraft(1, "draft");
        body = { draft: control.remediation };
      }
    } else if (path === "/api/finops/remediation-drafts/remediation-demo-cache") {
      body = { draft: control.remediation || remediationDraft(1, "draft") };
    } else if (path === "/api/finops/remediation-drafts/remediation-demo-cache/review") {
      if (control.remediationReviewConflictOnce) {
        control.remediationReviewConflictOnce = false;
        control.remediation = remediationDraft(2, "draft");
        status = 409;
        body = { detail: "remediation revision conflict" };
      } else {
        control.remediation = remediationDraft(Number(control.remediation?.revision || 1) + 1, "reviewed");
        body = { draft: control.remediation };
      }
    } else if (path === "/api/finops/roi/economics") {
      body = {
        workspace_id: "demo-corpus",
        funnel: [
          { id: "investment", label: "投入", value: 0.0269, unit: "USD", status: "estimated" },
          { id: "usage", label: "使用", value: 60, unit: "次调用", status: "observed" },
          { id: "output", label: "产出", value: 60, unit: "次分析", status: "observed" },
          { id: "outcome", label: "业务结果", value: null, unit: "项已验证结果", status: "not_recorded" },
        ],
        unit_economics: {
          cost_per_successful_request: { label: "每次成功调用成本", value: 0.00048, currency: "USD", status: "estimated" },
          cost_per_analysis: { label: "每次分析成本", value: 0.0269, currency: "USD", status: "estimated" },
          cost_per_artifact: { label: "每个产物成本", value: null, currency: null, status: "unavailable" },
          cost_per_verified_outcome: { label: "每个已验证结果成本", value: null, currency: null, status: "unavailable" },
        },
        verified_roi: { status: "not_recorded", value: null },
        evidence_gaps: ["独立验证的业务结果", "可计数的交付产物"],
        scenarios: control.roiScenarios,
      };
    } else if (
      path === "/api/workspaces/demo-corpus/governance/scenarios"
      && request.method() === "POST"
    ) {
      if (control.roiScenarioConflictOnce) {
        control.roiScenarioConflictOnce = false;
        status = 409;
        body = { detail: "ROI scenario revision has changed" };
      } else {
        const submitted = request.postDataJSON();
        const months = Number(submitted.evaluation_months);
        const monthlyBenefit = (
          Number(submitted.hours_saved) * Number(submitted.hourly_value)
          + Number(submitted.avoided_loss_or_revenue)
        );
        const monthlyTotalCost = (
          Number(submitted.implementation_cost) / months
          + Number(submitted.monthly_fixed_cost)
          + Number(submitted.model_cost)
        );
        const scenario = {
          scenario_id: `roi_scenario_demo${String(control.roiScenarios.length + 1).padStart(4, "0")}`,
          title: submitted.title,
          status: "estimated",
          revision: Number(submitted.base_revision || 0) + 1,
          previous_id: submitted.previous_id || null,
          inputs: {
            currency: "USD",
            hours_saved: submitted.hours_saved,
            hourly_value: submitted.hourly_value,
            avoided_loss_or_revenue: submitted.avoided_loss_or_revenue,
            implementation_cost: submitted.implementation_cost,
            monthly_fixed_cost: submitted.monthly_fixed_cost,
            model_cost: submitted.model_cost,
            evaluation_months: submitted.evaluation_months,
            evidence_revision: submitted.evidence_revision,
          },
          result: {
            status: "estimated",
            currency: "USD",
            monthly_benefit: monthlyBenefit,
            implementation_amortization: Number(submitted.implementation_cost) / months,
            monthly_total_cost: monthlyTotalCost,
            monthly_net_benefit: monthlyBenefit - monthlyTotalCost,
            roi_ratio: monthlyTotalCost > 0 ? (monthlyBenefit - monthlyTotalCost) / monthlyTotalCost : null,
            payback_months: monthlyBenefit > Number(submitted.monthly_fixed_cost) + Number(submitted.model_cost)
              ? Number(submitted.implementation_cost) / (
                monthlyBenefit - Number(submitted.monthly_fixed_cost) - Number(submitted.model_cost)
              )
              : null,
            formula_revision: "dataforge-roi-v1",
          },
          formula_revision: "dataforge-roi-v1",
        };
        control.roiScenarios = [...control.roiScenarios, scenario];
        body = { workspace_id: "demo-corpus", scenario };
      }
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
        scenarios: control.roiScenarios,
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


export function installFinOpsDemoCompletenessApi(page, calls = [], options = {}) {
  return installFinOpsMockApi(page, calls, { ...options, demoCompleteness: true });
}

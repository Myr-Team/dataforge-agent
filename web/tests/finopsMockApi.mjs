const NOW = new Date().toISOString();

export const bootstrapPayload = {
  scope: { workspace_ids: ["demo-corpus"], workspace_count: 1 },
  window: {
    from: "2026-07-12T00:00:00Z",
    to: "2026-08-11T23:59:59Z",
    timezone: "UTC",
  },
  freshness: {
    generated_at: NOW,
    sources: ["dataforge_application", "apim"],
    refresh_after_seconds: 300,
  },
  coverage: {
    observed_requests: 2404,
    apim_governed_requests: 2214,
    apim_coverage_pct: 92.1,
  },
  currency: "USD",
  data_status: "partial",
  overview: {
    freshness: { generated_at: NOW },
    data_status: "partial",
    trust: {
      pricing: {
        priced_requests: 2248,
        unpriced_requests: 156,
        coverage_pct: 93.51,
        state: "partial",
      },
      tokens: {
        known_requests: 2404,
        unknown_requests: 0,
        coverage_pct: 100,
        state: "complete",
      },
      apim: {
        app_observed_requests: 2404,
        apim_governed_requests: 2214,
        unmatched_metric_records: 11,
        coverage_pct: 92.1,
        state: "reconciliation_pending",
        gateway_unmatched: {
          scope: "unattributed",
          window: { from: "2026-07-12T00:00:00Z", to: "2026-08-11T23:59:59Z" },
          linked_requests: 2214,
          unmatched_gateway_errors: {
            total: 11,
            client_error_4xx: 7,
            server_error_5xx: 4,
          },
          data_source: "apim_gateway_logs",
          updated_at: "2026-08-07T07:58:00Z",
          note: "网关侧未关联到任何应用运行的 4xx/5xx 聚合证据；无法可靠归属租户或工作区，按 unattributed/system 范围统计，不计入请求账本、错误率或成本。",
        },
      },
    },
    metrics: {
      requests: 2404,
      tokens: {
        input: 138810535,
        output: 23738799,
        cached_input: 13785603,
        reasoning: 3113752,
        total: 165664006,
        known_requests: 2404,
        unknown_requests: 0,
      },
      estimated_cost: {
        amount: 493.88,
        priced_requests: 2248,
        unpriced_requests: 156,
        status: "partial",
      },
      budget: {
        amount: 600,
        used_amount: 493.88,
        usage_pct: 82.31,
        status: "partial",
        source: "daily_cost_budget",
      },
      latency: { p50_ms: 2133, p95_ms: 3478, known_requests: 2404 },
      error_rate_pct: 3.54,
      success_rate_pct: 96.46,
      cache_hit_rate_pct: 18.64,
      cache: {
        eligible_requests: 1921,
        hit: 358,
        miss: 1563,
        bypassed: 361,
        unavailable: 122,
        avoided_tokens: 13785603,
        estimated_savings: 35.74,
        data_status: "partial",
      },
      apim_coverage_pct: 92.1,
    },
  },
  trend: {
    bucket: "day",
    items: [
      {
        bucket: "2026-08-01T00:00:00Z",
        requests: 72,
        tokens: {
          input: 3993600,
          output: 686917,
          cached_input: 378116,
          reasoning: 83564,
          total: 4764081,
        },
        estimated_cost: 11.98,
        p95_latency_ms: 3180,
        cache: {
          eligible_requests: 55,
          hit: 10,
          miss: 45,
          bypassed: 13,
          unavailable: 4,
          avoided_tokens: 378116,
          estimated_savings: 1.04,
          data_status: "available",
        },
        data_status: "available",
      },
      {
        bucket: "2026-08-02T00:00:00Z",
        requests: 84,
        tokens: {
          input: 4905396,
          output: 842246,
          cached_input: 437782,
          reasoning: 113758,
          total: 5861400,
        },
        estimated_cost: 17.75,
        p95_latency_ms: 3610,
        cache: {
          eligible_requests: 67,
          hit: 12,
          miss: 55,
          bypassed: 13,
          unavailable: 4,
          avoided_tokens: 437782,
          estimated_savings: 1.28,
          data_status: "partial",
        },
        data_status: "partial",
      },
      {
        bucket: "2026-08-03T00:00:00Z",
        requests: 75,
        tokens: {
          input: 4189455,
          output: 734618,
          cached_input: 468504,
          reasoning: 91964,
          total: 5016037,
        },
        estimated_cost: 16.69,
        p95_latency_ms: 3520,
        cache: {
          eligible_requests: 59,
          hit: 11,
          miss: 48,
          bypassed: 13,
          unavailable: 3,
          avoided_tokens: 468504,
          estimated_savings: 1.31,
          data_status: "partial",
        },
        data_status: "partial",
      },
      { bucket: "2026-08-04T00:00:00Z", requests: 87, tokens: { input: 5112939, output: 866698, cached_input: 551036, reasoning: 111520, total: 6091157 }, estimated_cost: 18.29, p95_latency_ms: 3740, cache: { eligible_requests: 69, hit: 13, miss: 56, bypassed: 13, unavailable: 5, avoided_tokens: 551036, estimated_savings: 1.52, data_status: "partial" }, data_status: "partial" },
      { bucket: "2026-08-05T00:00:00Z", requests: 68, tokens: { input: 3771868, output: 664943, cached_input: 442463, reasoning: 89417, total: 4526228 }, estimated_cost: 11.26, p95_latency_ms: 3360, cache: { eligible_requests: 55, hit: 11, miss: 44, bypassed: 10, unavailable: 3, avoided_tokens: 442463, estimated_savings: 1.16, data_status: "partial" }, data_status: "partial" },
      { bucket: "2026-08-06T00:00:00Z", requests: 80, tokens: { input: 4607940, output: 780624, cached_input: 475138, reasoning: 102908, total: 5491472 }, estimated_cost: 16.66, p95_latency_ms: 3650, cache: { eligible_requests: 63, hit: 12, miss: 51, bypassed: 13, unavailable: 4, avoided_tokens: 475138, estimated_savings: 1.34, data_status: "partial" }, data_status: "partial" },
      { bucket: "2026-08-07T00:00:00Z", requests: 86, tokens: { input: 3974310, output: 639000, cached_input: 386847, reasoning: 98550, total: 4712780 }, estimated_cost: 14.4, p95_latency_ms: 4120, cache: { eligible_requests: 70, hit: 10, miss: 60, bypassed: 9, unavailable: 7, avoided_tokens: 386847, estimated_savings: 1.08, data_status: "partial" }, data_status: "partial" },
      { bucket: "2026-08-08T00:00:00Z", requests: 89, tokens: { input: 5228040, output: 901300, cached_input: 708240, reasoning: 120800, total: 6250140 }, estimated_cost: 20.14, p95_latency_ms: 3820, cache: { eligible_requests: 72, hit: 17, miss: 55, bypassed: 12, unavailable: 5, avoided_tokens: 708240, estimated_savings: 1.92, data_status: "available" }, data_status: "available" },
      { bucket: "2026-08-09T00:00:00Z", requests: 77, tokens: { input: 4312030, output: 748900, cached_input: 603670, reasoning: 98800, total: 5159730 }, estimated_cost: 16.87, p95_latency_ms: 3410, cache: { eligible_requests: 63, hit: 15, miss: 48, bypassed: 10, unavailable: 4, avoided_tokens: 603670, estimated_savings: 1.66, data_status: "available" }, data_status: "available" },
      { bucket: "2026-08-10T00:00:00Z", requests: 94, tokens: { input: 5564010, output: 944880, cached_input: 801220, reasoning: 133700, total: 6642590 }, estimated_cost: 21.76, p95_latency_ms: 3650, cache: { eligible_requests: 78, hit: 20, miss: 58, bypassed: 11, unavailable: 5, avoided_tokens: 801220, estimated_savings: 2.21, data_status: "available" }, data_status: "available" },
      { bucket: "2026-08-11T00:00:00Z", bucket_status: "in_progress", requests: 31, tokens: { input: 1719060, output: 289420, cached_input: 312400, reasoning: 43100, total: 2051580 }, estimated_cost: 6.42, p95_latency_ms: 3290, cache: { eligible_requests: 25, hit: 8, miss: 17, bypassed: 4, unavailable: 2, avoided_tokens: 312400, estimated_savings: 0.88, data_status: "partial" }, data_status: "partial" },
    ],
  },
  departments: {
    items: [
      {
        key: "Operations",
        requests: 618,
        tokens: 42100098,
        estimated_cost: 125.71,
        error_rate_pct: 3.4,
        p95_latency_ms: 3520,
        cache_hit_rate_pct: 18.8,
        data_status: "available",
      },
      {
        key: "AI Platform",
        requests: 604,
        tokens: 41809703,
        estimated_cost: 124.49,
        error_rate_pct: 3.6,
        p95_latency_ms: 3470,
        cache_hit_rate_pct: 18.5,
        data_status: "partial",
      },
      { key: "Delivery", requests: 582, tokens: 40368054, estimated_cost: 122.47, error_rate_pct: 3.8, p95_latency_ms: 3610, cache_hit_rate_pct: 18.2, data_status: "available" },
      { key: "Finance", requests: 600, tokens: 41386151, estimated_cost: 121.2, error_rate_pct: 3.3, p95_latency_ms: 3340, cache_hit_rate_pct: 19.1, data_status: "available" },
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
      generated_at: "2026-08-07T07:55:00Z",
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
      generated_at: "2026-08-07T07:50:00Z",
      draft_suggestions: [],
    },
  },
  filters: {
    departments: ["Operations", "AI Platform", "Delivery", "Finance"],
    workspaces: ["Commerce Insights", "Finance Forecast", "IT Governance"],
    agents: ["分析协调 Agent", "财务洞察 Agent", "风险审阅 Agent"],
    models: ["gpt-5.6-terra", "gpt-5.1", "deepseek-v4-flash"],
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
          apim_governed_requests: 2214,
          coverage_pct: 92.1,
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
    departments: {
      items: bootstrapPayload.departments.items,
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
          sample_count: 2404,
          observed_at: NOW,
          evidence_refs: ["req_slow_000001"],
          evidence_state: "observed",
        },
        {
          policy_type: "cache_hit_rate",
          title: "缓存命中率偏低",
          severity: "warning",
          status: "open",
          observed_value: 18.64,
          threshold_value: 20,
          sample_count: 1921,
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
          sample_count: 2404,
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
  case_story: {
    title: "运营自动化测算",
    status: "estimated",
    summary: "假设每月节省 40 小时，按 50 USD/小时折算，并计入 1,000 USD 的避免损失或新增收益。",
    boundary: "情景参数用于展示投入价值，业务结果验证前不计为已实现 ROI。",
    assumptions: [
      { id: "hours_saved", label: "每月节省工时", value: 40, unit: "小时/月" },
      { id: "hourly_value", label: "小时价值", value: 50, unit: "USD" },
      { id: "avoided_loss_or_revenue", label: "避免损失或新增收益", value: 1000, unit: "USD" },
      { id: "implementation_cost", label: "实施投入", value: 6000, unit: "USD" },
      { id: "monthly_fixed_cost", label: "月度固定运营成本", value: 200, unit: "USD/月" },
      { id: "model_cost", label: "月度模型成本", value: 450, unit: "USD/月" },
      { id: "evaluation_months", label: "评估周期", value: 12, unit: "月" },
    ],
  },
  metrics: [
    { id: "monthly_benefit", label: "月度收益", value: 3000, unit: "USD", status: "estimated", explanation: "来自情景测算，非已验证业务结果。" },
    { id: "monthly_total_cost", label: "月度总成本", value: 1150, unit: "USD", status: "estimated", explanation: "包含实施摊销、固定成本与当前模型估算成本。" },
    { id: "monthly_net_benefit", label: "月度净收益", value: 1850, unit: "USD", status: "estimated", explanation: "月度收益减去同一情景口径下的月度总成本。" },
    { id: "roi_ratio", label: "ROI 比率", value: 1.6087, unit: "ratio", status: "estimated", explanation: "情景测算比率，不能替代已验证业务结果。" },
  ],
  value_bridge: {
    formula_revision: "dataforge-roi-v1",
    scenario_id: "roi_scenario_demo0001",
    payback_months: 2.6,
    items: [
      { id: "monthly_benefit", label: "月度收益", value: 3000, unit: "USD", status: "estimated", explanation: "情景测算中的月度收益。" },
      { id: "monthly_total_cost", label: "AI 运营总投入", value: -1150, unit: "USD", status: "estimated", explanation: "价值桥中的成本扣减项。" },
      { id: "monthly_net_benefit", label: "月度净收益", value: 1850, unit: "USD", status: "estimated", explanation: "月度收益减去 AI 运营总投入。" },
    ],
  },
  evidence_maturity: {
    score_pct: 75,
    formula_revision: "roi-evidence-maturity-v1",
    stages: [
      { id: "investment", label: "投入", value: 1150, unit: "USD", status: "estimated", evidence_count: 2248, evidence_refs: ["req_priced_000001"], complete: true },
      { id: "usage", label: "使用", value: 2404, unit: "次调用", status: "observed", evidence_count: 2404, evidence_refs: ["req_cache_000001"], complete: true },
      { id: "output", label: "产出", value: 186, unit: "个产物", status: "observed", evidence_count: 186, evidence_refs: ["req_slow_000001"], complete: true },
      { id: "outcome", label: "业务结果", value: 2, unit: "项结果", status: "partial", evidence_count: 2, evidence_refs: ["req_outcome_000001"], evidence_gap: "业务结果仍需独立复核后才能计入已验证 ROI。", complete: false },
    ],
  },
  unit_economics_trend: [
    { id: "cost-per-call", period: "8月5日", label: "每次成功调用成本", value: 0.1706, unit: "USD", status: "estimated" },
    { id: "cost-per-call", period: "8月6日", label: "每次成功调用成本", value: 0.2143, unit: "USD", status: "estimated" },
    { id: "cost-per-call", period: "8月7日", label: "每次成功调用成本", value: 0.1735, unit: "USD", status: "estimated" },
  ],
  forecast_validation: {
    status: "estimated",
    target: "cost_per_successful_request",
    unit: "USD/次成功调用",
    sample_count: 10,
    train_count: 7,
    validation_count: 3,
    mse: 0.000004,
    rmse: 0.002,
    mae: 0.0016,
    r2: 0.82,
    baseline_mse: 0.000016,
    improvement_pct: 75,
    method_revision: "linear-holdout-v1",
  },
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
      monthly_total_cost: 1150,
      monthly_net_benefit: 1850,
      roi_ratio: 1.6087,
      payback_months: 2.6,
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
    signal: { metric: "latency_ms", value: 6200, unit: "ms" },
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
    signal: { metric: "cache_state", value: "miss", unit: "state" },
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
    signal: { metric: "pricing_status", value: "unpriced", unit: "status" },
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
    signal: { metric: "request_status", value: "failed", unit: "status" },
    latency_ms: 980,
    cache_state: "bypassed",
    status: "failed",
    error_category: "provider_5xx",
    visible_answer_summary: "调用在模型执行阶段失败，未生成业务侧回答。",
    technical_refs: { request_ref: "req_error_000001", run_id: "run_error_000001" },
  },
  {
    request_ref: "req_budget_000001",
    request_name: "Finance · 当日预算 · 消耗提醒",
    operation: "成本预算复核",
    signal: { metric: "预算使用率", value: 86.4, unit: "percent" },
    latency_ms: 1240,
    cache_state: "bypassed",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "当日估算成本已超过提醒阈值，尚未触发任何自动限制。",
    technical_refs: { request_ref: "req_budget_000001", run_id: "run_budget_000001" },
  },
  {
    request_ref: "req_token_000001",
    request_name: "AI Platform · 深度分析 · Token 突增",
    operation: "深度分析",
    signal: { metric: "tokens_total", value: 31580, unit: "token" },
    latency_ms: 3380,
    cache_state: "miss",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "本小时深度分析用量高于过去七天相同时段基线。",
    technical_refs: { request_ref: "req_token_000001", run_id: "run_token_000001" },
  },
  {
    request_ref: "req_coverage_000001",
    request_name: "Operations · 调用入口 · 覆盖复核",
    operation: "统一入口覆盖复核",
    signal: { metric: "gateway_coverage", value: "unmanaged", unit: "state" },
    latency_ms: 1120,
    cache_state: "bypassed",
    status: "succeeded",
    error_category: null,
    visible_answer_summary: "发现少量应用侧可见但未经过统一治理入口的调用。",
    technical_refs: { request_ref: "req_coverage_000001", run_id: "run_coverage_000001" },
  },
];


function riskDecisionPayload(baseVersion = "cache-policy-v1", { postScan = false } = {}) {
  const initialOpportunities = [
    { opportunity_id: "opp-latency", anomaly_id: "anom-latency", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "p95_latency", risk_domain: "experience", title: "响应时延优化", recommendation: "拆分大批量分析并复核高时延模型路由。", impact: "high", confidence: "high", effort: "high", sample_count: 60, evidence_refs: ["req_slow_000001"], expected_impact: { status: "estimated", value: 1.4, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-cache", anomaly_id: "anom-cache", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "cache_hit_rate", risk_domain: "efficiency", title: "缓存效率优化", recommendation: "统一重复分析的缓存键并复核有效期。", impact: "medium", confidence: "high", effort: "low", sample_count: 54, evidence_refs: ["req_cache_000001"], expected_impact: { status: "estimated", value: 0.0048, currency: "USD" }, base_version: baseVersion },
    { opportunity_id: "opp-unpriced", anomaly_id: "anom-unpriced", anomaly_status: "acknowledged", applicable_actions: ["suppress"], policy_type: "unpriced_requests", risk_domain: "cost", title: "计价覆盖补齐", recommendation: "为新接入模型补齐官方价目映射。", impact: "medium", confidence: "medium", effort: "medium", sample_count: 30, evidence_refs: ["req_unpriced_001"], expected_impact: { status: "estimated", value: 0.0026, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-error", anomaly_id: "anom-error", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "error_rate", risk_domain: "governance", title: "调用成功率改善", recommendation: "按失败类别和调用来源修复错误。", impact: "high", confidence: "medium", effort: "medium", sample_count: 24, evidence_refs: ["req_error_000001"], expected_impact: { status: "estimated", value: 0.0031, currency: "USD" }, base_version: "remediation-template-v1" },
  ];
  const postScanOpportunities = [
    ...initialOpportunities.filter((item) => item.opportunity_id !== "opp-error"),
    { opportunity_id: "opp-budget", anomaly_id: "anom-budget", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "daily_cost_budget", risk_domain: "cost", title: "预算消耗复核", recommendation: "复核主要成本贡献来源和模型路由。", impact: "high", confidence: "high", effort: "medium", sample_count: 34, evidence_refs: ["req_budget_000001"], expected_impact: { status: "estimated", value: 0.0038, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-token", anomaly_id: "anom-token", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "token_spike", risk_domain: "efficiency", title: "Token 用量复核", recommendation: "检查大上下文和重复分析调用。", impact: "medium", confidence: "high", effort: "low", sample_count: 18, evidence_refs: ["req_token_000001"], expected_impact: { status: "estimated", value: 0.0022, currency: "USD" }, base_version: "remediation-template-v1" },
    { opportunity_id: "opp-coverage", anomaly_id: "anom-coverage", anomaly_status: "open", applicable_actions: ["acknowledge", "suppress"], policy_type: "apim_coverage", risk_domain: "governance", title: "统一入口覆盖复核", recommendation: "核对未纳管调用来源并补齐入口治理。", impact: "medium", confidence: "high", effort: "medium", sample_count: 146, evidence_refs: ["req_coverage_000001"], expected_impact: { status: "estimated", value: 0.0019, currency: "USD" }, base_version: "remediation-template-v1" },
  ];
  const opportunities = postScan ? postScanOpportunities : initialOpportunities;
  const levels = { low: 1, medium: 2, high: 3 };
  const riskDomains = ["cost", "experience", "efficiency", "governance"].map((id) => ({
    id,
    count: opportunities.filter((item) => item.risk_domain === id).length,
  }));
  return {
    scope: { workspace_ids: ["demo-corpus"], workspace_count: 1 },
    window: bootstrapPayload.window,
    freshness: { generated_at: NOW, query_cache: { provider: "redis", status: "miss" } },
    data_status: "partial",
    decision: { state: "prioritized", title: "已按影响与证据确定优化优先级", summary: "风险以运营严重度、证据置信度、评估样本量和可追溯证据展示，不使用复合风险分数。", evidence_state: "observed" },
    risk_domains: riskDomains,
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
    portfolio_metadata: { x_axis: "effort", y_axis: "value_impact", size: "sample_count", color: "risk_domain" },
    selected_evidence_summaries: riskEvidence,
    insight: { title: "优先处理慢响应并验证缓存策略", summary: "高时延影响范围最大；缓存未命中具有可量化的成本改善空间。", status: "observed" },
    drafts: [],
    governance_capability: { read_enabled: true, draft_enabled: true, actions_enabled: false, typed_executors: ["cache_policy"] },
  };
}


function riskScanPayload() {
  const specs = [
    ["error_rate", "triggered", "critical", 8.3, 5, "%", 24, 20, "req_error_000001", "失败请求占比已达到当前策略阈值。", "按失败类别与模型路由复核错误来源。"],
    ["p95_latency", "triggered", "warning", 6200, 2000, "ms", 28, 20, "req_slow_000001", "P95 响应时间高于当前体验阈值。", "拆分大批量分析并复核高时延模型路由。"],
    ["daily_cost_budget", "triggered", "warning", 86.4, 80, "%", 34, 1, "req_budget_000001", "当日估算成本已达到预算提醒区间。", "复核主要成本贡献来源和模型路由。"],
    ["token_spike", "triggered", "warning", 2.36, 2, "x", 18, 1, "req_token_000001", "当前小时 Token 用量超过历史基线倍数。", "检查大上下文和重复分析调用。"],
    ["apim_coverage", "triggered", "warning", 92.8, 95, "%", 146, 1, "req_coverage_000001", "统一入口治理覆盖率低于当前策略要求。", "核对未纳管调用来源并补齐入口治理。"],
    ["unpriced_requests", "triggered", "warning", 6.2, 5, "%", 146, 1, "req_unpriced_001", "未计价请求占比高于当前策略阈值。", "补齐新接入模型的官方价目映射。"],
    ["cache_hit_rate", "triggered", "warning", 18.5, 20, "%", 54, 20, "req_cache_000001", "缓存命中率低于当前策略要求。", "统一重复分析缓存键并复核有效期。"],
  ];
  const findings = specs.map(([policy_type, status, severity, observed_value, threshold_value, unit, sample_count, minimum_samples, requestRef, reason, recommendation]) => ({
    policy_type, status, severity, observed_value, threshold_value, unit, sample_count, minimum_samples,
    reason, recommendation, evidence_refs: [requestRef], rule_revision: "policy_demo_v3",
  }));
  return {
    scan_ref: "rscan_0123456789abcdef0123456789abcdef",
    status: "completed",
    policy_revision: "policy_demo_v3",
    ledger_revision: "ledger_demo_v2",
    rules_evaluated: 7,
    rules_triggered: 7,
    rules_clear: 0,
    rules_insufficient: 0,
    request_sample_count: 146,
    evidence_coverage_pct: 100,
    findings,
    evidence_sets: findings.map((item) => ({
      subject_type: "risk",
      subject_id: item.policy_type,
      policy_type: item.policy_type,
      state: "observed",
      items: item.evidence_refs.map((request_ref) => ({ request_ref })),
    })),
    started_at: "2026-08-03T02:30:00Z",
    finished_at: "2026-08-03T02:30:01Z",
    governance: { mode: "read_only_scan", automatic_actions: false, explanation_agent_invoked: false },
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
    created_at: "2026-08-07T07:30:00Z",
    updated_at: "2026-08-07T07:35:00Z",
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
    memberBudgetEmailState: options.memberBudgetEmailState || "accepted",
    memberBudgetActiveDisabled: Boolean(options.memberBudgetActiveDisabled),
    memberBudgetNotificationState: options.memberBudgetNotificationState || "configured",
    memberBudgetAlertsState: options.memberBudgetAlertsState || "available",
    memberBudgetRecipientEmail: "demo-admin@example.test",
    memberBudgetTested: true,
    memberBudgetDeliveryState: "delivered",
    memberBudgetNotificationEnabled: true,
    memberBudgetDisabled: false,
    roiScenarioConflictOnce: Boolean(options.roiScenarioConflictOnce),
    remediationReviewConflictOnce: Boolean(options.remediationReviewConflictOnce),
    failRoiRefresh: Boolean(options.failRoiRefresh),
    riskDecisionRefreshFailuresRemaining: Math.max(0, Number(options.riskDecisionRefreshFailures || 0)),
    decisionDelayMs: Number(options.decisionDelayMs || 0),
    capabilityDelayMs: Number(options.capabilityDelayMs || 0),
    dashboardDelayMs: Number(options.dashboardDelayMs || 0),
    dashboardUnavailable: Boolean(options.dashboardUnavailable),
    assistantValidationFailuresRemaining: Math.max(0, Number(options.assistantValidationFailures || 0)),
    dashboardFailuresRemaining: Math.max(0, Number(options.dashboardFailures || 0)),
    failDashboardFallback: false,
    delayNextRoiRefreshMs: 0,
    riskBaseVersion: "cache-policy-v1",
    riskScanRuns: 0,
    remediation: null,
    calls: {
      bootstrap: 0,
      roiDecision: 0,
      riskDecision: 0,
      remediationCreate: 0,
      remediationReview: 0,
      dashboard: 0,
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
        model_cost: 450,
        evaluation_months: 12,
        evidence_revision: 1,
      },
      result: {
        status: "estimated",
        currency: "USD",
        monthly_benefit: 3000,
        implementation_amortization: 500,
        monthly_total_cost: 1150,
        monthly_net_benefit: 1850,
        roi_ratio: 1.6087,
        payback_months: 2.6,
        formula_revision: "dataforge-roi-v1",
      },
      formula_revision: "dataforge-roi-v1",
    }],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    calls.push({ method: request.method(), path, search: url.search, body: request.postData() || "" });
    if (path === "/api/finops/bootstrap") control.calls.bootstrap += 1;
    if (path === "/api/workspaces/demo-corpus/dashboard") control.calls.dashboard += 1;
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

    if (
      path === "/api/finops/risk/decision"
      && url.searchParams.get("refresh") === "1"
      && control.riskDecisionRefreshFailuresRemaining > 0
    ) {
      control.riskDecisionRefreshFailuresRemaining -= 1;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "优先事项更新失败，请重试" }),
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

    if (
      path === "/api/workspaces/demo-corpus/dashboard"
      && (control.dashboardUnavailable || control.dashboardFailuresRemaining > 0)
    ) {
      control.dashboardFailuresRemaining = Math.max(0, control.dashboardFailuresRemaining - 1);
      control.failDashboardFallback = true;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Dashboard bootstrap unavailable" }),
      });
      return;
    }
    if (
      (control.dashboardUnavailable || control.failDashboardFallback)
      && path === "/api/workspaces/demo-corpus"
    ) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Workspace fallback unavailable" }),
      });
      return;
    }

    if (path === "/api/workspaces/demo-corpus/access") {
      body = { allowed: true, role: "owner", workspace_id: "demo-corpus" };
    } else if (path === "/api/workspaces/demo-corpus/governance/capabilities") {
      if (control.capabilityDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, control.capabilityDelayMs));
      }
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
      control.failDashboardFallback = false;
      if (control.dashboardDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, control.dashboardDelayMs));
      }
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
      body = options.trendPayload
        ? { ...bootstrapPayload, trend: options.trendPayload }
        : control.demoCompleteness ? demoCompletenessBootstrapPayload() : bootstrapPayload;
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
            last_test_accepted_at: control.memberBudgetDeliveryState === "not_tested" ? null : NOW,
            last_test_delivery_state: control.memberBudgetDeliveryState,
            last_test_delivery_checked_at: control.memberBudgetDeliveryState === "not_tested" ? null : NOW,
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
      control.memberBudgetDeliveryState = "not_tested";
      body = {
        item: {
          recipient_actor_ref: "member-safe",
          recipient_email: control.memberBudgetRecipientEmail,
          sender_display_name: "DataForge",
          subject_template: "{{member_name}} 预算提醒",
          body_template: "{{estimated_spend}} / {{budget_amount}}",
          enabled: control.memberBudgetNotificationEnabled,
          test_email_succeeded_at: null,
          last_test_accepted_at: null,
          last_test_delivery_state: "not_tested",
          last_test_delivery_checked_at: null,
          revision: 3,
        },
        freshness: "recorded",
        coverage: "request_estimated_cost",
        data_status: "complete",
        currency: "USD",
      };
    } else if (path === "/api/finops/notification-settings/test-email" && request.method() === "POST") {
      if (control.memberBudgetEmailState === "accepted") control.memberBudgetDeliveryState = "accepted";
      body = control.memberBudgetEmailState === "accepted"
        ? { state: "accepted", accepted_at: NOW, safe_error_category: null }
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
    } else if (/^\/api\/model-providers\/[^/]+\/rotate-secret$/.test(path)) {
      const providerId = decodeURIComponent(path.split("/")[3]);
      const existing = control.providerItems.find((item) => item.provider_id === providerId);
      if (existing) {
        existing.secret_status = "stored";
        existing.connection_state = "connected";
        existing.connection_stage = "completed";
        existing.stage_durations_ms = {
          secret_read: 2,
          endpoint_resolution: 4,
          tls_connect: 7,
          provider_auth: 12,
          minimal_inference: 90,
          model_discovery: 16,
        };
        existing.safe_error_category = null;
        existing.last_tested_at = NOW;
        existing.revision += 1;
      }
      body = { provider_id: providerId };
    } else if (/^\/api\/model-providers\/[^/]+\/(govern|suspend)$/.test(path)) {
      const [, , , encodedProviderId, action] = path.split("/");
      const providerId = decodeURIComponent(encodedProviderId);
      const existing = control.providerItems.find((item) => item.provider_id === providerId);
      if (!existing) {
        status = 404;
        body = { detail: "provider not found" };
      } else {
        const governed = action === "govern";
        existing.governance_state = governed ? "governed" : "pending";
        existing.revision += 1;
        existing.route_eligibility = {
          state: governed ? "selectable" : "governance_required",
          selectable: governed,
          can_govern: !governed,
          reason: governed ? null : "governance_required",
          eligible_model_count: existing.available_models.filter((model) => model.support_state === "supported" && model.price_key).length,
        };
        body = { provider_id: providerId, revision: existing.revision };
      }
    } else if (path === "/api/workspaces/demo-corpus/governance/model-routing") {
      const providerRoutes = control.providerItems
        .filter((item) => item.provider_type === "deepseek")
        .flatMap((item) => item.available_models
          .filter((model) => ["supported", "unpriced"].includes(model.support_state))
          .map((model) => ({
            id: `ds_${item.provider_id}_${model.model_id}`,
            deployment: model.model_id,
            model_id: model.model_id,
            provider_id: item.provider_id,
            provider_type: "deepseek",
            provider_label: item.display_name,
            label: model.display_name,
            capabilities: model.capabilities,
            official_price_key: model.price_key,
            pricing_state: model.price_key ? "priced" : "unpriced",
            health_state: item.connection_state,
            governance_state: item.governance_state,
            selectable: item.route_eligibility?.selectable === true && Boolean(model.price_key),
            unavailable_reason: model.price_key ? item.route_eligibility?.reason : "official_pricing_required",
          })));
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
          ...providerRoutes,
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
            "df-finops-analyst": {
              primary_route_id: "terra",
              fallback_route_id: "analysis",
            },
            "df-roi-analyst": {
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
          {
            price_key: "deepseek:deepseek-v4-flash:official",
            provider: "deepseek",
            official_model: "deepseek-v4-flash",
            display_name: "DeepSeek V4 Flash",
            currency: "USD",
            input_per_million: 0.14,
            output_per_million: 0.28,
            cached_input_per_million: 0.0028,
            revision: "deepseek-2026-07-28-v1",
            source_url: "https://api-docs.deepseek.com/quick_start/pricing/",
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
        scope: "tenant",
        can_manage: true,
        authorization_source: "entra_tenant_pricing_admin",
      };
    } else if (path === "/api/finops/requests") {
      body = {
        ...bootstrapPayload,
        items: [
          {
            request_ref: "req_aaaaaaaaaaaa",
            occurred_at: "2026-08-07T02:42:00Z",
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
          occurred_at: "2026-08-07T02:42:00Z",
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
      || path === "/api/finops/requests/req_priced_000001"
      || path === "/api/finops/requests/req_unpriced_001"
      || path === "/api/finops/requests/req_error_000001"
      || path === "/api/finops/requests/req_budget_000001"
      || path === "/api/finops/requests/req_token_000001"
      || path === "/api/finops/requests/req_coverage_000001"
      || path === "/api/finops/requests/req_outcome_000001"
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
        req_priced_000001: {
          operation: "成本归因",
          request: "使用已配置价目的模型执行客户机会分析",
          response: "分析已完成，本次 Token 用量已按生效价目表完成估算成本归因。",
          status: "succeeded",
          latency: 1480,
          cache: "miss",
          cost: 0.0128,
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
        req_budget_000001: {
          operation: "成本预算复核",
          request: "检查当日模型成本是否接近预算提醒阈值",
          response: "当日估算成本已超过提醒阈值，尚未触发任何自动限制。",
          status: "succeeded",
          latency: 1240,
          cache: "bypassed",
          cost: 0.0186,
          error: null,
        },
        req_token_000001: {
          operation: "深度分析",
          request: "对本批客户反馈执行深度归因与机会分析",
          response: "本小时深度分析用量高于过去七天相同时段基线。",
          status: "succeeded",
          latency: 3380,
          cache: "miss",
          cost: 0.0248,
          error: null,
        },
        req_coverage_000001: {
          operation: "统一入口覆盖复核",
          request: "复核本期调用是否全部经过统一治理入口",
          response: "发现少量应用侧可见但未经过统一治理入口的调用。",
          status: "succeeded",
          latency: 1120,
          cache: "bypassed",
          cost: 0.0034,
          error: null,
        },
        req_outcome_000001: {
          operation: "业务结果复核",
          request: "复核本期分析建议是否形成可验证的业务结果",
          response: "已记录业务结果证据，仍需独立审核后计入已验证 ROI。",
          status: "succeeded",
          latency: 1460,
          cache: "bypassed",
          cost: 0.0041,
          error: null,
        },
      };
      const profile = profiles[requestRef];
      body = {
        ...bootstrapPayload,
        display: {
          name: `AI Platform · ${profile.operation} · 8月7日 14:10`,
          operation: profile.operation,
          occurred_at: "2026-08-07T06:10:00Z",
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
        { key: "demo-corpus", requests: 2404, tokens: 165664006, estimated_cost: 493.88, error_rate_pct: 3.54, p95_latency_ms: 3478, cache_hit_rate_pct: 18.64, data_status: "partial" },
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
          { key: "Product Architect", requests: 402, tokens: 28264062, estimated_cost: 85.94, error_rate_pct: 3.2, success_rate_pct: 96.8, p95_latency_ms: 3610, cache_hit_rate_pct: 18.9, data_status: "available" },
          { key: "Support Triage", requests: 407, tokens: 27438560, estimated_cost: 83.32, error_rate_pct: 3.7, success_rate_pct: 96.3, p95_latency_ms: 3440, cache_hit_rate_pct: 18.2, data_status: "partial" },
          { key: "Delivery Engineer", requests: 401, tokens: 27313712, estimated_cost: 82.8, error_rate_pct: 3.5, success_rate_pct: 96.5, p95_latency_ms: 3520, cache_hit_rate_pct: 19.1, data_status: "available" },
          { key: "Close Analyst", requests: 396, tokens: 27682606, estimated_cost: 81.39, error_rate_pct: 3.8, success_rate_pct: 96.2, p95_latency_ms: 3690, cache_hit_rate_pct: 18.4, data_status: "partial" },
          { key: "Compliance Reviewer", requests: 398, tokens: 27775009, estimated_cost: 80.92, error_rate_pct: 3.4, success_rate_pct: 96.6, p95_latency_ms: 3410, cache_hit_rate_pct: 18.6, data_status: "available" },
          { key: "Checkout Copilot", requests: 400, tokens: 27190057, estimated_cost: 79.5, error_rate_pct: 3.6, success_rate_pct: 96.4, p95_latency_ms: 3560, cache_hit_rate_pct: 18.7, data_status: "available" },
        ],
        models: [
          { key: "gpt-5.1", requests: 611, tokens: 42284821, estimated_cost: 272.2, error_rate_pct: 3.4, success_rate_pct: 96.6, p95_latency_ms: 3580, cache_hit_rate_pct: 18.5, token_composition: { input: 35000140, cached_input: 6814020, uncached_input: 28186120, output: 6114681, reasoning: 1170000, known_requests: 611, data_status: "available" }, data_status: "estimated" },
          { key: "gpt-5.6-terra", requests: 590, tokens: 40696870, estimated_cost: 109.7, error_rate_pct: 3.6, success_rate_pct: 96.4, p95_latency_ms: 3710, cache_hit_rate_pct: 18.8, token_composition: { input: 33192800, cached_input: 7202600, uncached_input: 25990200, output: 6324070, reasoning: 1180000, known_requests: 590, data_status: "available" }, data_status: "estimated" },
          { key: "deepseek-v4-flash", requests: 599, tokens: 41343442, estimated_cost: 72.01, error_rate_pct: 3.7, success_rate_pct: 96.3, p95_latency_ms: 3460, cache_hit_rate_pct: 18.6, token_composition: { input: 34601042, cached_input: 10242881, uncached_input: 24358161, output: 6742400, reasoning: null, known_requests: 599, data_status: "available" }, data_status: "estimated" },
          { key: "gpt-4.1-mini", requests: 604, tokens: 41338873, estimated_cost: 39.97, error_rate_pct: 3.5, success_rate_pct: 96.5, p95_latency_ms: 3370, cache_hit_rate_pct: 18.7, token_composition: { input: 34900873, cached_input: 5984400, uncached_input: 28916473, output: 6438000, reasoning: null, known_requests: 604, data_status: "partial" }, data_status: "estimated" },
        ],
      };
    } else if (path === "/api/finops/budgets") {
      body = {
        items: [{
          budget_id: "budget-a",
          name: "Commerce 月度预算",
          scope_type: "workspace",
          scope_id: "demo-corpus",
          amount: 600,
          currency: "USD",
          progress: {
            spent_amount: 493.88,
            usage_pct: 82.31,
            forecast_amount: 612.4,
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
      body = options.trendPayload || bootstrapPayload.trend;
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
    } else if (path === "/api/finops/assistant/bootstrap" && request.method() === "GET") {
      body = {
        conversation: { conversation_ref: "conversation-demo", updated_at: "2026-08-02T08:00:00Z" },
        messages: [{ role: "assistant", content: "上次分析已保留，可继续针对当前指标提问。" }],
        cache_status: "hit",
      };
    } else if (path === "/api/finops/assistant/conversations" && request.method() === "GET") {
      body = { items: [{ conversation_ref: "conversation-demo", updated_at: "2026-08-02T08:00:00Z" }], count: 1 };
    } else if (path === "/api/finops/assistant/conversations/conversation-demo/messages" && request.method() === "GET") {
      body = {
        items: [{ role: "assistant", content: "上次分析已保留，可继续针对当前指标提问。" }],
        count: 1,
      };
    } else if (path === "/api/finops/assistant/conversations/conversation-demo" && request.method() === "DELETE") {
      status = 204;
      body = {};
    } else if (path === "/api/finops/assistant/query") {
      if (control.assistantValidationFailuresRemaining > 0) {
        control.assistantValidationFailuresRemaining -= 1;
        await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({ detail: [{
            type: "string_pattern_mismatch",
            loc: ["body", "metric_context", "data_status"],
            msg: "String should match pattern",
            input: "estimated",
          }] }),
        });
        return;
      }
      const submitted = request.postDataJSON();
      const metricLabel = String(submitted?.metric_context?.label || "当前指标");
      const submittedRefs = Array.isArray(submitted?.metric_context?.evidence_refs)
        ? submitted.metric_context.evidence_refs.slice(0, 3)
        : [];
      const policyEvidence = {
        p95_latency: "req_slow_000001",
        cache_hit_rate: "req_cache_000001",
        unpriced_requests: "req_unpriced_001",
        error_rate: "req_error_000001",
        daily_cost_budget: "req_budget_000001",
        token_spike: "req_token_000001",
        apim_coverage: "req_coverage_000001",
      }[submitted?.metric_context?.policy_type];
      const metricId = String(submitted?.metric_context?.metric_id || "");
      const isCostQuestion = ["estimated_cost", "cost", "operations_cost"].includes(metricId);
      const metricEvidence = metricId.includes("error")
        ? "req_error_000001"
        : metricId.includes("latency") || metricId.includes("p95")
          ? "req_slow_000001"
          : metricId.includes("cost") || metricId.includes("price")
            ? "req_unpriced_001"
            : "req_cache_000001";
      const serviceSelectedRef = policyEvidence || metricEvidence;
      const assistantEvidenceRefs = submittedRefs.length
        ? (submittedRefs.includes(serviceSelectedRef) ? [serviceSelectedRef] : [])
        : [serviceSelectedRef];
      const assistantReady = assistantEvidenceRefs.length > 0;
      body = {
        status: assistantReady ? "ready" : "insufficient_data",
        conversation_ref: "conversation-demo",
        answer: assistantReady
          ? (isCostQuestion ? "本月估算成本为 $493.88，主要由 gpt-5.1 与高用量分析请求贡献。" : `${metricLabel}当前需要关注。`)
          : "当前指标缺少可复核证据，暂不能生成分析结论。",
        sections: assistantReady ? {
          conclusion: isCostQuestion
            ? "本月估算成本为 $493.88，gpt-5.1 贡献约 $272.20，是首要成本来源。"
            : `${metricLabel}当前需要关注。`,
          basis: isCostQuestion
            ? "30 天内记录 2,404 次调用，其中 2,248 次已匹配价目；成本和模型归因均来自请求级运行证据。"
            : "当前筛选范围内已有请求级运行证据和规则阈值。",
          impact: isCostQuestion
            ? "价目覆盖率为 93.51%，未计价请求会使总成本仍有低估可能；缓存命中率 18.64% 也限制了节省空间。"
            : "该信号会影响成本、体验或治理判断，应先复核影响范围。",
          recommendation: isCostQuestion
            ? "先下钻 gpt-5.1 的高成本请求，再验证重复分析的缓存键和有效期。"
            : "先查看关联证据，再在候选范围验证优化建议。",
          caveat: isCostQuestion
            ? "该金额按 DataForge 价目修订版估算，不等于云平台实际账单；未计价请求不按零成本处理。"
            : "这是基于当前运行证据的分析，不会自动执行生产变更。",
        } : null,
        evidence_state: assistantReady ? "observed" : "unavailable",
        evidence_refs: assistantEvidenceRefs,
        evidence_labels: assistantEvidenceRefs.map((_, index) => `${metricLabel}证据 ${index + 1}`),
        knowledge_citations: assistantReady
          ? [isCostQuestion
            ? "内部知识：DataForge 成本与计价方法 / 请求级估算成本"
            : "内部知识：DataForge 风险判定与证据手册 / 风险规则与代表证据"]
          : [],
        generation: assistantReady ? {
          model_id: "deepseek-v4-flash",
          provider_type: "deepseek",
          gateway_coverage: "app_observed",
          latency_ms: 345,
          provider_cache: {
            state: "partial_hit",
            hit_tokens: 80,
            miss_tokens: 40,
            hit_rate_pct: 66.67,
            evidence_state: "observed",
          },
        } : null,
        suggested_questions: assistantReady
          ? (isCostQuestion ? ["价目覆盖率如何影响成本可信度？", "哪些模型最适合优先优化？"] : ["与上一周期相比如何？"])
          : [],
      };
    } else if (path === "/api/service-readiness") {
      body = {
        workspace_id: "demo-corpus",
        generated_at: "2026-08-09T02:00:00Z",
        groups: {
          identity: { label: "身份与权限", items: [
            { key: "signed_in_identity", label: "登录身份", status: "ready", details: { role: "owner", source: "entra" } },
            { key: "group_governance", label: "群组权限解析", status: "ready", details: { state: "resolved" } },
          ] },
          data: { label: "数据服务", items: [
            { key: "blob", label: "工作区文件", status: "ready", details: { state: "ok", latency_ms: 12 } },
            { key: "search", label: "知识检索", status: "ready", details: { state: "ok", latency_ms: 18 } },
            { key: "cache", label: "查询缓存", status: "ready", details: { status: "ok", elapsed_ms: 3 } },
          ] },
          ai: { label: "AI 服务", items: [
            { key: "foundry", label: "主模型服务", status: "ready", details: { state: "ok", latency_ms: 42 } },
            { key: "external_models", label: "外部模型", status: "degraded", details: { configured: 1, connected: 0, governed: 0 } },
          ] },
          finops: { label: "成本与治理", items: [
            { key: "ledger", label: "运营账本", status: "ready", details: { persistence: "durable" } },
            { key: "pricing", label: "模型计价", status: "ready", details: { catalog_entries: 12, mapping_count: 5 } },
            { key: "risk_scan", label: "风险扫描", status: "ready", details: { rules_evaluated: 7, rules_triggered: 7, evidence_coverage_pct: 100 } },
          ] },
          background_jobs: { label: "后台任务", items: [
            { key: "finops_apim_reconciliation", label: "入口调用对账", status: "ready", last_completed_at: "2026-08-09T01:58:00Z", details: { rows_observed: 146, rows_written: 12, age_seconds: 120 } },
            { key: "finops_rollup", label: "运营指标聚合", status: "ready", last_completed_at: "2026-08-09T01:55:00Z", details: { rows_observed: 146, rows_written: 48, age_seconds: 300 } },
            { key: "finops_retention", label: "数据保留清理", status: "not_run", last_completed_at: null, details: {} },
          ] },
        },
      };
    } else if (path === "/api/finops/risk/scans/latest") {
      body = riskScanPayload();
    } else if (path === "/api/finops/risk/scans" && request.method() === "GET") {
      const latest = riskScanPayload();
      body = {
        items: [
          {
            scan_ref: latest.scan_ref,
            status: latest.status,
            policy_revision: latest.policy_revision,
            ledger_revision: latest.ledger_revision,
            rule_count: latest.rules_evaluated,
            rules_triggered: latest.rules_triggered,
            rules_clear: latest.rules_clear,
            rules_insufficient: latest.rules_insufficient,
            rules_unavailable: 0,
            request_sample_count: latest.request_sample_count,
            evidence_bound_findings: 7,
            evidence_coverage_pct: latest.evidence_coverage_pct,
            started_at: latest.started_at,
            finished_at: latest.finished_at,
          },
          {
            scan_ref: "rscan_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            status: "completed",
            rule_count: 7,
            rules_triggered: 4,
            rules_clear: 3,
            rules_insufficient: 0,
            rules_unavailable: 0,
            request_sample_count: 132,
            evidence_bound_findings: 7,
            evidence_coverage_pct: 100,
            started_at: "2026-08-02T02:30:00Z",
            finished_at: "2026-08-02T02:30:01Z",
          },
        ],
        count: 2,
        workspace_id: "demo-corpus",
      };
    } else if (path === "/api/finops/risk/scans" && request.method() === "POST") {
      status = 201;
      control.riskScanRuns += 1;
      body = riskScanPayload();
    } else if (/^\/api\/finops\/risk\/scans\/rscan_[a-f0-9]+$/.test(path)) {
      body = {
        ...riskScanPayload(),
        scan_ref: path.split("/").at(-1),
        started_at: "2026-08-02T02:30:00Z",
        finished_at: "2026-08-02T02:30:01Z",
      };
    } else if (path === "/api/finops/roi/decision") {
      body = roiDecisionPayload;
    } else if (path === "/api/finops/risk/decision") {
      body = riskDecisionPayload(control.riskBaseVersion, {
        postScan: control.riskScanRuns > 0 && url.searchParams.get("refresh") === "1",
      });
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
          { id: "investment", label: "投入", value: 493.88, unit: "USD", status: "estimated" },
          { id: "usage", label: "使用", value: 2404, unit: "次调用", status: "observed" },
          { id: "output", label: "产出", value: 186, unit: "次分析", status: "observed" },
          { id: "outcome", label: "业务结果", value: null, unit: "项已验证结果", status: "not_recorded" },
        ],
        unit_economics: {
          cost_per_successful_request: { label: "每次成功调用成本", value: 0.21297, currency: "USD", status: "estimated" },
          cost_per_analysis: { label: "每次分析成本", value: 2.65527, currency: "USD", status: "estimated" },
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
        cost: { total: 493.88, currency: "USD", status: "partial" },
        outcome_evidence: {
          status: "not_recorded",
          verified_outcome_event_ids: [],
        },
        lineage_complete: true,
      };
    } else if (path === "/api/workspaces/demo-corpus/governance/cost-value") {
      body = {
        workspace_id: "demo-corpus",
        cost_evidence: { total: 493.88, currency: "USD", status: "partial" },
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

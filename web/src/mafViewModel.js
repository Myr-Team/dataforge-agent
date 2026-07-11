export const MAF_EVENT_NAMES = new Set([
  "maf_plan",
  "maf_agent_started",
  "maf_agent_completed",
  "maf_agent_failed",
  "maf_branch_started",
  "maf_branch_joined",
  "maf_handoff",
  "maf_review",
  "maf_fallback",
]);

export const MAF_MODES = [
  { id: "direct", label: "直接响应" },
  { id: "concurrent_research", label: "并行研究" },
  { id: "specialist_handoff", label: "专家交接" },
  { id: "bounded_review", label: "限轮复核" },
];

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

function mafAgentId(value) {
  if (typeof value === "string") return value;
  return value?.agent_id || value?.id || value?.agent || "";
}

function mafArray(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function preferredArray(primary, key, fallbacks = []) {
  if (hasOwn(primary, key)) return mafArray(primary[key]);
  for (const [value, fallbackKey] of fallbacks) {
    if (hasOwn(value, fallbackKey)) return mafArray(value[fallbackKey]);
  }
  return [];
}

export function mafEventData(item) {
  const source = item && typeof item === "object" ? item : {};
  const data = source.data && typeof source.data === "object" ? source.data : {};
  const detail = source.detail && typeof source.detail === "object" ? source.detail : {};
  const { data: _data, detail: _detail, duration_ms: _inferredDuration, ...topLevel } = source;
  return { ...topLevel, ...data, ...detail };
}

export function mafRevisionNumber(data) {
  for (const key of ["round", "review_round", "revision"]) {
    if (data?.[key] == null || data[key] === "") continue;
    const explicit = Number(data[key]);
    if (Number.isFinite(explicit)) return explicit + (key === "revision" ? 1 : 0);
  }
  const revisionCode = mafArray(data?.reason_codes).find((code) => String(code).startsWith("revision:"));
  const revision = Number(String(revisionCode || "").split(":")[1]);
  return Number.isFinite(revision) ? revision + 1 : null;
}

export function mafStatusLabel(status) {
  const raw = String(status || "").trim();
  const key = raw.toLowerCase();
  if (key === "selected") return "已选择";
  if (["running", "started", "streaming"].includes(key)) return "进行中";
  if (["completed", "complete", "joined", "done", "success", "ok"].includes(key)) return "已完成";
  if (["failed", "fail", "error"].includes(key)) return "异常";
  if (key === "revision_requested") return "要求复修";
  if (key === "degraded") return "降级完成";
  if (key === "recorded") return "已记录";
  return raw || "未记录";
}

export function mafStatusTone(status) {
  const key = String(status || "").trim().toLowerCase();
  if (["completed", "complete", "joined", "done", "success", "ok", "完成", "已完成"].includes(key)) return "completed";
  if (["running", "started", "streaming", "pending", "进行中"].includes(key)) return "running";
  if (["failed", "fail", "error", "异常", "未通过"].includes(key)) return "failed";
  return "neutral";
}

function initialAgentState(id, persistedAgentMap) {
  const record = persistedAgentMap.get(id) || {};
  const metadata = record.metadata || {};
  return {
    id,
    status: record.status || "selected",
    durationMs: record.duration_ms ?? metadata.duration_ms ?? null,
    tokens: record.tokens || metadata.tokens || null,
    tools: mafArray(record.tool_names || record.tools || metadata.tool_names || metadata.tools),
    retries: record.retry_count ?? record.retries ?? metadata.retry_count ?? null,
    error: record.error_category || record.error || metadata.error_category || "",
  };
}

export function deriveMafViewModel(trace = [], persistedMaf = null) {
  const persisted = persistedMaf?.maf || persistedMaf || {};
  const events = (trace || [])
    .filter((item) => MAF_EVENT_NAMES.has(String(item?.event || item?.rawEvent || "").toLowerCase()))
    .map((item) => ({
      event: String(item.event || item.rawEvent).toLowerCase(),
      data: mafEventData(item),
    }));
  const plan = [...events].reverse().find((item) => item.event === "maf_plan")?.data || {};
  const planCollaboration = plan.collaboration && typeof plan.collaboration === "object" ? plan.collaboration : {};
  const persistedCollaboration = persisted.collaboration && typeof persisted.collaboration === "object" ? persisted.collaboration : {};
  const mode = plan.mode || plan.pattern || planCollaboration.pattern || persisted.mode || persistedCollaboration.pattern || "";

  const selectedFromData = preferredArray(plan, "selected_agents", [
    [planCollaboration, "selected_agents"],
    [planCollaboration, "agents"],
    [persisted, "selected_agents"],
    [persistedCollaboration, "selected_agents"],
    [persistedCollaboration, "agents"],
  ]).map(mafAgentId).filter(Boolean);
  const eventAgentIds = events.flatMap(({ data }) => [
    data.agent_id || data.agent,
    data.source_agent_id,
    data.target_agent_id,
  ]).filter(Boolean);
  const selectedAgents = [...new Set([...selectedFromData, ...eventAgentIds])];
  const skippedCandidates = preferredArray(plan, "skipped_agents", [
    [planCollaboration, "skipped_agents"],
    [persisted, "skipped_agents"],
  ]).map(mafAgentId).filter(Boolean);
  const skippedAgents = skippedCandidates.filter((id) => !selectedAgents.includes(id));

  const persistedAgentRecords = mafArray(persisted.agents).length
    ? mafArray(persisted.agents)
    : mafArray(persistedCollaboration.agents);
  const persistedAgentMap = new Map(persistedAgentRecords.map((record) => [mafAgentId(record), record]));
  const agentState = new Map(selectedAgents.map((id) => [id, initialAgentState(id, persistedAgentMap)]));
  const completedDurationTotals = new Map();
  const completedDurationMeasured = new Set();
  const branches = new Map();
  const handoffs = new Map();
  const reviews = new Map();
  const traceFallback = [...events].reverse().find((item) => item.event === "maf_fallback")?.data;
  const fallback = traceFallback || (persisted.fallback ? (typeof persisted.fallback === "object" ? persisted.fallback : { status: "recorded" }) : null);

  for (const { event, data } of events) {
    const id = data.agent_id || data.agent;
    if (id && ["maf_agent_started", "maf_agent_completed", "maf_agent_failed"].includes(event)) {
      const current = agentState.get(id) || initialAgentState(id, persistedAgentMap);
      const semanticStatus = event === "maf_agent_started"
        ? (data.status || "running")
        : event === "maf_agent_failed"
          ? (data.status || "failed")
          : (data.status || "completed");
      let durationMs = current.durationMs;
      if (event === "maf_agent_completed") {
        if (!completedDurationTotals.has(id)) {
          completedDurationTotals.set(id, 0);
          durationMs = null;
        }
        if (typeof data.duration_ms === "number" && Number.isFinite(data.duration_ms)) {
          const total = completedDurationTotals.get(id) + Math.max(0, data.duration_ms);
          completedDurationTotals.set(id, total);
          completedDurationMeasured.add(id);
          durationMs = total;
        } else if (!completedDurationMeasured.has(id)) {
          durationMs = null;
        }
      } else if (!completedDurationTotals.has(id)) {
        durationMs = null;
      }
      agentState.set(id, {
        ...current,
        status: semanticStatus,
        durationMs,
        tokens: data.tokens || data.usage || current.tokens,
        tools: mafArray(data.tool_names || data.tools).length ? mafArray(data.tool_names || data.tools) : current.tools,
        retries: data.retry_count ?? data.retries ?? current.retries,
        error: data.error_category || data.error || current.error,
      });
    }

    if ((event === "maf_branch_started" || event === "maf_branch_joined") && data.branch_id) {
      const current = branches.get(data.branch_id) || { id: data.branch_id };
      branches.set(data.branch_id, {
        ...current,
        agentId: data.agent_id || data.agent || current.agentId || "",
        required: data.required ?? current.required,
        status: data.status || (event === "maf_branch_started" ? "running" : "joined"),
        durationMs: event === "maf_branch_joined" && typeof data.duration_ms === "number" ? Math.max(0, data.duration_ms) : (current.durationMs ?? null),
        error: data.error_category || data.error || current.error || "",
      });
    }

    if (event === "maf_handoff") {
      const source = data.source_agent_id || data.source_agent || "";
      const target = data.target_agent_id || data.target_agent || "";
      const key = `${source}:${target}:${mafArray(data.reason_codes).join(",")}`;
      handoffs.set(key, {
        source,
        target,
        status: data.status || "recorded",
        reasons: mafArray(data.reason_codes),
        error: data.error_category || data.error || "",
      });
    }

    if (event === "maf_review") {
      const explicitRound = mafRevisionNumber(data);
      const pendingRound = [...reviews.values()].reverse().find((review) => review.status === "running")?.round;
      const round = explicitRound || pendingRound || reviews.size + 1;
      reviews.set(round, {
        round,
        status: data.status || "recorded",
        verdict: data.verdict || "",
        agentId: data.agent_id || data.agent || "",
        reasons: mafArray(data.reason_codes),
        error: data.error_category || data.error || "",
      });
    }
  }

  if (!events.length && !mode && !selectedAgents.length && !skippedAgents.length && !fallback) return null;
  const maxRevisions = plan.max_revisions ?? planCollaboration.max_revisions ?? persisted.max_revisions ?? persistedCollaboration.max_revisions ?? null;
  const reasonCodes = preferredArray(plan, "reason_codes", [
    [plan, "selection_reason_codes"],
    [planCollaboration, "reason_codes"],
    [persisted, "selection_reason_codes"],
    [persistedCollaboration, "reason_codes"],
  ]);
  return {
    mode,
    maxRevisions,
    selectedAgents,
    skippedAgents,
    agents: [...agentState.values()].map((agent) => ({ ...agent, tone: mafStatusTone(agent.status) })),
    branches: [...branches.values()],
    handoffs: [...handoffs.values()],
    reviews: [...reviews.values()].sort((a, b) => a.round - b.round),
    fallback,
    reasonCodes,
  };
}

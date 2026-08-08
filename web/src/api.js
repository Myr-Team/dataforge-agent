export const API_BASE = import.meta.env?.VITE_API_BASE ?? "";
const ACTOR_STORAGE_KEY = "df-current-user";

function clientActorHeaders() {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(ACTOR_STORAGE_KEY);
    if (!raw) return {};
    const actor = JSON.parse(raw);
    const email = String(actor?.email || "").trim();
    if (!email || !email.includes("@")) return {};
    const payload = {
      name: String(actor?.name || "").trim(),
      email,
      actor_id: String(actor?.actor_id || actor?.id || "").trim(),
      tenant_id: String(actor?.tenant_id || actor?.tid || "").trim(),
    };
    return { "X-DataForge-Actor": encodeURIComponent(JSON.stringify(payload)) };
  } catch {
    return {};
  }
}

function errorMessageFromPayload(data, fallback) {
  const raw = data?.detail ?? data?.message ?? fallback;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") {
    if (typeof raw.message === "string") return raw.message;
    if (typeof raw.error === "string") return raw.error;
    if (raw.error && typeof raw.error === "object") {
      if (typeof raw.error.message === "string") return raw.error.message;
      try {
        return JSON.stringify(raw.error);
      } catch {
        return fallback;
      }
    }
    try {
      return JSON.stringify(raw);
    } catch {
      return fallback;
    }
  }
  return String(raw || fallback);
}

async function request(path, options = {}) {
  const {
    timeoutMs = 0,
    signal: callerSignal,
    ...fetchOptions
  } = options;
  const boundedTimeoutMs = Number.isFinite(Number(timeoutMs))
    ? Math.max(0, Number(timeoutMs))
    : 0;
  let timeoutId = null;
  let timedOut = false;
  let timeoutController = null;
  let forwardCallerAbort = null;
  let requestSignal = callerSignal;
  if (boundedTimeoutMs > 0) {
    timeoutController = new AbortController();
    requestSignal = timeoutController.signal;
    if (callerSignal?.aborted) {
      timeoutController.abort(callerSignal.reason);
    } else if (callerSignal) {
      forwardCallerAbort = () => timeoutController.abort(callerSignal.reason);
      callerSignal.addEventListener("abort", forwardCallerAbort, { once: true });
    }
    timeoutId = setTimeout(() => {
      timedOut = true;
      timeoutController.abort();
    }, boundedTimeoutMs);
  }
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: {
        Accept: "application/json",
        ...(fetchOptions.body && !(fetchOptions.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...clientActorHeaders(),
        ...(fetchOptions.headers || {}),
      },
      ...fetchOptions,
      ...(requestSignal ? { signal: requestSignal } : {}),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        message = errorMessageFromPayload(data, message);
      } catch (error) {
        if (timedOut) throw error;
        // Keep HTTP status text when the server does not return JSON.
      }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) {
      return {};
    }
    return await response.json();
  } catch (error) {
    if (timedOut) {
      const timeoutError = new Error("服务响应超时，请重试");
      timeoutError.name = "DataForgeRequestTimeoutError";
      timeoutError.code = "request_timeout";
      timeoutError.cause = error;
      throw timeoutError;
    }
    throw await toUserFacingRequestError(error);
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
    if (callerSignal && forwardCallerAbort) {
      callerSignal.removeEventListener("abort", forwardCallerAbort);
    }
  }
}

export const apiFetch = request;


async function probeEasyAuthSession() {
  if (typeof window === "undefined") return { authenticated: null };
  const endpoint = import.meta.env?.VITE_AUTH_ME || "/.auth/me";
  try {
    const response = await fetch(endpoint, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (response.status === 401 || response.status === 403) {
      return { authenticated: false };
    }
    if (!response.ok) return { authenticated: null };
    const data = await response.json();
    const principal = Array.isArray(data)
      ? data[0]
      : data?.clientPrincipal || data;
    const authenticated = Boolean(
      principal?.user_id
      || principal?.userId
      || principal?.userDetails
      || principal?.user_name
      || principal?.identityProvider,
    );
    return { authenticated };
  } catch {
    return { authenticated: null };
  }
}


export async function toUserFacingRequestError(error, authProbe = probeEasyAuthSession) {
  if (!isTransientFetchError(error)) return error;
  const auth = await authProbe();
  const expired = auth?.authenticated === false;
  const translated = new Error(
    expired
      ? "登录已失效，请刷新后重新登录"
      : "暂时无法连接服务，请稍后重试",
  );
  translated.code = expired ? "auth_session_expired" : "service_unreachable";
  translated.cause = error;
  return translated;
}

export function buildFinOpsQuery(filters = {}) {
  const params = new URLSearchParams();
  const supported = {
    from: filters.from,
    to: filters.to,
    department_id: filters.departmentId,
    workspace_id: filters.workspaceId,
    agent_id: filters.agentId,
    actor_ref: filters.actorRef,
    model: filters.model,
    cursor: filters.cursor,
    limit: filters.limit,
    bucket: filters.bucket,
    group_by: filters.groupBy,
    agent_kind: filters.agentKind,
    metric_id: filters.metricId,
    policy_type: filters.policyType,
  };
  Object.entries(supported).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      params.set(key, String(value));
    }
  });
  return params.toString();
}

function requestFinOps(path, options = {}, requestOptions = {}) {
  const separator = path.indexOf("?");
  const pathname = separator >= 0 ? path.slice(0, separator) : path;
  const params = new URLSearchParams(separator >= 0 ? path.slice(separator + 1) : "");
  if (options?.refresh === true) params.set("refresh", "1");
  const query = params.toString();
  return request(`${pathname}${query ? `?${query}` : ""}`, {
    ...(options?.signal ? { signal: options.signal } : {}),
    ...requestOptions,
  });
}

function loadFinOpsResource(resource, filters = {}, options = {}) {
  const query = buildFinOpsQuery(filters);
  return requestFinOps(
    `/api/finops/${resource}${query ? `?${query}` : ""}`,
    options,
  );
}

export function loadFinOpsFilters(filters = {}, options = {}) {
  return loadFinOpsResource("filters", filters, options);
}

export function loadFinOpsBootstrap(filters = {}, options = {}) {
  return loadFinOpsResource("bootstrap", filters, options);
}

export function loadFinOpsOverview(filters = {}, options = {}) {
  return loadFinOpsResource("overview", filters, options);
}

export function loadFinOpsBreakdowns(groupBy, filters = {}, options = {}) {
  return loadFinOpsResource("breakdowns", { ...filters, groupBy }, options);
}

export function loadFinOpsAgents(filters = {}, options = {}) {
  return loadFinOpsResource("agents", filters, options);
}

export function loadFinOpsTrends(bucket, filters = {}, options = {}) {
  return loadFinOpsResource("trends", { ...filters, bucket }, options);
}

export function loadFinOpsAnomalies(filters = {}, options = {}) {
  return loadFinOpsResource("anomalies", filters, options);
}

export function loadFinOpsRecommendations(filters = {}, options = {}) {
  return loadFinOpsResource("recommendations", filters, options);
}

export function loadFinOpsOpportunities(filters = {}, options = {}) {
  return loadFinOpsResource("opportunities", filters, options);
}

export function loadFinOpsRequests(filters = {}, options = {}) {
  return loadFinOpsResource("requests", filters, options);
}

export function loadFinOpsRequest(requestRef, filters = {}, options = {}) {
  return loadFinOpsResource(`requests/${encodeURIComponent(requestRef)}`, filters, options);
}

export function loadFinOpsEvidence(subject = {}, filters = {}, options = {}) {
  const metricId = String(subject?.metricId || subject?.metric_id || "").trim();
  const policyType = String(subject?.policyType || subject?.policy_type || "").trim();
  if (Boolean(metricId) === Boolean(policyType)) {
    return Promise.reject(new Error("exactly one evidence subject is required"));
  }
  return loadFinOpsResource(
    "evidence",
    {
      ...filters,
      ...(metricId ? { metricId } : { policyType }),
    },
    options,
  );
}

export function loadFinOpsInsights(filters = {}, options = {}) {
  return loadFinOpsResource("insights", filters, options);
}

export function analyzeFinOpsInsight(payload) {
  return request("/api/finops/insights/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function queryFinOpsAssistant(payload, options = {}) {
  return request("/api/finops/assistant/query", {
    ...options,
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function loadFinOpsAssistantBootstrap(workspaceId, options = {}) {
  return loadFinOpsResource(
    "assistant/bootstrap",
    { workspaceId },
    options,
  );
}

export function loadFinOpsAssistantConversations(workspaceId, options = {}) {
  return loadFinOpsResource(
    "assistant/conversations",
    { workspaceId },
    options,
  );
}

export function loadFinOpsAssistantMessages(
  conversationRef,
  workspaceId,
  options = {},
) {
  return loadFinOpsResource(
    `assistant/conversations/${encodeURIComponent(conversationRef)}/messages`,
    { workspaceId },
    options,
  );
}

export function clearFinOpsAssistantConversation(
  conversationRef,
  workspaceId,
  options = {},
) {
  const query = buildFinOpsQuery({ workspaceId });
  return request(
    `/api/finops/assistant/conversations/${encodeURIComponent(conversationRef)}?${query}`,
    { ...options, method: "DELETE" },
  );
}

export function loadFinOpsActions(filters = {}, options = {}) {
  return loadFinOpsResource("actions", filters, options);
}

export function loadFinOpsBudgets(filters = {}, options = {}) {
  return loadFinOpsResource("budgets", filters, options);
}

export function loadFinOpsRoiEconomics(filters = {}, options = {}) {
  return loadFinOpsResource("roi/economics", filters, options);
}

export function loadFinOpsRoiDecision(filters = {}, options = {}) {
  return loadFinOpsResource("roi/decision", filters, options);
}

export function loadFinOpsRiskDecision(filters = {}, options = {}) {
  return loadFinOpsResource("risk/decision", filters, options);
}

export function loadLatestFinOpsRiskScan(filters = {}, options = {}) {
  return loadFinOpsResource("risk/scans/latest", filters, options);
}

export function runFinOpsRiskScan(payload = {}, options = {}) {
  const clean = {
    workspace_id: payload.workspaceId ?? payload.workspace_id,
    from: payload.from,
    to: payload.to,
    department_id: payload.departmentId ?? payload.department_id,
    agent_id: payload.agentId ?? payload.agent_id,
    actor_ref: payload.actorRef ?? payload.actor_ref,
    model: payload.model,
  };
  const body = Object.fromEntries(Object.entries(clean).filter(([, value]) => (
    value !== undefined && value !== null && String(value).trim() !== ""
  )));
  return requestFinOps("/api/finops/risk/scans", options, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function remediationTransitionBody(payload = {}) {
  const baseRevision = payload.baseRevision ?? payload.base_revision;
  const reason = payload.reason;
  return {
    base_revision: baseRevision,
    ...(reason !== undefined && reason !== null && String(reason) !== "" ? { reason } : {}),
  };
}

export function loadFinOpsRemediationDrafts(workspaceOrFilters = {}, options = {}) {
  const workspaceId = typeof workspaceOrFilters === "string"
    ? workspaceOrFilters
    : workspaceOrFilters?.workspaceId ?? workspaceOrFilters?.workspace_id;
  const params = new URLSearchParams();
  if (workspaceId !== undefined && workspaceId !== null && String(workspaceId).trim() !== "") {
    params.set("workspace_id", String(workspaceId));
  }
  const query = params.toString();
  return requestFinOps(
    `/api/finops/remediation-drafts${query ? `?${query}` : ""}`,
    options,
  );
}

export function loadFinOpsRemediationDraft(draftId, options = {}) {
  return requestFinOps(
    `/api/finops/remediation-drafts/${encodeURIComponent(draftId)}`,
    options,
  );
}

export function createFinOpsRemediationDraft(payload = {}, options = {}) {
  return requestFinOps("/api/finops/remediation-drafts", options, {
    method: "POST",
    body: JSON.stringify({
      workspace_id: payload.workspaceId ?? payload.workspace_id,
      source_opportunity_id: payload.sourceOpportunityId ?? payload.source_opportunity_id,
      base_version: payload.baseVersion ?? payload.base_version,
    }),
  });
}

function transitionFinOpsRemediationDraft(draftId, transition, payload = {}, options = {}) {
  return requestFinOps(
    `/api/finops/remediation-drafts/${encodeURIComponent(draftId)}/${transition}`,
    options,
    {
      method: "POST",
      body: JSON.stringify(remediationTransitionBody(payload)),
    },
  );
}

export function reviewFinOpsRemediationDraft(draftId, payload = {}, options = {}) {
  return transitionFinOpsRemediationDraft(draftId, "review", payload, options);
}

export function promoteFinOpsRemediationDraft(draftId, payload = {}, options = {}) {
  return transitionFinOpsRemediationDraft(draftId, "promote", payload, options);
}

export function closeFinOpsRemediationDraft(draftId, payload = {}, options = {}) {
  return transitionFinOpsRemediationDraft(draftId, "close", payload, options);
}

export function loadFinOpsSavedViews(filters = {}, options = {}) {
  return loadFinOpsResource("views", filters, options);
}

export function createFinOpsSavedView(payload) {
  return request("/api/finops/views", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteFinOpsSavedView(viewId) {
  return request(`/api/finops/views/${encodeURIComponent(viewId)}`, {
    method: "DELETE",
  });
}

export function finOpsExportUrl(groupBy, filters = {}) {
  const query = buildFinOpsQuery({ ...filters, groupBy });
  return `${API_BASE}/api/finops/export.csv${query ? `?${query}` : ""}`;
}

export function acknowledgeFinOpsAnomaly(anomalyId) {
  return request(`/api/finops/anomalies/${encodeURIComponent(anomalyId)}/acknowledge`, {
    method: "POST",
  });
}

export function suppressFinOpsAnomaly(anomalyId, reason, until = "") {
  return request(`/api/finops/anomalies/${encodeURIComponent(anomalyId)}/suppress`, {
    method: "POST",
    body: JSON.stringify({ reason, ...(until ? { until } : {}) }),
  });
}

export function createFinOpsAction(payload) {
  return request("/api/finops/actions", { method: "POST", body: JSON.stringify(payload) });
}

export function transitionFinOpsAction(actionId, transition, payload = null) {
  return request(`/api/finops/actions/${encodeURIComponent(actionId)}/${encodeURIComponent(transition)}`, {
    method: "POST",
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
}

export function artifactLink(artifact) {
  if (!artifact) return "";
  const url =
    artifact.artifact_url ||
    artifact.audio_blob_url ||
    artifact.pdf_blob_url ||
    artifact.concept_image_blob_url ||
    artifact.url ||
    "";
  if (!url) return "";
  return url.startsWith("http") ? url : `${API_BASE}${url}`;
}

export function parseSse(buffer) {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() || "";
  const events = chunks
    .map((chunk) => {
      const lines = chunk.split("\n");
      const eventLine = lines.find((line) => line.startsWith("event: "));
      const dataLines = lines.filter((line) => line.startsWith("data: "));
      if (!eventLine) return null;
      let data = dataLines.map((line) => line.slice(6)).join("\n");
      try {
        data = JSON.parse(data);
      } catch {
        data = { text: data };
      }
      return { event: eventLine.slice(7), data };
    })
    .filter(Boolean);
  return { events, rest };
}

export function isTransientFetchError(error) {
  const name = error?.name || "";
  const message = error instanceof Error ? error.message : String(error || "");
  return (
    name === "TypeError" &&
    /failed to fetch|networkerror|network error|load failed|fetch failed|connection|disconnected/i.test(message)
  );
}

export async function loadDashboard(workspaceId, options = {}) {
  const requestOptions = { timeoutMs: 15_000, ...options };
  try {
    return await request(`/api/workspaces/${encodeURIComponent(workspaceId)}/dashboard`, requestOptions);
  } catch (error) {
    if (error?.name === "DataForgeRequestTimeoutError" || error?.name === "AbortError") {
      throw error;
    }
    const [workspace, workspaces, runs, conversations, health] = await Promise.all([
      request(`/api/workspaces/${encodeURIComponent(workspaceId)}`, requestOptions).catch(() => ({})),
      request("/api/workspaces", requestOptions).catch(() => ({ workspaces: [] })),
      request(`/api/runs?workspace_id=${encodeURIComponent(workspaceId)}`, requestOptions).catch(() => ({ runs: [] })),
      request(`/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}`, requestOptions).catch(() => ({ conversations: [] })),
      request("/api/health", requestOptions).catch((healthError) => ({ ok: false, message: healthError.message })),
    ]);
    return {
      workspace_id: workspaceId,
      workspace,
      workspaces: workspaces.workspaces || [],
      runs: runs.runs || [],
      conversations: conversations.conversations || [],
      health,
      dependency_details: health.dependency_details || {},
      fallback_error: error.message,
    };
  }
}

export async function loadWorkspaceAccess(workspaceId, options = {}) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/access`, { timeoutMs: 8_000, ...options });
}

export async function loadLatestAnalysis(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/latest-analysis`);
}

export async function listWorkspaces() {
  const data = await request("/api/workspaces");
  return data.workspaces || [];
}

export async function loadObservability() {
  return request("/api/observability");
}

export async function loadPlaybookDetail(payload) {
  return request("/api/playbook", { method: "POST", body: JSON.stringify(payload) });
}

export async function loadRun(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function loadRunSummary(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}/summary`);
}

export async function loadRunTrace(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}/trace`);
}

export async function loadRunLog(runId, format = "json") {
  return request(`/api/runs/${encodeURIComponent(runId)}/log?format=${encodeURIComponent(format)}`);
}

export async function loadDataOverview(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/data-overview`);
}

export async function loadWorkspaceFiles(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files`);
}

export async function createWorkspaceFile(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function loadWorkspaceFileContent(workspaceId, fileId, { limit = 100, offset = 0 } = {}) {
  return request(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/content?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
  );
}

export async function saveWorkspaceTableCells(workspaceId, fileId, edits) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/cells`, {
    method: "PUT",
    body: JSON.stringify({ edits }),
  });
}

export async function saveWorkspaceFileContent(workspaceId, fileId, text) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/content`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });
}

export async function loadWorkspaceFileQuality(workspaceId, fileId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/quality`);
}

export async function loadWorkspaceFieldMapping(workspaceId, fileId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/field-mapping`);
}

export async function saveWorkspaceFieldMapping(workspaceId, fileId, mappings) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/field-mapping`, {
    method: "PUT",
    body: JSON.stringify({ mappings }),
  });
}

export async function loadWorkspaceFileHistory(workspaceId, fileId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(fileId)}/history`);
}

export async function analyzeWorkspaceFiles(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files/analyze`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function loadConnectorCapabilities(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/connectors/capabilities`);
}

export async function connectWorkspaceBlob(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/connectors/blob/connect`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function connectWorkspaceSql(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/connectors/sql/connect`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function loadWorkspaceArtifacts(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/artifacts`);
}

export async function loadExperiments(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/experiments`);
}

export async function compareExperiments(workspaceId, fromId, toId) {
  const params = new URLSearchParams({ from: fromId, to: toId });
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/experiments/compare?${params.toString()}`);
}

export async function loadSystemStatus() {
  return request("/api/system-status");
}

export async function loadWorkspaceSettings(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`);
}

export async function loadWorkspaceMembers(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members`);
}

export async function searchWorkspaceEntraUsers(workspaceId, query, limit = 8) {
  const params = new URLSearchParams({
    query: query || "",
    limit: String(limit || 8),
  });
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members/entra-users?${params.toString()}`);
}

export async function inviteWorkspaceMember(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members/invite`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function inviteWorkspaceEntraMember(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members/entra-invite`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function removeWorkspaceMember(workspaceId, subjectRef) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(subjectRef)}`, {
    method: "DELETE",
  });
}

export async function updateWorkspaceMemberRole(workspaceId, subjectRef, role) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(subjectRef)}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function loadWorkspaceUsageSummary(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/usage-summary`);
}

export async function loadWorkspaceAuditEvents(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/audit-events`);
}

export async function loadWorkspaceGovernance(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance-summary`);
}

export async function loadGovernanceCapabilities(workspaceId, options = {}) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/capabilities`, { timeoutMs: 8_000, ...options });
}

export async function loadGovernanceLineage(workspaceId, { scope = "self", cursor = "", limit = 50 } = {}) {
  const boundedLimit = Math.max(1, Math.min(100, Number.parseInt(limit, 10) || 50));
  const params = new URLSearchParams({ scope: scope === "workspace" ? "workspace" : "self", limit: String(boundedLimit) });
  if (cursor) params.set("cursor", cursor);
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/lineage?${params.toString()}`);
}

export async function loadEnterpriseIdentityPolicy(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/identity-policy`);
}

export async function updateEnterpriseIdentityPolicy(workspaceId, trustedEmailDomains) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/identity-policy`, {
    method: "PUT",
    body: JSON.stringify({ trusted_email_domains: trustedEmailDomains }),
  });
}

export async function loadModelProviders() {
  return request("/api/model-providers");
}

export async function createModelProvider(payload) {
  return request("/api/model-providers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function testModelProvider(providerId) {
  return request(`/api/model-providers/${encodeURIComponent(providerId)}/test`, {
    method: "POST",
  });
}

export async function rotateModelProviderSecret(providerId, credentials, baseRevision) {
  const body = typeof credentials === "string"
    ? { api_key: credentials, base_revision: Number(baseRevision || 0) }
    : credentials?.provider_type === "aws_bedrock"
      ? {
        provider_type: "aws_bedrock",
        access_key_id: String(credentials.access_key_id || ""),
        secret_access_key: String(credentials.secret_access_key || ""),
        session_token: credentials.session_token || null,
        base_revision: Number(baseRevision || 0),
      }
      : { ...credentials, base_revision: Number(baseRevision || 0) };
  return request(`/api/model-providers/${encodeURIComponent(providerId)}/rotate-secret`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateModelProvider(providerId, payload) {
  return request(`/api/model-providers/${encodeURIComponent(providerId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function disableModelProvider(providerId, baseRevision) {
  return request(`/api/model-providers/${encodeURIComponent(providerId)}/disable`, {
    method: "POST",
    body: JSON.stringify({ base_revision: Number(baseRevision || 0) }),
  });
}

export async function loadIdentityGovernance() {
  return request("/api/identity-governance");
}

export async function searchIdentityGovernanceGroups(query, limit = 8) {
  const params = new URLSearchParams({
    query: String(query || ""),
    limit: String(limit),
  });
  return request(`/api/identity-governance/groups?${params.toString()}`);
}

export async function createIdentityGroupMapping(payload) {
  return request("/api/identity-governance/group-mappings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateIdentityGroupMapping(mappingId, payload) {
  return request(`/api/identity-governance/group-mappings/${encodeURIComponent(mappingId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function disableIdentityGroupMapping(mappingId, baseRevision) {
  return request(`/api/identity-governance/group-mappings/${encodeURIComponent(mappingId)}/disable`, {
    method: "POST",
    body: JSON.stringify({ base_revision: Number(baseRevision || 0) }),
  });
}

export async function loadWorkspaceModelRouting(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/model-routing`);
}

export async function updateWorkspaceModelRouting(workspaceId, policy) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/model-routing`, {
    method: "PUT",
    body: JSON.stringify(policy),
  });
}

export async function loadWorkspaceModelPriceCard(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/model-price-card`);
}

export async function updateWorkspaceModelPriceCard(workspaceId, priceCard) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/model-price-card`, {
    method: "PUT",
    body: JSON.stringify(priceCard),
  });
}

export async function loadFinOpsOfficialPriceCatalog() {
  return request("/api/finops/pricing/catalog");
}

export async function loadFinOpsOfficialPriceMappings() {
  return request("/api/finops/pricing/mappings");
}

export async function updateFinOpsOfficialPriceMapping(
  deployment,
  { officialPriceKey, baseRevision = 0 },
) {
  return request(`/api/finops/pricing/mappings/${encodeURIComponent(deployment)}`, {
    method: "PUT",
    body: JSON.stringify({
      official_price_key: String(officialPriceKey || ""),
      base_revision: Number.isInteger(baseRevision) ? baseRevision : 0,
    }),
  });
}

export async function deleteFinOpsOfficialPriceMapping(deployment) {
  return request(`/api/finops/pricing/mappings/${encodeURIComponent(deployment)}`, {
    method: "DELETE",
  });
}

function workspaceBudgetUrl(path, workspaceId, params = {}) {
  const cleanWorkspaceId = String(workspaceId || "").trim();
  if (!cleanWorkspaceId) throw new Error("workspaceId is required");
  const query = new URLSearchParams({
    workspace_id: cleanWorkspaceId,
    ...params,
  });
  return `${path}?${query.toString()}`;
}

export async function loadMemberBudgets(workspaceId) {
  return request(workspaceBudgetUrl("/api/finops/member-budgets", workspaceId, { limit: "100" }));
}

export async function loadMemberBudgetMembers(workspaceId) {
  return request(workspaceBudgetUrl("/api/finops/member-budget-members", workspaceId, { limit: "100" }));
}

export async function loadMemberBudgetNotification(workspaceId) {
  return request(workspaceBudgetUrl("/api/finops/notification-settings", workspaceId));
}

export async function loadMemberBudgetAlerts(workspaceId) {
  return request(workspaceBudgetUrl("/api/finops/budget-alerts", workspaceId, { limit: "50" }));
}

export async function saveMemberBudget({
  workspaceId = "",
  budgetId = "",
  memberRef = "",
  amountUsd,
  thresholdsPct = [80, 95, 100],
  enabled = true,
  baseRevision = 0,
} = {}) {
  const editing = Boolean(String(budgetId || "").trim());
  const body = {
    ...(editing ? {} : { member_ref: String(memberRef || "").trim() }),
    amount_usd: Number(amountUsd),
    thresholds_pct: Array.isArray(thresholdsPct) ? thresholdsPct.map(Number) : [],
    enabled: enabled === true,
    base_revision: Number.isInteger(baseRevision) ? baseRevision : 0,
  };
  return request(
    editing
      ? workspaceBudgetUrl(`/api/finops/member-budgets/${encodeURIComponent(budgetId)}`, workspaceId)
      : workspaceBudgetUrl("/api/finops/member-budgets", workspaceId),
    {
      method: editing ? "PATCH" : "POST",
      body: JSON.stringify(body),
    },
  );
}

export async function disableMemberBudget(workspaceId, budgetId, baseRevision) {
  return request(workspaceBudgetUrl(`/api/finops/member-budgets/${encodeURIComponent(budgetId)}/disable`, workspaceId), {
    method: "POST",
    body: JSON.stringify({
      base_revision: Number.isInteger(baseRevision) ? baseRevision : 0,
    }),
  });
}

export async function saveMemberBudgetNotification({
  workspaceId = "",
  recipientEmail = "",
  senderDisplayName = "DataForge",
  subjectTemplate = "",
  bodyTemplate = "",
  enabled = false,
  baseRevision = 0,
} = {}) {
  return request(workspaceBudgetUrl("/api/finops/notification-settings", workspaceId), {
    method: "PUT",
    body: JSON.stringify({
      recipient_email: String(recipientEmail || "").trim(),
      sender_display_name: String(senderDisplayName || "").trim(),
      subject_template: String(subjectTemplate || ""),
      body_template: String(bodyTemplate || ""),
      enabled: enabled === true,
      base_revision: Number.isInteger(baseRevision) ? baseRevision : 0,
    }),
  });
}

export async function sendMemberBudgetTestEmail(workspaceId) {
  return request(workspaceBudgetUrl("/api/finops/notification-settings/test-email", workspaceId), {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function loadMonitoringDashboard({ scope = "current", workspaceId, from, to, signal } = {}) {
  const params = new URLSearchParams({
    scope: String(scope || "current"),
    workspace_id: String(workspaceId || ""),
    from: String(from || ""),
    to: String(to || ""),
  });
  return request(`/api/monitoring?${params.toString()}`, { signal });
}

export async function loadWorkspaceTraceStatus(workspaceId, { runId = "", correlationId = "" } = {}) {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (correlationId) params.set("correlation_id", correlationId);
  const query = params.toString();
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/trace-status${query ? `?${query}` : ""}`);
}

export async function loadWorkspaceTraceMetrics(workspaceId, { runId = "", correlationId = "" } = {}) {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (correlationId) params.set("correlation_id", correlationId);
  const query = params.toString();
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/trace-metrics${query ? `?${query}` : ""}`);
}

export async function loadWorkspaceRoi(workspaceId, { from, to }) {
  const params = new URLSearchParams({ from, to });
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/roi?${params.toString()}`);
}

export async function loadWorkspaceCostValue(workspaceId, { from, to }) {
  const params = new URLSearchParams({ from, to });
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/cost-value?${params.toString()}`);
}

export async function createWorkspaceRoiScenario(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/scenarios`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function loadWorkspaceChargeback(workspaceId, { from, to }) {
  const params = new URLSearchParams({ from, to });
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/chargeback?${params.toString()}`);
}

export async function loadWorkspaceGovernanceAuditEvents(workspaceId, { limit = 50, cursor = "" } = {}) {
  const boundedLimit = Math.max(1, Math.min(100, Number.parseInt(limit, 10) || 50));
  const params = new URLSearchParams({ limit: String(boundedLimit) });
  if (cursor) params.set("cursor", cursor);
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/audit-events?${params.toString()}`);
}

export async function loadWorkspaceInvitationHistory(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/invitations`);
}

export async function loadConversationStructuredResult(conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}/structured-result`);
}

export async function loadConversationContext(conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}/context`);
}

export async function loadConversationQuickActions(conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}/quick-actions`);
}

export async function loadPlanMetrics(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}/plan-metrics`);
}

export async function loadFlagship(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/flagship`);
}

export async function setFlagship(workspaceId, runId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/flagship`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId }),
  });
}

export async function deleteWorkspace(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
}

export async function loadConversation(conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}`);
}

export async function uploadWorkspace({ name, description, files, workspaceId, assetRole }) {
  const form = new FormData();
  for (const file of files) form.append("file", file);
  if (name) form.append("name", name);
  if (description) form.append("description", description);
  if (workspaceId) form.append("workspace_id", workspaceId);
  if (assetRole) form.append("asset_role", assetRole);
  return request("/api/upload", { method: "POST", body: form });
}

export async function produceArtifacts(payload) {
  return request("/api/produce", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function createArtifactJob(payload, idempotencyKey) {
  return request("/api/artifact-jobs", {
    method: "POST",
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
    body: JSON.stringify(payload),
  });
}

export async function loadArtifactJob(jobId) {
  return request(`/api/artifact-jobs/${encodeURIComponent(jobId)}`);
}

export async function loadArtifactJobs(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/artifact-jobs`);
}

export async function loadWorkspaceTasks(workspaceId, options = {}) {
  const data = await request(`/api/workspaces/${encodeURIComponent(workspaceId)}/tasks`, options);
  return data.tasks || [];
}

export async function loadTask(taskId) {
  return request(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function cancelTask(taskId) {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

export async function retryTask(taskId) {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
}

const wsPath = (id) => `/api/workspaces/${encodeURIComponent(id)}`;
const queryString = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();

export async function dwListFiles(workspaceId) {
  return loadWorkspaceFiles(workspaceId);
}
export async function dwFileContent(workspaceId, fileId, { limit = 100, offset = 0 } = {}) {
  return loadWorkspaceFileContent(workspaceId, fileId, { limit, offset });
}
export async function dwCreateFile(workspaceId, body) {
  return createWorkspaceFile(workspaceId, body);
}
export async function dwSaveCells(workspaceId, fileId, payload) {
  const body = Array.isArray(payload) ? { edits: payload } : payload || {};
  return request(`${wsPath(workspaceId)}/files/${encodeURIComponent(fileId)}/cells`, { method: "PUT", body: JSON.stringify(body) });
}
export async function dwSaveContent(workspaceId, fileId, text) {
  return saveWorkspaceFileContent(workspaceId, fileId, text);
}
export async function dwDeleteFile(workspaceId, fileId) {
  return request(`${wsPath(workspaceId)}/files/${encodeURIComponent(fileId)}`, { method: "DELETE" });
}
export async function dwFileQuality(workspaceId, fileId) {
  return loadWorkspaceFileQuality(workspaceId, fileId);
}
export async function dwFieldMapping(workspaceId, fileId) {
  return loadWorkspaceFieldMapping(workspaceId, fileId);
}
export async function dwSaveFieldMapping(workspaceId, fileId, mapping) {
  return saveWorkspaceFieldMapping(workspaceId, fileId, mapping);
}
export async function dwFileHistory(workspaceId, fileId) {
  return loadWorkspaceFileHistory(workspaceId, fileId);
}
export async function dwAnalyzeFiles(workspaceId, fileIds, message) {
  return analyzeWorkspaceFiles(workspaceId, { file_ids: fileIds, message: message || "请分析这些文件里的机会", artifact_mode: "report" });
}

export function runLogUrl(runId, format = "json") {
  return `${API_BASE}/api/runs/${encodeURIComponent(runId)}/log?format=${format}`;
}

export async function loadArtifactsList(workspaceId) {
  return loadWorkspaceArtifacts(workspaceId);
}
export async function loadMembers(workspaceId) {
  return loadWorkspaceMembers(workspaceId);
}
export async function searchEntraUsers(workspaceId, query, limit = 8) {
  return searchWorkspaceEntraUsers(workspaceId, query, limit);
}
export async function inviteMember(workspaceId, payload) {
  return inviteWorkspaceMember(workspaceId, payload);
}
export async function inviteEntraMember(workspaceId, payload) {
  return inviteWorkspaceEntraMember(workspaceId, payload);
}
export async function removeMember(workspaceId, subjectRef) {
  return removeWorkspaceMember(workspaceId, subjectRef);
}
export async function updateMemberRole(workspaceId, subjectRef, role) {
  return updateWorkspaceMemberRole(workspaceId, subjectRef, role);
}

export async function dwConnectorCapabilities(workspaceId) {
  return loadConnectorCapabilities(workspaceId);
}
export async function dwListConnectors(workspaceId) {
  return request(`${wsPath(workspaceId)}/connectors`);
}
export async function dwReconnectConnector(workspaceId, connectorId) {
  return request(`${wsPath(workspaceId)}/connectors/${encodeURIComponent(connectorId)}/reconnect`, { method: "POST" });
}
export async function dwSyncConnector(workspaceId, connectorId, payload) {
  return request(`${wsPath(workspaceId)}/connectors/${encodeURIComponent(connectorId)}/sync`, { method: "POST", body: JSON.stringify(payload) });
}
export async function dwDeleteConnector(workspaceId, connectorId) {
  return request(`${wsPath(workspaceId)}/connectors/${encodeURIComponent(connectorId)}`, { method: "DELETE" });
}
export async function dwBlobConnect(workspaceId, payload) {
  return connectWorkspaceBlob(workspaceId, payload);
}
export async function dwBlobStatus(workspaceId, connectionId) {
  return request(`${wsPath(workspaceId)}/connectors/blob/status?${queryString({ connection_id: connectionId })}`);
}
export async function dwBlobContainers(workspaceId, connectionId) {
  return request(`${wsPath(workspaceId)}/connectors/blob/containers?${queryString({ connection_id: connectionId })}`);
}
export async function dwBlobItems(workspaceId, connectionId, container, prefix = "", limit = 100) {
  return request(`${wsPath(workspaceId)}/connectors/blob/blobs?${queryString({ connection_id: connectionId, container, prefix, limit })}`);
}
export async function dwBlobPreview(workspaceId, connectionId, container, blob, { limit = 100, offset = 0 } = {}) {
  return request(`${wsPath(workspaceId)}/connectors/blob/preview?${queryString({ connection_id: connectionId, container, blob, limit, offset })}`);
}
export async function dwBlobImport(workspaceId, payload) {
  return request(`${wsPath(workspaceId)}/connectors/blob/import`, { method: "POST", body: JSON.stringify(payload) });
}
export async function dwSqlConnect(workspaceId, payload) {
  return connectWorkspaceSql(workspaceId, payload);
}
export async function dwSqlStatus(workspaceId, connectionId) {
  return request(`${wsPath(workspaceId)}/connectors/sql/status?${queryString({ connection_id: connectionId })}`);
}
export async function dwSqlTables(workspaceId, connectionId) {
  return request(`${wsPath(workspaceId)}/connectors/sql/tables?${queryString({ connection_id: connectionId })}`);
}
export async function dwSqlPreview(workspaceId, connectionId, table, limit = 100) {
  return request(`${wsPath(workspaceId)}/connectors/sql/preview?${queryString({ connection_id: connectionId, table, limit })}`);
}
export async function dwSqlImport(workspaceId, payload) {
  return request(`${wsPath(workspaceId)}/connectors/sql/import`, { method: "POST", body: JSON.stringify(payload) });
}
export async function dwDisconnectConnector(workspaceId, payload) {
  return request(`${wsPath(workspaceId)}/connectors/disconnect`, { method: "POST", body: JSON.stringify(payload) });
}

export async function streamChat(payload, onEvent, signal) {
  let deliveredEvents = 0;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...clientActorHeaders() },
        body: JSON.stringify(payload),
        signal,
      });
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const data = await response.json();
          message = errorMessageFromPayload(data, message);
        } catch {
          // Keep HTTP status text.
        }
        throw new Error(message);
      }
      if (!response.body) throw new Error("SSE stream is unavailable in this browser.");
      const taskId = response.headers.get("X-DataForge-Task-Id");
      if (taskId) onEvent({ event: "task_meta", data: { task_id: taskId } });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSse(buffer);
        buffer = parsed.rest;
        for (const event of parsed.events) {
          deliveredEvents += 1;
          onEvent(event);
        }
      }
      return;
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") throw error;
      if (attempt === 0 && deliveredEvents === 0 && isTransientFetchError(error)) continue;
      throw error;
    }
  }
}

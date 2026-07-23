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
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "application/json",
      ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...clientActorHeaders(),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = errorMessageFromPayload(data, message);
    } catch {
      // Keep HTTP status text when the server does not return JSON.
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const apiFetch = request;

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

export async function loadDashboard(workspaceId) {
  try {
    return await request(`/api/workspaces/${encodeURIComponent(workspaceId)}/dashboard`);
  } catch (error) {
    const [workspace, workspaces, runs, conversations, health] = await Promise.all([
      request(`/api/workspaces/${encodeURIComponent(workspaceId)}`),
      request("/api/workspaces"),
      request(`/api/runs?workspace_id=${encodeURIComponent(workspaceId)}`).catch(() => ({ runs: [] })),
      request(`/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}`).catch(() => ({ conversations: [] })),
      request("/api/health").catch((healthError) => ({ ok: false, message: healthError.message })),
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

export async function loadWorkspaceAccess(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/access`);
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

export async function loadGovernanceCapabilities(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/capabilities`);
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

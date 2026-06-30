export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

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
    throw new Error(message);
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

export async function loadSystemStatus() {
  return request("/api/system-status");
}

export async function loadWorkspaceSettings(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`);
}

export async function loadWorkspaceMembers(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/members`);
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

export async function streamChat(payload, onEvent, signal) {
  let deliveredEvents = 0;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
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

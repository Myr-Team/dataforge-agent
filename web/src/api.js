export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

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
      message = data.detail || data.message || message;
    } catch {
      // Keep HTTP status text when the server does not return JSON.
    }
    throw new Error(message);
  }
  return response.json();
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

export async function deleteWorkspace(workspaceId) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
}

export async function loadConversation(conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}`);
}

export async function uploadWorkspace({ name, description, files, workspaceId }) {
  const form = new FormData();
  for (const file of files) form.append("file", file);
  if (name) form.append("name", name);
  if (description) form.append("description", description);
  if (workspaceId) form.append("workspace_id", workspaceId);
  return request("/api/upload", { method: "POST", body: form });
}

export async function produceArtifacts(payload) {
  return request("/api/produce", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function streamChat(payload, onEvent) {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = data.detail || data.message || message;
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
    for (const event of parsed.events) onEvent(event);
  }
}

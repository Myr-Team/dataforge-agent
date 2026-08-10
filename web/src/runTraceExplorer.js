const SECRET_KEY = /^(authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|password|secret|connection[-_]?string|sas|sig)$/i;

function text(value) {
  return String(value ?? "").trim();
}

export function safeTraceValue(value, depth = 0) {
  if (depth > 6) return "[truncated]";
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => safeTraceValue(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [
      key,
      SECRET_KEY.test(key) ? "[redacted]" : safeTraceValue(item, depth + 1),
    ]));
  }
  return value;
}

export function prettyTraceJson(value) {
  return JSON.stringify(safeTraceValue(value), null, 2);
}

export function traceExplorerRows(trace = []) {
  return (Array.isArray(trace) ? trace : [])
    .filter((item) => item && typeof item === "object")
    .map((item, index) => {
      const detail = item.detail && typeof item.detail === "object" ? item.detail : {};
      const agentReference = detail.agent_reference && typeof detail.agent_reference === "object"
        ? detail.agent_reference
        : null;
      const event = text(item.event) || "event";
      const agent = text(item.agent) || text(agentReference?.name) || "系统";
      const external = Boolean(agentReference)
        || /external|hosted|mcp/i.test(`${event} ${text(detail.execution_kind)} ${text(detail.provider)}`);
      return {
        id: `${Number.isInteger(item.index) ? item.index : index}:${event}:${agent}`,
        index: Number.isInteger(item.index) ? item.index + 1 : index + 1,
        event,
        agent,
        role: text(item.role),
        status: text(item.status) || "unknown",
        time: text(item.time),
        durationMs: Number.isFinite(Number(item.duration_ms)) ? Number(item.duration_ms) : null,
        source: text(item.source) || "run_store.steps",
        external,
        payload: safeTraceValue(item),
      };
    });
}

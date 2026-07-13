const SENSITIVE_KEY = /password|passwd|pwd|connection.?string|sas|token|secret|credential|username|user.?id/i;
const RECORD_FIELDS = new Set([
  "workspace_id", "connector_id", "connection_id", "kind", "status", "persistence", "metadata",
  "created_at", "updated_at", "expires_at", "delete_pending", "error",
]);

function safeMetadata(metadata) {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return {};
  return Object.fromEntries(Object.entries(metadata).filter(([key]) => !SENSITIVE_KEY.test(key)));
}

export function publicConnectorRecord(record) {
  if (!record || typeof record !== "object" || !record.connector_id) return null;
  const safe = {};
  for (const [key, value] of Object.entries(record)) {
    if (!RECORD_FIELDS.has(key)) continue;
    if (key === "metadata") {
      safe.metadata = safeMetadata(value);
    } else if (!SENSITIVE_KEY.test(key) && key !== "secret_ref") {
      safe[key] = value;
    }
  }
  return safe;
}

export function connectorRecordsForWorkspaceResponse(response, workspaceId) {
  if (response?.workspace_id && response.workspace_id !== workspaceId) return [];
  return (Array.isArray(response?.connectors) ? response.connectors : [])
    .map(publicConnectorRecord)
    .filter(Boolean);
}

export function isCurrentConnectorListResponse({ requestSequence, currentSequence, requestWorkspaceId, currentWorkspaceId }) {
  return requestSequence === currentSequence && requestWorkspaceId === currentWorkspaceId;
}

export function createConnectorListController({ load, apply }) {
  let sequence = 0;
  let workspaceId = "";
  return {
    async refresh(nextWorkspaceId) {
      const requestSequence = ++sequence;
      workspaceId = nextWorkspaceId || "";
      apply([], workspaceId);
      if (!workspaceId) return [];
      const response = await load(workspaceId);
      if (!isCurrentConnectorListResponse({
        requestSequence,
        currentSequence: sequence,
        requestWorkspaceId: nextWorkspaceId,
        currentWorkspaceId: workspaceId,
      })) return [];
      const records = connectorRecordsForWorkspaceResponse(response, workspaceId);
      apply(records, workspaceId);
      return records;
    },
  };
}

export function createConnectorActionController({ currentWorkspaceId }) {
  const epochs = new Map();
  return {
    begin({ workspaceId, action, connectorId = "", kind = "" }) {
      const key = `${workspaceId}:${action}:${connectorId || `kind:${kind}`}`;
      const epoch = (epochs.get(key) || 0) + 1;
      epochs.set(key, epoch);
      return {
        workspaceId,
        action,
        connectorId,
        kind,
        epoch,
        isCurrent() {
          return currentWorkspaceId() === workspaceId && epochs.get(key) === epoch;
        },
      };
    },
  };
}

export function commitGuardedConnectorAction(guard, commit) {
  if (!guard?.isCurrent?.()) return false;
  commit();
  return true;
}

export function commitGuardedFileAction(guard, commit) {
  if (!guard?.isCurrent?.()) return false;
  commit();
  return true;
}

export function createWorkspaceFileController({ currentWorkspaceId }) {
  let requestSequence = 0;
  let actionSequence = 0;
  return {
    beginAction() {
      const workspaceId = currentWorkspaceId();
      const epoch = ++actionSequence;
      return {
        workspaceId,
        isCurrent() {
          return currentWorkspaceId() === workspaceId && actionSequence === epoch;
        },
      };
    },
    async reload(workspaceId, load, apply) {
      const requestedWorkspaceId = workspaceId || currentWorkspaceId();
      const sequence = ++requestSequence;
      const data = await load(requestedWorkspaceId);
      if (currentWorkspaceId() !== requestedWorkspaceId || requestSequence !== sequence) return data;
      apply(data, requestedWorkspaceId);
      return data;
    },
  };
}

export function replaceConnectorRecord(records, record) {
  const safe = publicConnectorRecord(record);
  if (!safe) return Array.isArray(records) ? records : [];
  const items = Array.isArray(records) ? records.filter((item) => item?.connector_id !== safe.connector_id) : [];
  return [safe, ...items];
}

export function connectorViewModel(records, selectedConnectorId, operations = {}) {
  const items = (Array.isArray(records) ? records : []).map(publicConnectorRecord).filter(Boolean);
  const selected = items.find((item) => item.connector_id === selectedConnectorId)
    || items.find((item) => item.status !== "disconnected")
    || items[0]
    || null;
  const selectedByKind = {};
  for (const kind of ["sql", "blob"]) {
    selectedByKind[kind] = selected?.kind === kind
      ? selected
      : items.find((item) => item.kind === kind && item.status !== "disconnected")
        || items.find((item) => item.kind === kind)
        || null;
  }
  return {
    records: items,
    selected,
    selectedByKind,
    cards: items.map((connector) => ({
      connector,
      pending: String(operations?.[connector.connector_id]?.pending || (connector.status === "finalizing" ? "finalizing" : "")),
      error: String(operations?.[connector.connector_id]?.error || ""),
    })),
  };
}

export function connectorActionState(model, connectorId) {
  const card = model?.cards?.find((item) => item.connector.connector_id === connectorId);
  return card ? { pending: card.pending, error: card.error } : { pending: "", error: "" };
}

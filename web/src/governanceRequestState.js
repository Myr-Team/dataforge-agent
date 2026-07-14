import { appendAuditPage } from "./governanceViewModel.js";

export function createWorkspaceRequestGuard() {
  let generation = 0;
  let workspaceId = "";
  let current = null;
  const isCurrent = (token, activeWorkspaceId) => Boolean(
    current
      && token
      && token.workspaceId === workspaceId
      && token.workspaceId === String(activeWorkspaceId || "")
      && token.generation === current.generation
  );
  return {
    begin(nextWorkspaceId) {
      workspaceId = String(nextWorkspaceId || "");
      current = Object.freeze({ workspaceId, generation: ++generation });
      return current;
    },
    capture(base, cursor, cursorWorkspaceId = base?.workspaceId) {
      if (!isCurrent(base, cursorWorkspaceId)) return null;
      return Object.freeze({ workspaceId: base?.workspaceId || "", generation: base?.generation || -1, cursor: String(cursor || "") });
    },
    isCurrent,
  };
}

export function createGovernanceRequestGuard() {
  return createWorkspaceRequestGuard();
}

export function workspaceBoundMemberContract(activeWorkspaceId, loadedWorkspaceId, rows, meta) {
  const ready = Boolean(activeWorkspaceId && activeWorkspaceId === loadedWorkspaceId && meta);
  return {
    ready,
    rows: ready && Array.isArray(rows) ? rows : [],
    meta: ready ? meta : null,
  };
}

export function emptyGovernanceData(workspaceId, loading = true) {
  return { workspaceId: String(workspaceId || ""), loading, trace: null, roi: null, chargeback: null, audit: null, auditRetryCursor: "", errors: {} };
}

export function workspaceBoundGovernanceData(activeWorkspaceId, data) {
  if (activeWorkspaceId && data?.workspaceId === activeWorkspaceId) return data;
  return emptyGovernanceData(activeWorkspaceId, Boolean(activeWorkspaceId));
}

export function auditPageFailure(current, cursor, message) {
  return {
    ...current,
    auditRetryCursor: String(cursor || ""),
    errors: { ...(current?.errors || {}), auditPage: message },
  };
}

export function auditPageSuccess(current, page) {
  return {
    ...current,
    audit: appendAuditPage(current?.audit || {}, page || {}),
    auditRetryCursor: "",
    errors: { ...(current?.errors || {}), auditPage: "" },
  };
}

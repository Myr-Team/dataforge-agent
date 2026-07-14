import { appendAuditPage } from "./governanceViewModel.js";

export function createGovernanceRequestGuard() {
  let generation = 0;
  let workspaceId = "";
  let current = null;
  return {
    begin(nextWorkspaceId) {
      workspaceId = String(nextWorkspaceId || "");
      current = Object.freeze({ workspaceId, generation: ++generation });
      return current;
    },
    capture(base, cursor) {
      return Object.freeze({ workspaceId: base?.workspaceId || "", generation: base?.generation || -1, cursor: String(cursor || "") });
    },
    isCurrent(token, activeWorkspaceId) {
      return Boolean(current && token && token.workspaceId === workspaceId && token.workspaceId === String(activeWorkspaceId || "") && token.generation === current.generation);
    },
  };
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

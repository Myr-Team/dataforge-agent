export function matchingWorkspaceValue(value, workspaceId) {
  if (!value || String(value.workspace_id || "") !== String(workspaceId || "")) {
    return null;
  }
  return value;
}

export function workspaceBootstrapFailure(value, workspaceId, error) {
  if (matchingWorkspaceValue(value, workspaceId)) return "";
  return error instanceof Error ? error.message : String(error || "服务暂时不可用");
}

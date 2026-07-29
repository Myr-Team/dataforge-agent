const ROLE_LABELS = {
  admin: "管理员",
  editor: "编辑者",
  viewer: "查看者",
};

function text(value) {
  return String(value || "").trim();
}

function permission(value) {
  const state = text(value) || "unavailable";
  return {
    state,
    ready: state === "configured",
    label: state === "configured" ? "已配置" : "未配置",
  };
}

export function identityAccessViewModel(payload = {}, workspaceId = "") {
  const rows = Array.isArray(payload.mappings) ? payload.mappings : [];
  const mappings = rows
    .filter((item) => {
      const scopes = Array.isArray(item?.workspace_ids) ? item.workspace_ids.map(text) : [];
      return item && typeof item === "object" && text(item.mapping_id)
        && (!workspaceId || scopes.includes(workspaceId));
    })
    .map((item) => ({
      mappingId: text(item.mapping_id),
      name: text(item.display_name) || "未命名组",
      role: text(item.role),
      roleLabel: ROLE_LABELS[text(item.role)] || text(item.role),
      workspaceIds: Array.isArray(item.workspace_ids) ? item.workspace_ids.map(text).filter(Boolean) : [],
      priority: Number.isInteger(item.priority) ? item.priority : 100,
      enabled: item.enabled !== false,
      revision: Number.isInteger(item.revision) ? item.revision : 0,
      updatedAt: text(item.updated_at),
    }));
  return {
    mappings,
    totalCount: Number.isInteger(payload.mapping_count) ? payload.mapping_count : mappings.length,
    permissions: {
      userRead: permission(payload?.permissions?.["User.ReadBasic.All"]),
      groupMembership: permission(payload?.permissions?.["GroupMember.Read.All"]),
    },
    membership: {
      claimsEnabled: text(payload?.membership_resolution?.claims) === "enabled",
      overageFallbackEnabled: text(payload?.membership_resolution?.overage_fallback) === "enabled",
      failureMode: text(payload?.membership_resolution?.failure_mode),
    },
  };
}

export function identityGroupSearchViewModel(payload = {}) {
  const groups = Array.isArray(payload.groups) ? payload.groups : [];
  return groups
    .filter((item) => item && typeof item === "object" && text(item.id))
    .map((item) => ({
      id: text(item.id),
      name: text(item.display_name) || text(item.name) || "未命名组",
    }));
}

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
  const labels = {
    configured: "已配置",
    granted: "已验证",
    verification_required: "待查询验证",
    token_available: "待查询验证",
    consent_required: "需要管理员同意",
    denied: "权限不足",
    unavailable: "未连接",
  };
  return {
    state,
    ready: ["configured", "granted"].includes(state),
    label: labels[state] || "状态未知",
  };
}

function graphConnection(value = {}) {
  const state = text(value?.state) || "unavailable";
  const labels = {
    connected: "Microsoft Graph 已连接",
    token_available: "登录令牌可用，等待目录查询验证",
    consent_required: "需要租户管理员同意目录权限",
    unavailable: "未取得可用的 Microsoft Graph 令牌",
  };
  return {
    state,
    tokenSource: text(value?.token_source),
    ready: state === "connected",
    label: labels[state] || "目录连接状态未知",
  };
}

const SESSION_ROLE_LABELS = {
  owner: "工作区所有者",
  admin: "工作区管理员",
  editor: "工作区编辑者",
  viewer: "工作区查看者",
};

const AUTHORIZATION_LABELS = {
  owner_match: "工作区创建者授权",
  member_match: "显式成员授权",
  group_match: "Entra 组映射授权",
  demo_tenant_owner: "演示租户所有者授权",
  role_denied: "当前操作超出角色权限",
  tenant_mismatch: "登录租户与工作区不匹配",
  identity_missing: "未取得可信登录身份",
  membership_missing: "未匹配成员或 Entra 组",
};

export function identitySessionViewModel({ user = {}, authState = "unavailable", access = null } = {}) {
  const trusted = authState === "authenticated"
    && text(user.identityProvider) === "microsoft_entra"
    && text(user.identitySource) === "trusted_proxy";
  const role = trusted && access?.allowed === true ? text(access.role) : "";
  const reason = trusted ? text(access?.reason_code) : "identity_missing";
  return {
    trusted,
    displayName: trusted ? text(user.name) || "DataForge 用户" : "身份信息暂不可用",
    email: trusted ? text(user.email) : "",
    identityLabel: trusted ? "Microsoft Entra ID" : "等待可信登录信息",
    identitySourceLabel: trusted ? "由 Easy Auth 验证" : "未使用本地身份代替",
    roleLabel: role ? SESSION_ROLE_LABELS[role] || "工作区成员" : "尚未核验",
    accessAllowed: trusted && access?.allowed === true,
    authorizationLabel: AUTHORIZATION_LABELS[reason] || "服务端授权策略",
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
    connection: graphConnection(payload.graph_connection),
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

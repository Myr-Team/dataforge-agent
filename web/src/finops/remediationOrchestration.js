export const REMEDIATION_CONFLICT_MESSAGE = "方案已更新，请重新复核";

const SAFE_IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/;
const SAFE_REVISION = /^[A-Za-z0-9._:-]{1,128}$/;
const FAILURE_MESSAGES = Object.freeze({
  create: "整改草案保存失败",
  review: "整改草案复核失败",
  promote: "审批动作草案创建失败",
});


function identifier(value) {
  const clean = String(value || "").trim();
  return SAFE_IDENTIFIER.test(clean) ? clean : "";
}


function revision(value) {
  if (typeof value === "number" && Number.isInteger(value) && value >= 1) return value;
  const clean = String(value || "").trim();
  return SAFE_REVISION.test(clean) ? clean : "";
}


function boundedReason(value) {
  return String(value || "").trim().slice(0, 300);
}


function mutationRequest({ kind, workspaceId, opportunity, draft, reason }) {
  if (kind === "create") {
    const workspace = identifier(workspaceId);
    const sourceOpportunityId = identifier(opportunity?.id);
    const baseVersion = revision(opportunity?.baseVersion);
    if (!workspace || !sourceOpportunityId || !baseVersion) return null;
    return {
      args: [{ workspaceId: workspace, sourceOpportunityId, baseVersion }],
      failure: FAILURE_MESSAGES.create,
    };
  }
  if (!["review", "promote"].includes(kind)) return null;
  if (kind === "promote" && draft?.executionCapability !== "typed_action_available") return null;
  const draftId = identifier(draft?.id);
  const baseRevision = revision(draft?.revision);
  if (!draftId || typeof baseRevision !== "number") return null;
  const cleanReason = boundedReason(reason);
  return {
    args: [draftId, {
      baseRevision,
      ...(cleanReason ? { reason: cleanReason } : {}),
    }],
    failure: FAILURE_MESSAGES[kind],
  };
}


export async function orchestrateRemediationMutation({
  kind,
  workspaceId = "",
  opportunity = null,
  draft = null,
  reason = "",
  clients = {},
  reloadLatest = null,
  refreshRisk = null,
}) {
  const request = mutationRequest({ kind, workspaceId, opportunity, draft, reason });
  const client = clients?.[kind];
  if (!request || typeof client !== "function") {
    return { status: "failed", error: FAILURE_MESSAGES[kind] || "整改操作失败" };
  }
  try {
    const response = await client(...request.args);
    refreshRisk?.();
    return { status: "succeeded", response };
  } catch (error) {
    if (error?.status === 409) {
      const latest = typeof reloadLatest === "function"
        ? await reloadLatest(REMEDIATION_CONFLICT_MESSAGE)
        : null;
      return {
        status: "conflict",
        error: REMEDIATION_CONFLICT_MESSAGE,
        latest,
      };
    }
    return { status: "failed", error: request.failure };
  }
}

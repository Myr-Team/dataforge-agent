# Tenant FinOps budget authorization design

## Decision

Member budgets, eligible budget members, budget alerts, and tenant email
settings are one tenant-wide FinOps control plane. Every read and write route in
that control plane requires the `DataForge.FinOpsAdmin` Entra application role
from a trusted Easy Auth principal. A workspace Owner/Admin role is neither
required nor sufficient.

The configured role is read from `DF_FINOPS_TENANT_ADMIN_ROLE`. The previous
`DF_FINOPS_EMAIL_ADMIN_ROLE` name remains a deprecated compatibility fallback
only when the new variable is absent or blank. If both are configured, the new
variable wins. Matching trims whitespace and compares the complete role
case-insensitively; substrings and suffixed roles do not match.

## Backend authorization and scope

All budget, alert, and email handlers use one shared context in this order:

1. Check the member-budget feature flag. Email routes first check their
   independent email-configuration flag.
2. Resolve a trusted tenant Easy Auth identity.
3. Require the configured tenant FinOps application role.
4. Require the FinOps HMAC secret and canonicalize tenant and actor references.
5. Enumerate local workspaces and retain only workspaces whose internal trusted
   identity records include the same canonical tenant.
6. Fail closed when no tenant workspace is found.

The resulting complete tenant workspace set is passed to the member directory,
cost reader, alert filter, and email recipient resolver. The evaluator remains
tenant-wide. This aligns interactive reads and writes with the facts used by
evaluation instead of filtering by the caller's workspace memberships.

Mutation audit records use the lexicographically first discovered tenant
workspace as a deterministic storage scope. The selected workspace ID is never
added to API responses. Audit persistence remains mandatory before mutation.

Authorization runs before query/body validation, directory/service calls, and
audit writes wherever the feature flags allow. Unknown, untrusted, missing,
near-match, or client-supplied roles return `403` without revealing tenant data.

## Frontend behavior

An HTTP `403` from the primary member-budget read is a permission state, not a
service failure. The settings home shows:

- `需要租户 FinOps 管理员角色`;
- `预算与提醒已受限`;
- an action labelled `查看权限说明`.

Opening the page shows a compact permission explanation and no budget, cost, or
alert values. It tells the user to request the tenant FinOps application role
and sign in again. Users with the role retain the existing full management
experience.

## Verification

Backend tests cover every budget and alert endpoint, including create, update,
re-enable, disable, list, member list, and alert list. They prove role checks
precede body, workspace, service, and audit work; exact case-normalized matches
are accepted; deprecated fallback and new-variable precedence are deterministic;
and untrusted or near-match roles are denied.

Tenant workspace discovery tests prove only workspaces containing trusted
identities from the same canonical tenant are included and an empty tenant scope
fails closed. Existing service/repository tests continue proving authorized
member filtering before pagination.

Node and Playwright tests cover both settings-home and detail-page permission
copy, hidden data, the non-misleading action label, and unchanged role-holder
functionality. Documentation and the candidate runbook describe the Entra app
role, environment-variable precedence, deterministic audit scope, and
zero-traffic acceptance checks.


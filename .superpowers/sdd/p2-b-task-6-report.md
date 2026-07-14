# P2-B Task 6 Governance Frontend Report

## Outcome

Implemented the governance frontend from base `1544f77` without modifying backend code or tracked `output/` artifacts. The Settings governance view now consumes the Task 1-5 contracts directly and keeps configuration, delivery, local evidence, provider evidence, member chargeback, invitation state, and immutable audit history separate.

## State Matrix

| Surface | State | UI behavior |
|---|---|---|
| All governance evidence | loading | Shows a compact loading row and disables refresh while the request is active. |
| Any evidence endpoint | error | Shows a scoped, sanitized error and retry control; raw server error bodies are not rendered. |
| Azure trace | `not_configured` | Shows `未配置 Azure 遥测`; no delivery claim or transaction link. |
| Azure trace | `partial` | Shows `已配置，尚未确认遥测到达`; local emit/export callback remain distinct from remote confirmation. |
| Azure trace | `connected` | Shows `已确认遥测送达` only with remote delivery evidence; a validated HTTPS transaction link may be shown. |
| Azure trace | `unavailable` | Shows verification unavailable plus an allowlisted error type, never secret-bearing detail. |
| Local ROI | `estimated` | Shows estimates as estimates and preserves unknown values as `未记录`. |
| Local ROI | `measured` | Shows `已测量` with local outcome evidence counts. |
| Local ROI | `verified` | Shows `已验证` only from the local ROI contract. |
| Foundry ROI | `not_configured` / unavailable | Shows an independent provider state and no provider value. |
| Foundry ROI | connected / measured / verified | Shows the signed provider snapshot independently; it never promotes or replaces local ROI. |
| Chargeback | empty / unknown | Shows an honest no-attribution or `未记录` state; `null` never becomes zero. |
| Chargeback | partial / complete | Keeps per-member tokens, evidence status, currency, mixed-currency text, and truncation warning. |
| Invitations | pending / accepted / failed / expired / revoked | Maps the lifecycle states exposed by current member and invitation responses without inferring access from email. |
| Audit | empty | Shows no immutable events recorded. |
| Audit | paged | Reads 25 events per bounded cursor page, appends immutably, and deduplicates revisions. |
| Audit detail | any | Renders only bounded `actor_` and `res_` pseudonyms; raw actor, email, OID, correlation values, and secret-like resource data are ignored. |
| Restricted role | permission denied | Uses audit endpoint permissions as the authority, disables member commands with a concise reason, hides chargeback data, and does not request the restricted chargeback endpoint. |

## File Changes

- `web/src/api.js`: added exact trace, ROI, chargeback, and bounded governance audit pagination clients; HTTP errors now retain status for permission handling.
- `web/src/governanceApi.test.mjs`: verifies encoded paths, date query parameters, cursor encoding, and audit page-size bounds.
- `web/src/governanceViewModel.js`: added truthful trace/ROI/chargeback/invitation/audit adapters, server-permission handling, pseudonym filtering, and immutable page merge.
- `web/src/governanceViewModel.test.mjs`: covers evidence separation, null/empty handling, provider amount contract, permissions, lifecycle states, redaction, pagination, and UTF-8 labels.
- `web/src/components.jsx`: replaced the legacy summary with separate dense governance sections, retry/loading/error/empty states, date-window refresh, permission-gated actions, and paged immutable audit events.
- `web/src/styles.css`: added responsive blue/white operations layout, compact controls/tables, visible focus states, and bounded desktop/mobile overflow behavior.

## TDD Evidence

- Initial governance view-model test failed because `governanceViewModel.js` did not exist.
- Initial API test failed because the four governance API exports did not exist.
- Provider amount regression test failed with `未记录` before support for the Foundry `business_value.amount` contract.
- Null chargeback regression test failed with `0` before null-safe numeric parsing was added.

## Verification

- `node --test src/*.test.mjs` from `web`: **45 passed, 0 failed**.
- `npm run build` from `web`: **passed**, Vite transformed 1,755 modules.
- `git diff --check`: **passed**.
- Playwright acceptance: **14/14 passed** across desktop `1440px` and mobile `390px`.
- Browser checks: **0 console errors, 0 page errors, 0 request failures**; no page overflow; keyboard focus visible; restricted controls disabled; permission-denied fixtures made no chargeback request.
- Machine-readable result: `output/playwright/p2-b-task6-governance-acceptance.json`.

## Screenshot Evidence

- Connected: `output/playwright/p2-b-task6-connected-desktop.png`, `output/playwright/p2-b-task6-connected-mobile.png`
- Partial: `output/playwright/p2-b-task6-partial-desktop.png`, `output/playwright/p2-b-task6-partial-mobile.png`
- Not configured: `output/playwright/p2-b-task6-not_configured-desktop.png`, `output/playwright/p2-b-task6-not_configured-mobile.png`
- Measured: `output/playwright/p2-b-task6-measured-desktop.png`, `output/playwright/p2-b-task6-measured-mobile.png`
- Verified: `output/playwright/p2-b-task6-verified-desktop.png`, `output/playwright/p2-b-task6-verified-mobile.png`
- Permission denied: `output/playwright/p2-b-task6-permission_denied-desktop.png`, `output/playwright/p2-b-task6-permission_denied-mobile.png`
- Audit pagination: `output/playwright/p2-b-task6-audit_pagination-desktop.png`, `output/playwright/p2-b-task6-audit_pagination-mobile.png`

## Concern

The current Task 4 public members contract persists and returns active/pending members, while invite mutation responses can additionally return accepted/failed states. The frontend faithfully supports all five lifecycle labels when supplied, but historical expired/revoked/failed invitation journal entries are not reloadable through a dedicated read endpoint within Task 6's frontend-only ownership.

## Reviewer Remediation (2026-07-14)

This section supersedes the earlier frontend-only scope statement and the concern above. The follow-up adds the required backend read/action contracts and closes the reloadability and identity-display gaps without changing Easy Auth configuration or tracked `output/` content.

### Remediation Matrix

| Finding | Remediation and observed behavior |
|---|---|
| Durable invitation history | Added permission-gated `GET /api/workspaces/{workspace_id}/governance/invitations`. It replays the append-only local/Blob journal, returns one effective row per invitation attempt, survives reload, and preserves separate accepted/failed attempts for the same subject. |
| Invitation state truth | Supports pending, accepted, failed, expired, revoked, and activated-then-revoked as removed. Lifecycle never grants or implies workspace access. |
| Invitation redaction | Returns only `invite_<HMAC>` and `member_<HMAC>` references plus role/state/timestamp. Email, OID, tenant, invited actor, and provider body are excluded. Missing pseudonym salt fails closed. |
| Explicit action permissions | Members, invitation history, chargeback, and audit contracts expose `permissions.actions` for `audit.read`, `chargeback.read`, `invitation.read`, and `member.manage`. The frontend treats missing or non-true fields as denied and displays the server reason. |
| Foundry ROI shape | Parses the production `foundry_roi.status.state` object and preserves configured-unverified, connected, and provider evidence semantics independently from local ROI. |
| Settings identity privacy | Public member rows now use `subject_label` and exclude email/OID/tenant. Role update and removal accept the safe member reference. The member table, directory results, and account menu do not render raw identity values; email appears only in the editable invitation input. |
| Independent invitation state | Invitation history has its own loading/error/data/retry state. Member permission failure does not render a false empty invitation state, and retry reloads the members endpoint before the invitation endpoint. |
| Stale audit requests | Cursor requests capture workspace and request generation. Responses from an old workspace/generation cannot update the current workspace. |
| Cursor retry | A failed page keeps all loaded events and its failed cursor. Retry requests that same cursor and appends immutably after success. |
| UTF-8 and responsive behavior | Chinese labels contain no literal `?`, replacement characters, or tested mojibake patterns. Desktop and 390px mobile fixtures have no page/governance horizontal overflow and retain keyboard focus. |

### Follow-up File Changes

- `backend/invitation_store.py`: durable invitation history replay, fail-closed HMAC pseudonyms, and stable safe member subject labels.
- `backend/control_plane.py`: invitation-history GET, explicit governance action permissions, redacted member projections, and safe-reference member management.
- `backend/workspace_authz.py`: explicit audit and invitation read capabilities for owner/admin policy.
- `backend/roi_service.py`: server-generated chargeback subject labels.
- `tests/test_entra_member_invites.py`: history states, same-subject terminal attempts, reload, redaction, and safe-reference management.
- `tests/test_actor_audit_usage.py`: endpoint authz/redaction, explicit permissions, and safe settings member contract.
- `tests/test_roi_service.py`: bounded subject-label coverage for active and departed chargeback rows.
- `web/src/api.js`: exact invitation-history API client.
- `web/src/governanceViewModel.js`: nested Foundry parsing, strict permission adapter, redacted invitation/chargeback/member projections.
- `web/src/governanceRequestState.js`: workspace-generation guard and cursor failure/success reducers.
- `web/src/components.jsx`: independent invitation state/retry, safe member controls, and stale/cursor guarded audit pagination.
- `web/src/governanceApi.test.mjs`, `web/src/governanceViewModel.test.mjs`, `web/src/governanceRequestState.test.mjs`: API, production-shape, privacy, permission, and deterministic request-state coverage.
- `web/src/styles.css`: compact accessible retry control styling.

### Exact Follow-up Verification

- `python -m pytest tests/test_entra_member_invites.py tests/test_actor_audit_usage.py tests/test_roi_service.py tests/test_foundry_roi.py tests/test_workspace_roles.py -q`: **184 passed in 15.37s**.
- `node --test src/*.test.mjs` from `web`: **50 passed, 0 failed** in 428.98ms.
- `npm run build` from `web`: **passed**, Vite transformed **1,756 modules** in 1.28s.
- `git diff --check`: **passed**.
- Playwright review acceptance: **24/24 passed** across desktop `1440x1000` and mobile `390x844`.
- Browser assertions: **0 unexpected console errors, 0 page errors, 0 request failures**, no horizontal overflow, keyboard focus retained, no raw email/OID/tenant, and no literal `?`/mojibake labels.
- The four expected browser console resource errors are the deliberately injected HTTP 503 responses for member-load and cursor-retry fixtures; each error produced the intended scoped retry UI and successful retry.
- Machine-readable results: `output/playwright/p2-b-task6-review-acceptance.json`.

### Follow-up Screenshot Evidence

- Connected: `output/playwright/p2-b-task6-review-connected-desktop.png`, `output/playwright/p2-b-task6-review-connected-mobile.png`
- Partial: `output/playwright/p2-b-task6-review-partial-desktop.png`, `output/playwright/p2-b-task6-review-partial-mobile.png`
- Not configured: `output/playwright/p2-b-task6-review-not_configured-desktop.png`, `output/playwright/p2-b-task6-review-not_configured-mobile.png`
- Measured: `output/playwright/p2-b-task6-review-measured-desktop.png`, `output/playwright/p2-b-task6-review-measured-mobile.png`
- Verified: `output/playwright/p2-b-task6-review-verified-desktop.png`, `output/playwright/p2-b-task6-review-verified-mobile.png`
- Nested Foundry status: `output/playwright/p2-b-task6-review-nested_foundry-desktop.png`, `output/playwright/p2-b-task6-review-nested_foundry-mobile.png`
- Permission denied: `output/playwright/p2-b-task6-review-permission_denied-desktop.png`, `output/playwright/p2-b-task6-review-permission_denied-mobile.png`
- Reloadable invitation/member privacy: `output/playwright/p2-b-task6-review-invitation_reload-desktop.png`, `output/playwright/p2-b-task6-review-invitation_reload-mobile.png`
- Member load failure/retry: `output/playwright/p2-b-task6-review-member_retry-desktop.png`, `output/playwright/p2-b-task6-review-member_retry-mobile.png`
- Audit pagination: `output/playwright/p2-b-task6-review-audit_pagination-desktop.png`, `output/playwright/p2-b-task6-review-audit_pagination-mobile.png`
- Cursor failure/same-cursor retry: `output/playwright/p2-b-task6-review-cursor_retry-desktop.png`, `output/playwright/p2-b-task6-review-cursor_retry-mobile.png`
- Stale workspace response: `output/playwright/p2-b-task6-review-stale_workspace-desktop.png`, `output/playwright/p2-b-task6-review-stale_workspace-mobile.png`

### Remaining Risk

Safe invitation/member labels depend on a stable server-only `DF_INVITATION_PSEUDONYM_SALT` or existing `DF_WEB_PROXY_SECRET`. The contract intentionally returns 503 instead of emitting guessable labels when neither is configured; rotating that salt changes displayed pseudonyms but does not alter journal history or authorization.

## R2 Important-Finding Remediation (2026-07-14)

This pass is limited to the four R2 Important findings and the directly related safe member-reference route cleanup. Easy Auth configuration and tracked `output/` content were not changed.

| R2 finding | Implemented result |
|---|---|
| Workspace switch isolation | Member rows, member metadata, derived governance permissions, directory search state, invitation state, governance evidence, audit rows, next cursor, and retry cursor are cleared or workspace-bound immediately. Member and restricted governance actions remain unavailable until the new workspace members contract loads successfully. |
| Audit generation isolation | Audit page tokens now require the active workspace and matching data workspace. A new generation cannot capture an old workspace cursor, and stale responses cannot commit. |
| API identity removal | The public members usage projection and chargeback rows expose bounded `subject_label` values instead of raw email, actor ID, tenant ID, or name. Tests inspect the complete serialized response. |
| Cross-contract attribution | Members, invitation history, and chargeback use the shared server-only member pseudonym helper and salt resolution. The same workspace identity receives the same `member_<HMAC>` label across all three contracts. |
| Safe member mutation references | PATCH/DELETE resolve only `member_<HMAC>` subject references. Raw email route input is rejected; route and helper parameters are named `subject_ref`. |

### R2 Files Changed

- `backend/control_plane.py`: workspace-safe member usage projection, subject-reference-only mutations, and shared public labels.
- `backend/invitation_store.py`: shared member pseudonym salt resolution.
- `backend/roi_service.py`: redacted chargeback member projection using the shared pseudonym helper.
- `tests/test_actor_audit_usage.py`: complete serialized member/chargeback identity-redaction assertions.
- `tests/test_entra_member_invites.py`: shared-label contract and raw-email mutation rejection coverage.
- `tests/test_roi_service.py`: chargeback redaction coverage.
- `web/src/api.js`: subject-reference member mutation parameters.
- `web/src/components.jsx`: immediate workspace-bound member, permission, invitation, governance, and audit reset behavior.
- `web/src/governanceRequestState.js`: member/governance workspace binding and cursor-generation checks.
- `web/src/governanceRequestState.test.mjs`: switch-window and old-cursor capture regression tests.

### R2 Verification Before Commit

- `python -m pytest tests/test_entra_member_invites.py tests/test_actor_audit_usage.py tests/test_roi_service.py tests/test_foundry_roi.py tests/test_workspace_roles.py -q`: **184 passed in 14.21s**.
- `node --test src/*.test.mjs` from `web`: **50 passed, 0 failed** in 380.06ms.
- `npm run build` from `web`: **passed**, Vite transformed **1,756 modules** in 1.30s.
- `git diff --check`: **passed**.
- R2 Playwright desktop/mobile rerun: **pending after this commit at the user's direction**. The earlier follow-up's 24/24 result and screenshot paths above predate the R2 changes and are not claimed as R2 evidence.

### R2 Remaining Verification

Run the existing desktop/mobile review fixtures after commit, with emphasis on the workspace-switch loading window, old-cursor rejection, member action disablement, and absence of raw identity in rendered governance surfaces. Store all resulting screenshots and machine-readable results only under untracked `output/`.

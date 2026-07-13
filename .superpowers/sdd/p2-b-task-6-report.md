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

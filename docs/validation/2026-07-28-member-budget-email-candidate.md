# Entra member budget and ACS Email candidate acceptance record

## Scope and decision

This record separates local automated evidence from live Azure and email
acceptance. It is not production approval.

**Current decision: local automated gate PASS; live candidate release gate
`PENDING — NOT ACCEPTED`.**

No Azure inventory, resource creation, SQL migration, managed-identity/RBAC
change, deployment, job creation, email send, or traffic change is evidenced by
this document.

## Required candidate configuration

```text
DF_FINOPS_MEMBER_BUDGETS_ENABLED=1
DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1
DF_FINOPS_EMAIL_ALERTS_ENABLED=0
DF_FINOPS_ACTIONS_ENABLED=0
```

All four settings default to `0` in source configuration. ACS uses only the
backend system-assigned managed identity; no connection string, service key, or
SMTP credential is accepted.

## Local automated regression

The full local product gate was run once against exact tested commit
`83ee9e4ffd8aa0cea54cb6fd82ad7b2882240175`. A later runbook-only remediation
was tested at `f8e5c066f562e542370b2167493060b9c7fa63ae`: it changes the
fail-closed `linkedDomains` shape check and adds its focused test, but does not
change executable product code. The earlier full product gate was not rerun;
the focused runbook gate is recorded separately below and in the manifest.

| Check | Result | Evidence |
| --- | --- | --- |
| `python -m pytest -q` | PASS — 1484 passed, 1 skipped, 1 warning | [Pytest output](evidence/2026-07-28-member-budget-email/pytest-stdout.log) |
| `node --test` in `web` | PASS — 158 passed | [Node TAP output](evidence/2026-07-28-member-budget-email/node-test.tap) |
| `npm run build` in `web` | PASS — 1780 modules transformed | [Vite output](evidence/2026-07-28-member-budget-email/vite-build.log); the existing chunk-size advisory remains. |
| `npx playwright test` in `web` | PASS — 26 passed | [Playwright output](evidence/2026-07-28-member-budget-email/playwright.log) |
| `git diff --check` | PASS — clean | Recorded in the [evidence manifest](evidence/2026-07-28-member-budget-email/manifest.json). |
| Runbook `linkedDomains` validation | PASS — 10 focused tests; 14 PowerShell blocks parsed with 0 syntax errors | [Runbook validation](evidence/2026-07-28-member-budget-email/runbook-validation.log). |
| Local desktop/mobile screenshot review | PASS — legible, aligned, and no visible mobile horizontal overflow | Local-only paths listed below; not live-candidate evidence. |

The manifest records exact commands, UTC intervals, tool versions, exit codes,
sanitization, and SHA-256 hashes. Local PASS proves only the software regression
gate; it does not promote any live Azure row below.

Evidence manifest SHA-256:
`BDDB3CDFC9D6776F7F0B3314044FE77698880BBB04E446F4720DC9035EE7D684`.

Expected local screenshot paths:

- `output/playwright/task6-member-budget-entry-desktop.png`
- `output/playwright/task6-member-budget-page-desktop.png`
- `output/playwright/task6-member-budget-page-mobile.png`

Screenshots are local Playwright artifacts and are not committed. They do not
prove live identity, cost, ACS, or device-persistence behavior.

## Live Azure and candidate acceptance

Every item is `PENDING — NOT ACCEPTED`. The repeated read-only inventory
attempts did not establish a reliable current Azure baseline, and the resource
group, region/data residency, ACS names/domain/sender, administrator recipient,
and minimum role assignment have not been explicitly approved for execution.

| Acceptance item | Status | Required redacted evidence |
| --- | --- | --- |
| Approved RG, region/data location, ACS names, managed domain, sender, and administrator recipient | PENDING — NOT ACCEPTED | Human approval, names, and UTC timestamp only. |
| Email Communication Services, Azure Managed Domain, linked Communication Services resource | PENDING — NOT ACCEPTED | Validated pre/post reads, resource names, link counts, preservation result, protected rollback-record reference, separately approved exact-set restore result, and outcome only; no IDs. |
| Backend system-assigned managed identity has minimum ACS Email send role | PENDING — NOT ACCEPTED | Role name/definition and assignment outcome only; no principal/resource IDs. |
| Additive SQL migration completes twice and required objects exist | PENDING — NOT ACCEPTED | UTC timestamps, exit status, and object/constraint/index names. |
| Exact approved commit is checked out in an isolated workspace with zero tracked modifications and zero untracked files | PENDING — NOT ACCEPTED | Commit match and clean-status result only. |
| Docker exclusions are verified and filesystem-level pre-build scan finds no local environment/private credential artifact | PENDING — NOT ACCEPTED | Exclusion check, scan exit status, and zero count only; no file names or contents. |
| Immutable backend and web images built from the tested release commit | PENDING — NOT ACCEPTED | Commit, immutable digests, and build timestamps. |
| Before any candidate creation, both apps are `Multiple`, explicitly route named stable revision at 100%, and have no `latestRevision` route | PENDING — NOT ACCEPTED | Safe app/revision names, mode, and traffic summary. |
| Backend and web candidate revisions are Healthy at 0% traffic | PENDING — NOT ACCEPTED | Revision names, health, traffic, and rollback targets. |
| Health and authentication checks | PENDING — NOT ACCEPTED | HTTP status categories for health, unauthenticated, Owner/Admin, and member. |
| Active administrator recipient can be saved; member/external recipient is denied | PENDING — NOT ACCEPTED | Safe outcome categories only; do not record email addresses. |
| One `[Test]` / `[测试]` ACS Email is received | PENDING — NOT ACCEPTED | Accepted/received UTC timestamps and safe status only; no ACS message ID. |
| Configuration persists after reload and on a second device | PENDING — NOT ACCEPTED | UTC observation and pass/fail only. |
| One member's current-month spend manually reconciles to `$190 / $200` | PENDING — NOT ACCEPTED | UTC window, priced/total counts, amount, coverage, and price-card lineage. |
| Unpriced requests reduce coverage and are not treated as zero cost | PENDING — NOT ACCEPTED | Counts, coverage, and pass/fail only. |
| Desktop/mobile signed-in UI and truthful states pass visual review | PENDING — NOT ACCEPTED | Screenshot paths and review result; no sensitive identity/email. |
| 15-minute Container Apps Job is created last with alerts off | PENDING — NOT ACCEPTED | Job name, schedule, image digest, and flag state. |
| Controlled 95% alert sends once and rerun deduplicates | PENDING — NOT ACCEPTED | Opaque alert reference, timestamps, and duplicate count. |
| Direct jump above 100% sends only highest threshold | PENDING — NOT ACCEPTED | Threshold/state summary only. |
| Automatic alerts are restored to `0` after the test | PENDING — NOT ACCEPTED | Final verified flag state and timestamp. |
| Redacted candidate logs contain no critical or secret-like signals | PENDING — NOT ACCEPTED | Bounded UTC window and zero/non-zero category counts only. |

## Production gate

Do not switch production traffic and do not enable automatic reminders based on
local tests alone. The operator must follow the
[candidate runbook](2026-07-28-member-budget-email-candidate-runbook.md), fill
every live row above with admissible redacted evidence, record rollback
revisions, and obtain explicit human approval.

When approved, promote backend before web, recheck health and critical logs,
and enable the scheduled job last. Keep `DF_FINOPS_ACTIONS_ENABLED=0`.

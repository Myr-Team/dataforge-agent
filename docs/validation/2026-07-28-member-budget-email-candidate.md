# Entra member budget and ACS Email candidate acceptance record

## Scope and decision

This record separates local automated evidence from live Azure and email
acceptance. It is not production approval.

**Current decision: local gate awaiting execution; live candidate release gate
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

The exact tested commit and evidence hashes will be added in a provenance-only
follow-up commit after the documentation commit has been tested unchanged.

| Check | Result | Evidence |
| --- | --- | --- |
| `python -m pytest -q` | NOT RUN | Pending local full gate. |
| `node --test` in `web` | NOT RUN | Pending local full gate. |
| `npm run build` in `web` | NOT RUN | Pending local full gate. |
| `npx playwright test` in `web` | NOT RUN | Pending local full gate. |
| `git diff --check` | NOT RUN | Pending local full gate. |

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
| Email Communication Services, Azure Managed Domain, linked Communication Services resource | PENDING — NOT ACCEPTED | Resource names and link outcome only; no IDs. |
| Backend system-assigned managed identity has minimum ACS Email send role | PENDING — NOT ACCEPTED | Role name/definition and assignment outcome only; no principal/resource IDs. |
| Additive SQL migration completes twice and required objects exist | PENDING — NOT ACCEPTED | UTC timestamps, exit status, and object/constraint/index names. |
| Immutable backend and web images built from the tested release commit | PENDING — NOT ACCEPTED | Commit, immutable digests, and build timestamps. |
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

# FinOps Operations Closure Validation — 2026-08-10

## Release scope

- Commit under test: `3719ab3` (`feat(finops): close provider budget and roi demo gaps`)
- Pull request: [Myr-Team/dataforge-agent#29](https://github.com/Myr-Team/dataforge-agent/pull/29)
- Release mode: backend and web zero-traffic candidates before production promotion
- Security boundary: Easy Auth, tenant/workspace RBAC, audit persistence, write-only provider secrets, and explicit provider governance remain enabled
- Production actions: `DF_FINOPS_ACTIONS_ENABLED=0`
- Automatic budget email alerts: remain disabled until a test message has recipient-delivery evidence

## Repository gates

| Gate | Result |
| --- | --- |
| `python -m pytest -q` | 1855 passed, 1 skipped |
| `node --test` (`web/`) | 308 passed |
| `npm run build` (`web/`) | passed; 1795 modules transformed; existing bundle-size warning only |
| `npx playwright test` on a unique port | 63 passed |
| `git diff --check` | clean |
| Gitleaks staged scan | 0 leaks |

## Visual acceptance

The following Playwright screenshots were inspected at desktop and responsive boundaries:

- DeepSeek provider governance and model assignment
- Official DeepSeek input, cache-hit, cache-miss, and output pricing
- Cost and token trend tooltip, including distinct cached-token color and data-scaled bars
- Cost analysis, ROI case narrative, and risk decision views
- Member budget page with verified enterprise identity labels

The review confirmed no permanent values inside trend bars, no refresh-control blank width, no tooltip clipping, and no raw member pseudonym as the primary label when a verified enterprise identity is available.

## Target-environment database migration

The additive FinOps schema migration ran through a dedicated user-assigned migration identity and a manual Container Apps job. The backend runtime identity was not elevated.

| Execution | Result |
| --- | --- |
| Initial migration | Succeeded |
| Idempotency rerun | Succeeded |

## Zero-traffic candidate evidence

- Backend candidate: Healthy, one ready replica, exact tested image digest, zero production traffic
- Web candidate: Healthy, one ready replica, exact tested image digest, zero production traffic
- Existing stable backend and web revisions retained 100% production traffic throughout candidate validation
- Candidate web points to the candidate backend revision, not the previous stable backend
- Direct in-environment candidate health returned HTTP 200
- Required Foundry, Search, MCP, Speech, Blob, and Content Safety probes returned healthy; optional ROI provider remains intentionally unconfigured because ROI is calculated by DataForge
- Anonymous access to the candidate web revision redirected to Easy Auth; authentication was not bypassed

## Candidate business-contract acceptance

The deployed backend candidate executed 33 authenticated, workspace-scoped API requests against the demonstration workspace. Safe aggregate results:

- 30 trend buckets with distinct request, token, and cost geometry
- 11 Agent rows, including priced and unpriced states
- 6 price-catalog entries and 6 active price mappings; required models available
- One active member budget with spend, forecast, and threshold data
- ROI value bridge, four evidence stages, 22 openable request references, one bounded scenario case
- Six risk matrix points, six priorities, six optimization portfolio points, and six distinct evidence sets
- Selected risk evidence was bound to the AI question context
- Model routing confirmed the operations-analysis workload on the governed Terra route
- Request detail and evidence drill-down contracts completed successfully

## Email delivery gate

Azure Communication Services diagnostic logs for send and recipient-status events are routed to the configured Log Analytics workspace. The backend managed identity has the scoped Communication and Email Service role required for test sending.

The product now distinguishes:

1. `accepted`: the email service accepted the request;
2. `delivered`: recipient-level status evidence exists in Azure Monitor;
3. terminal safe failure states such as bounced or suppressed.

Provider message identifiers remain internal. Automatic budget alerts must not be enabled until a test message reaches `delivered`.

Live acceptance found that the current Azure Monitor SDK exposes query column names as strings, while the initial adapter handled only column objects. A production-shaped regression test now covers both forms; the focused email/budget suite passed 60 tests and the complete Python suite passed 1855 tests after the compatibility fix.

## Rollback

- Backend rollback target: the stable revision that held 100% traffic before this release
- Web rollback target: the stable revision that held 100% traffic before this release
- Rollback order: web first, then backend
- SQL changes are additive and do not require destructive rollback

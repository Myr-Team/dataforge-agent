# Operations Management evidence, pricing, and production acceptance

Date: 2026-07-26

## Released scope

- The primary navigation renders Business Workspace, Operations Governance, and
  System groups immediately. Operations Management and Run History are both
  retained.
- Operations Management provides the overview, cost analysis, efficiency and
  ROI, and risk and optimization views.
- Six core metrics expose evidence-state tooltips and request drill-down:
  estimated cost, calls, tokens, success rate, P95 latency, and cache hit rate.
- Trend columns use a shared zero baseline and a dedicated plot slot, so the
  rendered height follows the real value without near-maximum clipping.
- Operations AI is a compact popover. Conversations persist in SQL and can be
  resumed across devices. The client and server both bound historical context
  to 600 characters per turn.
- Customer-facing evidence uses `workspace · operation · time`. Raw request,
  provider, and correlation identifiers remain available only in technical
  details.
- Agent model assignments are stored server-side. Request facts retain the
  actual route and deployment used for model-level aggregation.
- Model cost is an estimate from an owner-managed mapping to the official
  price catalog. A request that cannot be matched reliably remains `unpriced`;
  the UI exposes a small edit action for creating the mapping.

## Automated verification

- Python: `1204 passed, 1 skipped`.
- Node: `129 passed, 0 failed`.
- Vite production build: `1772` modules transformed.
- Playwright: `6 passed`.
- Focused regression:
  - a second Operations AI question succeeds after a persisted assistant answer
    longer than 600 characters;
  - historical raw `req_*` values are removed from customer-facing messages;
  - the trend plot has a dedicated height slot and no clipping max-height.

## Production artifacts and traffic

- Backend image:
  `dataforge-backend@sha256:1514a5d5ca2b567983936ea89cc2b8406ed761c3ee98ebef10f41c8b596a10dd`
- Backend production revision:
  `ca-dataforge-backend--ops1596d4f`, Healthy, one replica, 100% traffic.
- Web image:
  `dataforge-web@sha256:a459ad22a061e5cc006751f25fb65b17bf72dc952b3a33702aef669466ba2993`
- Web production revision:
  `ca-dataforge-web--ops4769b33`, Healthy, one replica, 100% traffic.
- The web revision proxies to the exact backend revision URL.
- `DF_FINOPS_READ_ENABLED=1`.
- `DF_FINOPS_ACTIONS_ENABLED=0`; production governance execution remains
  disabled.
- Previous revisions remain retained at zero traffic as rollback points.

## Authenticated production UI evidence

The production page was force-refreshed in an authenticated browser after the
traffic switch.

- All three primary navigation groups were present in the first DOM snapshot.
- The page title is `运营管理`; the update indicator and refresh button share
  the same vertical center.
- No `Failed to fetch` text or application console error was present.
- The 13,437-token and 14,045-token columns rendered at 158 px and 165 px,
  respectively. Their inline ratios were 95.6711% and 100%.
- Operations AI restored an earlier persisted question and answer after reload.
- A second live question completed successfully after that long answer.
- The response cited friendly evidence such as
  `选址情报演示（模拟） · 分析运行 · 7月24日 18:12`.
- No raw `req_*` identifier appeared in the page or AI response.

## SQL and privacy state

- Additive FinOps SQL tables include official price mappings and persisted
  assistant conversations/messages.
- APIM diagnostics keep request and response body capture at zero bytes.
- Prompts, completions, credentials, raw identities, and provider error bodies
  are not collected by the FinOps request fact.

## APIM reconciliation acceptance boundary

The scheduled `job-dataforge-finops-apim` job was updated to the released
backend digest while preserving its user-assigned identity, five-minute
schedule, 300-second timeout, and retry limit of two. Manual execution
`job-dataforge-finops-apim-ba6f07q` succeeded.

The latest ten-minute result was:

```json
{
  "status": "completed",
  "scope_count": 0,
  "application_events": 0,
  "apim_observations": 0,
  "reconciled_events": 0
}
```

This proves the collector path executes, but it does **not** satisfy the
non-zero APIM correlation acceptance gate. The Portal therefore correctly
shows APIM reconciliation as pending rather than verified.

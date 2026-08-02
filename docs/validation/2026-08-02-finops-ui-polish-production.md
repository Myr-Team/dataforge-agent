# FinOps ROI / risk UI polish production evidence (2026-08-02)

## Release scope

This was a web-only release. It refines the Operations Management ROI and
risk decision views, moves chart help into viewport-level tooltips, and
prefetches the workspace-scoped Operations AI history. The backend, Easy Auth,
SQL schema, APIM configuration, and production governance execution setting
were not changed.

Release source:

- Branch: `codex/finops-ui-polish`
- Web source commit: `11c19f3b0693`
- Image: `acrdataforgedev.azurecr.io/dataforge-web:finops-ui-11c19f3b0693`
- Digest: `sha256:9b76a6c0d491cedd9fa7564a919f013074efe10d514b4b309d46458b1760b449`

## Functional acceptance

- ROI presents one decision hierarchy with proportional value bars, compact
  evidence-maturity cards, metric-level AI entry points, and no duplicate
  consultation footer.
- Risk presents four non-overlapping decision quadrants, ranked impact bars,
  linked evidence and remediation details, and no duplicated consultation
  controls.
- Metric and trend help uses one shared document-level tooltip. It clamps to
  the viewport and keeps only one tooltip open, preventing clipping by chart
  or panel boundaries.
- Operations AI history is prefetched once the authorized workspace scope is
  available, cached in memory for five minutes, deduplicated while in flight,
  and guarded against late responses after a clear. Server-side history
  remains the cross-device source of truth.
- The Operations Management refresh interval remains ten minutes and pauses
  while the page is hidden.
- The demo-completeness browser fixture verifies visible values, charts,
  tables, and queues on all four Operations Management views without
  `未接入`, `暂不可用`, `Failed to fetch`, `待接入`, or `未记录` copy.

## Test evidence

Run from the isolated release worktree immediately before image creation:

- `python -m pytest -q`: **1688 passed, 1 skipped**.
- `node --test` from `web/`: **255 passed**.
- `npm run build` from `web/`: **success**, 1791 modules transformed.
- `npx playwright test` from `web/`: **46 passed**.
- `git diff --check`: clean before commit.

The Python suite initially exposed a date-dependent test whose fixed end date
became historical on 2026-08-02. The test now derives an always-current window
and continues to assert the intended closed-rollup boundary. The targeted test
and the complete Python suite both passed after this correction; production
backend behavior was not changed.

Visual evidence is retained locally under `output/playwright/`, including:

- `operations-roi-desktop.png`
- `operations-risk-desktop.png`
- `operations-roi-mobile.png`
- `operations-risk-mobile.png`
- `operations-trend-tooltip-desktop.png`

## Candidate and production evidence

Candidate `ca-dataforge-web--finui11c19f3` was created by copying the stable
web revision and replacing only the image. Before cutover it was verified as:

- `Healthy` and `Running`.
- Explicitly assigned 0% traffic while
  `ca-dataforge-web--opsaug3cd8d44` retained 100%.
- Bound to the immutable image digest above.
- Returning HTTP 401 for anonymous root and `/api/workspaces` requests,
  preserving the Easy Auth boundary.

The in-app browser could not complete the candidate revision-host login flow
within 60 seconds, so no signed-in candidate claim is made. The authenticated
UI acceptance gate for this release is the complete 46-test Playwright suite.

At `2026-08-02T09:37:30Z`, after the authorized production cutover:

- `ca-dataforge-web--finui11c19f3`: `Healthy`, `Running`, 100% traffic.
- `ca-dataforge-web--opsaug3cd8d44`: `Healthy`, `Running`, 0% traffic.
- Stable web root and `/api/workspaces`: HTTP 401 anonymously, as expected.
- Stable backend `/api/health`: HTTP 200.
- Backend remained `ca-dataforge-backend--opsaug3cd8d44` at 100% traffic.
- `DF_FINOPS_ACTIONS_ENABLED` remained `0`.
- New web revision logs contained a normal Nginx startup and no application
  startup error.

Production URL:

`https://ca-dataforge-web.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`

## Rollback

The previous web revision remains healthy and can be restored without a new
build:

```powershell
az containerapp ingress traffic set `
  --name ca-dataforge-web `
  --resource-group rg-dataforge-dev `
  --revision-weight `
    "ca-dataforge-web--finui11c19f3=0" `
    "ca-dataforge-web--opsaug3cd8d44=100"
```

After rollback, recheck revision health, stable-domain authentication, and
backend `/api/health` before declaring recovery complete.

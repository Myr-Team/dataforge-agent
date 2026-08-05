# 2026-08-05 production release: load reliability and FinOps UI remediation

## Release source

- Repository: `Myr-Team/dataforge-agent`
- Pull request: `#25`
- PR head: `8058cd588ed1efac2578f6b061390574b4c0203d`
- Merged `main`: `cbc3f1ff79fa8634dec46d7afdc5ecba7eb987d6`
- The merged tree was verified to match the PR head.
- Release window evidence captured at `2026-08-05T14:47:07Z`.
- This release contains no SQL migration and does not change Easy Auth or Entra configuration.

## Pre-release verification

- Python: `1715 passed, 1 skipped, 1 warning`
- Node: `284 passed, 0 failed`
- Vite production build: passed (`1793` modules; existing bundle-size warning only)
- Playwright: `56 passed`
- `git diff --check`: clean
- High-confidence secret scan of the release diff: no AWS, GitHub, OpenAI, Azure connection-string, private-key, or JWT credential patterns found.

## Immutable images

- Backend: `acrdataforgedev.azurecr.io/dataforge-backend@sha256:d5533aaa2db5c1408a2b9eca33c504a20615dc9a3b7c62489536c66acc1d61a9`
- Web: `acrdataforgedev.azurecr.io/dataforge-web@sha256:d150d44da9be01645b887e61ec76b011576d23ec164e1f33f29ddbcd42efb946`
- The backend image build re-ran the ODBC Driver 18 registration check, lineage SQL prerequisites check, and import smoke test.
- The web image build re-ran the Vite production build.

## Candidate and production state

### Backend

- New revision: `ca-dataforge-backend--prodcbc3f1b1`
- State after promotion: `Healthy`, `Provisioned`, `1` replica, `100%` traffic.
- Rollback revision retained: `ca-dataforge-backend--gov098e2ca`.
- Candidate revision `/api/health`: `200` before promotion.
- Candidate revision comparison sample: `5/5` HTTP `200`, approximately `0.90-1.75s`.
- Stable production sample immediately after promotion: `5/5` HTTP `200`.
- Final stable sample: `2/3` HTTP `200`; one connection establishment timed out at the six-second client limit and returned no HTTP status. No application `5xx` was observed.
- Candidate log classification: `0` ERROR, `0` Exception/Traceback/CRITICAL, `0` HTTP `5xx`.

### Web

- New revision: `ca-dataforge-web--prodcbc3f1w1`
- State after promotion: `Healthy`, `Provisioned`, `2` replicas at the final snapshot, `100%` traffic.
- Rollback revision retained: `ca-dataforge-web--finui-a5ae457`.
- Candidate root and `/api/workspaces`: each `3/3` HTTP `401`, matching the existing anonymous Easy Auth boundary and returning no `5xx`.
- Stable production root after promotion: `3/3` HTTP `401` for anonymous requests.
- Stable production API proxy after promotion: `3/3` HTTP `401` for anonymous requests.

## Runtime safety gates

The promoted backend revision retained the expected values:

- `DF_FINOPS_READ_ENABLED=1`
- `DF_FINOPS_ACTIONS_ENABLED=0`
- `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`
- `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`

No governance action executor, external provider routing, or external gateway provisioning was enabled by this release.

## UI acceptance boundary

- Authenticated production workspace first screen: visually verified after promotion.
- Business workspace content and all left navigation groups appeared together; the page did not remain on the previous operations-permission spinner.
- Cost Management and Risk & Optimization entries were present in the production navigation.
- The browser-control channel repeatedly timed out while attempting to enter Cost Management, so authenticated production screenshots of the Cost, ROI, Risk, and Operations AI views were **not** completed in this release window.
- The corresponding flows remain covered by the passing `56`-test Playwright suite, but that does not replace the outstanding authenticated production visual walkthrough.

## Rollback

Backend rollback:

```powershell
az containerapp ingress traffic set --name ca-dataforge-backend --resource-group rg-dataforge-dev --revision-weight ca-dataforge-backend--gov098e2ca=100 ca-dataforge-backend--prodcbc3f1b1=0
```

Web rollback:

```powershell
az containerapp ingress traffic set --name ca-dataforge-web --resource-group rg-dataforge-dev --revision-weight ca-dataforge-web--finui-a5ae457=100 ca-dataforge-web--prodcbc3f1w1=0
```

## Remaining manual acceptance

Using an authenticated production browser, verify Cost Management, ROI, Risk & Optimization, evidence details, and Operations AI history/friendly error rendering. Record screenshots only after the pages have loaded with production data. The single observed connection-level timeout should also be monitored separately from application request latency.

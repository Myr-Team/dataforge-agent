# FinOps governance production evidence (2026-08-03)

## Release scope

This release promotes PR #20 after the PR #21 review fixes. It adds the
workspace-scoped FinOps governance and evidence fixes, aligns the web decision
model with the backend risk-finding contract, and keeps mobile chart tooltips
inside the viewport. Easy Auth, model-provider secrets, and production
governance execution were not changed.

Release source:

- Team main commit: `098e2ca8f9a78b5f73739f0261f5651abf15c2bf`.
- PR #20: `https://github.com/Myr-Team/dataforge-agent/pull/20`.
- Review-fix PR #21: `https://github.com/Myr-Team/dataforge-agent/pull/21`.
- Backend image digest:
  `sha256:1c08b212cdf810642f1d8190332773424d745546adc0d42421e0d55c615e88d5`.
- Web image digest:
  `sha256:3a0118056ebb30b8283dd2891f12cf88abc391c08d496dfffd6dac95002684fa`.

## Review fixes

- Workspace-scoped evidence and request-detail authorization now checks the
  selected workspace role. Unscoped organization-wide reads retain the
  stricter all-workspace owner/admin rule.
- A completed risk scan prefers its persisted finding evidence references and
  retains surviving references when only part of the set has expired. Live
  evidence selection is used only when every stored reference has expired.
- The web risk view matches scan findings using the backend's actual
  `subject_type="risk"` shape and `policy_type ?? subject_id` identity.
- The mobile viewport test again verifies that the decision-chart tooltip is
  unique and remains within a 390 px viewport. Keyboard focus suppresses
  incidental pointer tooltips during focus-induced scrolling.

## Verification before release

Run against the same source tree used to build both images:

- `python -m pytest -q`: **1704 passed, 1 skipped**.
- `node --test` from `web/`: **271 passed**.
- `npm run build` from `web/`: **success**, 1792 modules transformed.
- `npx playwright test` from `web/`: **52 passed**.
- `git diff --check`: clean.

## Database migration

The SQL delta is additive and idempotent. It adds only
`df_finops.risk_scan` and `df_finops.risk_scan_finding`; it contains no drop
or data rewrite. The migration was executed twice and both tables were then
verified present.

The SQL server firewall was opened only for the observed deployment-client
address using the exact temporary rule `codex-finops-gov-20260803`. The rule
was removed in the cleanup path and its absence was reverified after the
migration. No address, credential, or connection string is recorded here.

## Candidate and production acceptance

Both candidates were created at zero traffic and reached `Healthy` and
`Running` before promotion:

- Backend candidate: `ca-dataforge-backend--gov098e2ca`.
- Web candidate: `ca-dataforge-web--gov098e2ca`.
- The web candidate points to the backend candidate revision FQDN.
- Anonymous requests to the candidate web root and `/api/workspaces` returned
  HTTP 401, preserving Easy Auth.
- Direct backend candidate `/api/health` returned HTTP 200 three times.
- Candidate backend and web logs contained no `ERROR`, `CRITICAL`, traceback,
  or unhandled-exception signal.

The revision-specific web hostname is not registered as an Entra redirect URI
and therefore returned `AADSTS50011`. No callback whitelist was added for this
temporary candidate hostname. Authenticated acceptance was completed on the
existing stable hostname immediately after cutover.

At `2026-08-03T07:34:08Z`, production was promoted backend-first and then web:

- `ca-dataforge-backend--gov098e2ca`: 100% traffic, `Healthy`, `Running`.
- `ca-dataforge-web--gov098e2ca`: 100% traffic, `Healthy`, `Running`.
- Stable backend `/api/health`: HTTP 200 in five consecutive checks.
- Stable anonymous web root and `/api/workspaces`: HTTP 401.
- `DF_FINOPS_READ_ENABLED=1`.
- `DF_FINOPS_ACTIONS_ENABLED=0`.
- Post-cutover backend and web log samples contained no severe error signal.

Authenticated stable-host acceptance confirmed:

- The Cost Management view renders differentiated daily cost values, a
  proportional department-cost donut, model filtering, cost evidence, and the
  Operations AI entry point without failed-fetch or unavailable-service copy.
- The Risk and Optimization view renders rescan, seven policy checks, the risk
  matrix, ranked priorities, remediation actions, evidence, and contextual AI
  entry points without failed-fetch or unavailable-service copy.
- The failure-rate evidence action opened three failure-specific request
  records with distinct workspace operation names, latency, token, cost,
  cache, gateway, orchestration, agent, and model evidence. It did not fall
  back to the same generic evidence payload used by unrelated policies.

Production URL:

`https://ca-dataforge-web.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`

The showcase narrative is maintained in
`docs/dataforge-finops-showcase-core.md`.

## Rollback

The immediately previous revisions remain retained:

```powershell
az containerapp ingress traffic set `
  --name ca-dataforge-backend `
  --resource-group rg-dataforge-dev `
  --revision-weight `
    "ca-dataforge-backend--gov098e2ca=0" `
    "ca-dataforge-backend--opsaug3cd8d44=100"

az containerapp ingress traffic set `
  --name ca-dataforge-web `
  --resource-group rg-dataforge-dev `
  --revision-weight `
    "ca-dataforge-web--gov098e2ca=0" `
    "ca-dataforge-web--finexec12be269=100"
```

After rollback, verify unique positive traffic, revision health, stable-domain
authentication, backend `/api/health`, and both application log streams.

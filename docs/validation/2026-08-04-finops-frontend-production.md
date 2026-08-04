# FinOps frontend production evidence (2026-08-04)

## Release scope

This was a Web-only release of PR #23. It simplifies Cost Management,
reorders Risk and Optimization around decisions and evidence, improves ROI and
risk readability, reserves a safe lane for Operations AI, and prevents local
Playwright acceptance from silently reusing a stale Vite preview.

No backend image, SQL migration, Easy Auth setting, tenant permission,
provider route, governance action flag, or production backend configuration
was changed.

Release source:

- Team main merge commit: `a5ae457d5cfaf7126d6fdfe87753cd3cb5cfefda`.
- PR: `https://github.com/Myr-Team/dataforge-agent/pull/23`.
- Tested PR head: `a8ecc323dc4bd361e4d1b0865657b42013249755`.
- The tested PR head and merge commit had the same Git tree:
  `6c90e96ac0ce6e6c20a3d6991a0e55e558646e72`.
- Web image tag: `dataforge-web:finops-ui-a5ae457`.
- Web image digest:
  `sha256:4f16b8d7ce77ce8dd231cd089c65b5cc548c6dc4b6da1c674948076c7738edf0`.

## Verification before deployment

The following suites ran against the same source tree before image creation:

- `python -m pytest -q`: **1704 passed, 1 skipped**.
- `node --test` from `web/`: **275 passed**.
- `npm run build` from `web/`: **success**, 1792 modules transformed.
- `npx playwright test` from `web/` on isolated port 5219: **52 passed**.
- `git diff --check`: clean.

The Playwright configuration now starts a fresh preview by default. Reuse is
available only through explicit `DF_PLAYWRIGHT_REUSE_SERVER=1` opt-in.

## Candidate acceptance

The Web candidate was copied from the current production Web revision and only
its image was replaced:

- Candidate: `ca-dataforge-web--finui-a5ae457`.
- Initial traffic: **0%**.
- State: `Healthy`, `Running`, active.
- Image matched the immutable digest above.
- Candidate root: anonymous HTTP 401.
- Candidate `/api/workspaces`: anonymous HTTP 401.
- Stable backend `/api/health`: HTTP 200.
- Candidate log sample: 26 lines, zero severe error signals.

The revision-specific hostname was not added to the Entra redirect allowlist.
Signed-in acceptance was therefore performed on the existing stable hostname
after cutover, preserving the production authentication configuration.

## Production promotion and acceptance

At approximately `2026-08-04T12:36:50Z`, Web production state was:

- `ca-dataforge-web--finui-a5ae457`: **100% traffic**, `Healthy`, `Running`.
- `ca-dataforge-web--gov098e2ca`: **0% traffic**, `Healthy`, `Running`.
- `ca-dataforge-backend--gov098e2ca`: **100% traffic**, `Healthy`, `Running`.
- Stable Web root and `/api/workspaces`: anonymous HTTP 401.
- Stable backend `/api/health`: HTTP 200 in five consecutive probes.
- Production Web log sample: 101 lines, zero severe error signals and zero HTTP
  5xx signals.

Signed-in stable-host desktop acceptance confirmed:

- The complete navigation is visible immediately, including Workspaces,
  Assets, Conversations, Run History, Artifacts, Cost Management, Risk and
  Optimization, and Settings.
- Cost Management renders estimated cost, pricing coverage, cache savings,
  differentiated daily values, proportional department allocation, attention
  items, evidence controls, and Operations AI without failed-fetch copy.
- Risk and Optimization renders the current read-only scan, seven default-
  collapsed policy checks, six prioritized findings, the risk matrix,
  finding-specific request evidence, remediation drafts, and contextual AI.
- No backend or production-action configuration was changed during the Web
  release.

The 390 px responsive paths passed the production-source Playwright suite.
The signed-in browser viewport override timed out in the browser-control layer,
so no separate signed-in production mobile screenshot is claimed for this
release.

Production URL:

`https://ca-dataforge-web.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`

## Rollback

The previous Web revision remains healthy and can be restored without a new
build:

```powershell
az containerapp ingress traffic set `
  --name ca-dataforge-web `
  --resource-group rg-dataforge-dev `
  --revision-weight `
    "ca-dataforge-web--finui-a5ae457=0" `
    "ca-dataforge-web--gov098e2ca=100"
```

After rollback, verify that only one Web revision has positive traffic, the
stable Web endpoints retain Easy Auth 401 anonymously, the stable backend
health endpoint is 200, and both revision log streams contain no severe error
signal.

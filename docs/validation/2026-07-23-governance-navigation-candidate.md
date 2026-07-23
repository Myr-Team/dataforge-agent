# Governance Navigation Candidate Evidence

Date: `2026-07-23`

## Scope

- Grouped workspace and governance navigation.
- Backend-authorized enterprise identity projection and workspace-scoped lineage.
- Owner-only configuration actions, including trusted enterprise email domains.
- Focused governance pages with policy configuration contained in a modal.

## Automated verification

- `python -m pytest -q -x`: `1037 passed, 1 skipped`.
- `node --test web/src/*.test.mjs`: `80 passed`.
- `npm --prefix web run build`: passed.
- `git diff --check`: passed before the implementation commits.

## Candidate deployment

- Backend image: `dataforge-backend:govnav-20260723`.
- Backend candidate: `ca-dataforge-backend--gnav723`, `Healthy`, `0%` traffic.
- Web image: `dataforge-web:govnav-20260723`.
- Web candidate: `ca-dataforge-web--gnav723`, `Healthy`, `0%` traffic.
- Candidate URL: `https://ca-dataforge-web---lineage.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`.
- The candidate web revision proxies only to the candidate backend label.
- Candidate backend health endpoint returned `ok: true`.
- A direct candidate web request reached the established Easy Auth sign-in flow.
- Production traffic was unchanged: backend `ca-dataforge-backend--apimprod723` at `100%`; web `ca-dataforge-web--monprod722` at `100%`.

## Remaining candidate gate

The signed-in browser visual smoke remains pending. The automated in-app browser could not
reach the candidate label even though the candidate URL and Easy Auth redirect were reachable
from the deployment host. Validate the following in a normal signed-in session before approving
promotion:

1. The two navigation groups and their focused pages render without layout shift.
2. An Owner sees Member collaboration, lineage, cost and value, models and connections, and settings.
3. A non-Owner sees only their own lineage and no owner-only navigation or controls.
4. Enterprise identity configuration opens as a modal; no raw Entra identifiers or credentials are rendered.
5. The candidate remains at `0%` traffic until explicit promotion approval.

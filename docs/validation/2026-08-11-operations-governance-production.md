# DataForge operations governance production validation — 2026-08-11

## Release

- Source branch: `codex/agent-runtime-trace-closure-20260811`
- Source commit: `239324c`
- Backend image digest: `sha256:8bc853c0e9c10d35186f343be1f3eee4cd87b87ef1de2ec99363a330a183d2f1`
- Web image digest: `sha256:508cea14bdc3bf45ebc64bd01ef90509e1ce00d3bd2996db0b444664a8877550`
- Production URL: `https://ca-dataforge-web.grayground-b382bfb9.eastus2.azurecontainerapps.io/`

The backend revision `ca-dataforge-backend--id239` and web revision
`ca-dataforge-web--idw239` are Healthy, Running, and receive 100% of traffic.
The immediately previous `agt622f` / `agtw622f` revisions remain Healthy at 0%
as the rollback targets.

## Regression gates

- Python: `1869 passed, 1 skipped, 1 warning`
- Node: `315 passed`
- Vite: production build succeeded; the existing large-entry-chunk warning remains
- Playwright: `65 passed` on an isolated preview port
- `git diff --check`: clean
- changed-file credential pattern scan: pass

## Production evidence

- The public unauthenticated request returns `401`, confirming that Easy Auth
  remains enforced.
- The web candidate successfully proxied `/api/health` to the pinned backend
  candidate; required Foundry, Search, MCP, Speech, Blob, and Content Safety
  dependencies reported healthy.
- The demo workspace exposes six governed routes. Both discovered DeepSeek
  models are selectable and have server-owned price keys.
- The FinOps model filter returned six models. The unfiltered request total was
  2389, while model-filtered totals were distinct, proving that top-level
  metrics are recalculated for the selected model rather than left unchanged.
- A tenant-scoped trusted Owner profile matching the persisted workspace Owner
  produced verified enterprise email display. Unverified members remain
  private and now receive friendly customer-facing labels instead of visible
  `member_...` hashes.
- Notification settings are readable and contain a configured recipient. A
  provider `accepted` response remains a pending-delivery state; only a
  delivery event is presented as success. Automated threshold sends remain
  disabled until delivery monitoring is explicitly accepted.
- Easy Auth token storage is enabled on Blob storage. Delegated
  `User.ReadBasic.All` and `GroupMember.Read.All` login scopes are configured,
  so group search can use the signed-in user's delegated token after a fresh
  sign-in. Group mappings remain tenant-scoped and revisioned.

## User acceptance note

Because the delegated Graph scopes changed, the signed-in user must sign out
and sign in once before testing Entra group search. No auth bypass, secret,
raw provider identifier, raw member object ID, or production response body was
added to the repository.

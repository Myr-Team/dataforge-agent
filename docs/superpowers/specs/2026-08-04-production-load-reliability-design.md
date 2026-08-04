# Production Load Reliability Design

## Goal

Prevent the production UI from remaining indefinitely on the operations-permission loading state while preserving workspace isolation, Easy Auth trust, Entra group governance, and server-side authorization.

## Observed Failure

- The web and backend revisions are warm (`minReplicas=1`), ready, and have no container restarts.
- Static HTML, JavaScript, and CSS complete before the operations surface is usable.
- `GET /api/workspaces/{workspace_id}/governance/capabilities` intermittently completes after tens of seconds or has no completion record before the browser abandons the request.
- `App.refreshDashboard` clears the last capability state and the operations route renders only `正在核验运营管理权限` until that request resolves.
- General API requests have no bounded client timeout and nginx permits a 1,200-second upstream read.
- Entra group governance is enabled. Identity enrichment is currently attempted before direct workspace ownership or persisted membership can short-circuit it, so an external group lookup can sit on the critical path for every concurrent workspace request.

## Selected Design

### 1. Lazy workspace group resolution

Add a workspace-aware authorization helper that first parses only trusted Easy Auth claims and evaluates direct workspace ownership or persisted membership. Only a caller that remains unresolved because group membership is missing may invoke Entra group enrichment and retry authorization.

The helper is used by the workspace access, dashboard, ordinary workspace authorization, trusted-reader authorization, and sensitive governance authorization paths. A direct owner or persisted member therefore does not depend on Microsoft Graph. Group-only members retain their existing mapped-role behavior.

### 2. Request-local and cross-request membership protection

Cache the resolved actor on `request.state` so repeated authorization calls in one request cannot repeat group resolution. For overage resolution, use a process-local single-flight guard around the remote lookup and store both observed results and bounded unavailable results in Redis. Observed memberships retain the existing 120-second TTL; unavailable results use a short 30-second TTL so transient failures recover without producing a request storm.

No access token, group id, raw actor id, or email is added to cache keys or responses. Existing HMAC-derived membership keys remain authoritative.

### 3. Bounded frontend permission reads

Extend the shared API client with an opt-in timeout that composes with any caller-provided `AbortSignal`. Apply an 8-second timeout to workspace access and governance capabilities, and a 15-second timeout to the dashboard read. Streaming, upload, model calls, and production actions keep their existing behavior.

Timeout failures return a bounded Chinese service message and never become an authorization denial.

### 4. Stale-while-revalidate authorization UI

Do not clear a matching, previously verified workspace access or capability object when refreshing the same workspace. Start access, dashboard, and capability reads concurrently. When there is no verified capability state and the bounded read fails, replace the infinite loading state with a retryable service-unavailable panel. A capability object for another workspace is never reused because existing workspace-id checks remain in force.

The backend remains the enforcement point for every FinOps read. Preserving the last UI capability state cannot grant API access after a server-side revocation.

### 5. Static shell improvement

Add an immutable or long-lived cache rule for the root logo asset so repeat navigation does not transfer the 1.4 MB PNG again. Do not alter the visual asset in this change.

## Error Handling

- Direct members: no remote group lookup on the critical path.
- Group-only members: one in-flight lookup per backend process and HMAC-scoped Redis reuse.
- Graph timeout or permission failure: short unavailable cache, fail closed for group-only access, direct users remain authorized.
- Frontend timeout with no prior state: show `权限服务暂时不可用` and a `重新检查` action.
- Frontend timeout with prior matching state: retain the current surface while the next manual refresh can retry.
- Workspace switch: prior access and capabilities fail existing workspace-id validation and cannot cross scopes.

## Acceptance

1. A direct workspace owner reaches governance capabilities without invoking the group loader.
2. A group-only member still resolves through Entra group mapping.
3. Concurrent overage requests invoke the remote group loader once; unavailable results are retried only after the short TTL.
4. Repeated `actor_from_request` calls in one request reuse the request-local actor.
5. Access and capability reads abort within their configured bound and show a retryable state rather than an infinite spinner.
6. A same-workspace refresh retains previously verified capabilities; a workspace switch cannot reuse them.
7. The production navigation remains structurally complete before capabilities resolve.
8. Python, Node, Vite, Playwright, and `git diff --check` pass.
9. A zero-traffic candidate is healthy, signed-in desktop and mobile operations surfaces load, and production traffic is unchanged until explicit approval.

## Out of Scope

- Changing Easy Auth registration, claims, token store, or Entra application permissions.
- Disabling Entra group governance.
- Changing FinOps data contracts or production action flags.
- Replacing the logo artwork or redesigning the operations pages.
- Moving long-running chat or artifact endpoints behind a different ingress.

# Production Load Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workspace and operations loading bounded and responsive without weakening Easy Auth, workspace isolation, or Entra group authorization.

**Architecture:** Workspace authorization first evaluates direct trusted membership and resolves Entra groups only as a fallback. The frontend composes bounded abort signals for critical bootstrap reads, preserves only matching verified workspace state, and renders an explicit retry state instead of an infinite permission spinner.

**Tech Stack:** Python 3.12, FastAPI, Redis, Microsoft Graph client, React 18, Vite, Node test runner, Playwright, nginx, Azure Container Apps.

## Global Constraints

- Do not change Easy Auth registration, claims, token store, or Entra application permissions.
- Do not disable `DF_ENTRA_GROUP_GOVERNANCE_ENABLED` or weaken server-side authorization.
- Do not enable `DF_FINOPS_ACTIONS_ENABLED` or external provider routing.
- Do not return or log raw tokens, group ids, actor ids, email addresses, secrets, or provider response ids.
- Preserve long-running chat, upload, and artifact request behavior; timeouts are opt-in for bounded bootstrap reads only.
- Keep production traffic unchanged until the user explicitly approves a production switch.

---

### Task 1: Workspace-aware authorization fast path

**Files:**
- Modify: `backend/identity.py`
- Modify: `backend/entra_membership.py`
- Modify: `backend/workspace_authz.py`
- Modify: `backend/app.py`
- Modify: `backend/control_plane.py`
- Test: `tests/test_entra_membership.py`
- Test: `tests/test_workspace_roles.py`

**Interfaces:**
- Produces: `actor_from_request(request, *, fallback=True, resolve_groups=True)` with request-local caching by resolution mode.
- Produces: `actor_for_workspace_request(workspace_id, request, *, fallback=False)` returning a trusted base actor immediately for a direct owner/member and an enriched actor only for a group fallback.
- Preserves: `require_workspace_permission` and `require_sensitive_workspace_permission` public behavior.

- [ ] **Step 1: Write failing tests for the direct-member fast path**

```python
def test_workspace_owner_authorization_does_not_resolve_groups(monkeypatch, owner_request):
    calls = 0
    def fail_if_called(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("group lookup must not run for the persisted owner")
    monkeypatch.setattr(identity, "resolve_actor_group_membership", fail_if_called, raising=False)
    actor = workspace_authz.actor_for_workspace_request("ws-owner", owner_request)
    assert workspace_authz.require_workspace_permission("ws-owner", actor, "workspace.read") == "owner"
    assert calls == 0
```

- [ ] **Step 2: Run the owner test and verify RED**

Run: `python -m pytest -q tests/test_workspace_roles.py -k "does_not_resolve_groups"`

Expected: FAIL because `actor_for_workspace_request` and the `resolve_groups` argument do not exist.

- [ ] **Step 3: Write failing tests for unavailable caching and single-flight behavior**

```python
def test_overage_unavailable_result_is_short_cached(monkeypatch, fake_cache):
    calls = 0
    def unavailable(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("graph unavailable")
    first = resolve_actor_group_membership(OVERAGE_ACTOR, request=object(), graph_loader=unavailable, cache=fake_cache)
    second = resolve_actor_group_membership(OVERAGE_ACTOR, request=object(), graph_loader=unavailable, cache=fake_cache)
    assert first["state"] == second["state"] == "unavailable"
    assert calls == 1
    assert fake_cache.last_ttl == 30
```

- [ ] **Step 4: Run the membership tests and verify RED**

Run: `python -m pytest -q tests/test_entra_membership.py`

Expected: FAIL because unavailable membership results are not cached.

- [ ] **Step 5: Implement lazy actor resolution and bounded membership reuse**

```python
def actor_for_workspace_request(workspace_id, request, *, fallback=False):
    actor = actor_from_request(request, fallback=fallback, resolve_groups=False)
    decision = workspace_access_decision(workspace_id, actor)
    if decision.allowed or decision.reason_code != "membership_missing":
        return actor
    return actor_from_request(request, fallback=fallback, resolve_groups=True)
```

Store a sanitized actor on `request.state` separately for base and enriched modes. In `entra_membership.py`, re-check the HMAC-scoped Redis key under a module single-flight lock, store observed results for 120 seconds, and store `{state: "unavailable", group_refs: []}` for 30 seconds. Update workspace authorization entry points in `app.py` and `control_plane.py` to use the workspace-aware helper.

- [ ] **Step 6: Run backend authorization tests and verify GREEN**

Run: `python -m pytest -q tests/test_entra_membership.py tests/test_workspace_roles.py`

Expected: all tests pass and group-only role tests remain unchanged.

- [ ] **Step 7: Commit Task 1**

```powershell
git add backend/identity.py backend/entra_membership.py backend/workspace_authz.py backend/app.py backend/control_plane.py tests/test_entra_membership.py tests/test_workspace_roles.py
git commit -m "fix(auth): keep Entra lookup off direct workspace path"
```

### Task 2: Bounded bootstrap API reads

**Files:**
- Modify: `web/src/api.js`
- Test: `web/src/finopsApi.test.mjs`

**Interfaces:**
- Produces: internal `request(path, { timeoutMs, signal, ...fetchOptions })` support.
- Preserves: caller aborts as `AbortError` and converts only internally generated timeout aborts to `DataForgeRequestTimeoutError` with the public message `服务响应超时，请重试`.
- Applies: workspace access 8,000 ms, governance capabilities 8,000 ms, dashboard 15,000 ms.

- [ ] **Step 1: Write a failing timeout test**

```javascript
test("governance capability reads abort at the bounded timeout", async () => {
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(options.signal.reason));
  });
  await assert.rejects(
    loadGovernanceCapabilities("ws-a", { timeoutMs: 5 }),
    (error) => error.name === "DataForgeRequestTimeoutError",
  );
});
```

- [ ] **Step 2: Run the timeout test and verify RED**

Run: `node --test finopsApi.test.mjs`

Expected: FAIL because capability reads do not create a timeout signal.

- [ ] **Step 3: Implement composed bounded signals**

Use an internal `AbortController`, forward a caller abort once, clear the timer in `finally`, and never pass `timeoutMs` into `fetch`. Only an internal timer produces `DataForgeRequestTimeoutError`; a navigation or request-guard abort remains an `AbortError`.

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `node --test finopsApi.test.mjs governanceApi.test.mjs`

Expected: both files pass with no unhandled rejection.

- [ ] **Step 5: Commit Task 2**

```powershell
git add web/src/api.js web/src/finopsApi.test.mjs
git commit -m "fix(web): bound critical workspace bootstrap reads"
```

### Task 3: Retryable operations permission state

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Create: `web/src/workspaceBootstrap.js`
- Create: `web/src/workspaceBootstrap.test.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Produces: `matchingWorkspaceValue(value, workspaceId)` and `workspaceBootstrapFailure(value, workspaceId, error)` pure helpers.
- Produces: `WorkbenchMain` props `governanceCapabilitiesError` and `onRetryGovernanceCapabilities`.
- Preserves: `navigationAccessState` and server permission fields as the only source of an allowed operations surface.

- [ ] **Step 1: Write failing state tests**

```javascript
test("same-workspace refresh keeps verified capabilities", () => {
  const verified = { workspace_id: "ws-a", sections: { finops: { visible: true } } };
  assert.equal(matchingWorkspaceValue(verified, "ws-a"), verified);
  assert.equal(matchingWorkspaceValue(verified, "ws-b"), null);
});
```

- [ ] **Step 2: Run the state test and verify RED**

Run: `node --test workspaceBootstrap.test.mjs`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement concurrent refresh and explicit error state**

Start access, dashboard, and capabilities promises together. Do not clear matching access or capability state at refresh start. Track capability failures separately and show a compact `权限服务暂时不可用` panel with one `重新检查` button only when no matching verified capability exists.

- [ ] **Step 4: Add a Playwright acceptance for a stalled capability request**

Intercept the capability route so it exceeds the test timeout. Assert the operations route leaves the loading copy, renders the service-unavailable copy, and exposes exactly one retry button without showing the unauthorized-account message.

- [ ] **Step 5: Run state, component, and Playwright tests and verify GREEN**

Run: `node --test`

Run: `$env:DF_PLAYWRIGHT_PORT='5227'; npx playwright test tests/finops-operations-management.spec.mjs`

Expected: Node passes; operations Playwright passes on a fresh isolated preview.

- [ ] **Step 6: Commit Task 3**

```powershell
git add web/src/App.jsx web/src/components.jsx web/src/styles.css web/src/workspaceBootstrap.js web/src/workspaceBootstrap.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(web): replace permission spinner with bounded retry"
```

### Task 4: Static caching and complete acceptance

**Files:**
- Modify: `web/nginx.conf.template`
- Create: `web/src/nginxConfig.test.mjs`
- Create: `docs/validation/2026-08-04-production-load-reliability-candidate.md`

**Interfaces:**
- Produces: exact `/dataforge-logo.png` cache rule with a 30-day public cache and 7-day stale-while-revalidate.
- Produces: immutable backend and web candidate image tags and a zero-traffic candidate validation record.

- [ ] **Step 1: Write and run the failing nginx test**

```javascript
test("the root logo is cached independently from the no-store SPA shell", async () => {
  const config = await readFile(new URL("../nginx.conf.template", import.meta.url), "utf8");
  assert.match(config, /location = \/dataforge-logo\.png/);
  assert.match(config, /stale-while-revalidate=604800/);
});
```

Run: `node --test nginxConfig.test.mjs`

Expected: FAIL because the root logo currently inherits the SPA no-store rule.

- [ ] **Step 2: Add the exact cache rule and verify GREEN**

Run: `node --test nginxConfig.test.mjs`

Expected: PASS.

- [ ] **Step 3: Run the complete local regression**

Run: `python -m pytest -q`

Run: `node --test` from `web/`

Run: `npm run build` from `web/`

Run: `$env:DF_PLAYWRIGHT_PORT='5227'; npx playwright test` from `web/`

Run: `git diff --check team/main...HEAD`

Expected: all suites pass, Vite builds, Playwright uses the isolated port, and whitespace checks are clean.

- [ ] **Step 4: Build and deploy zero-traffic candidates**

Build immutable backend and web images from the branch HEAD. Create one backend candidate and one web candidate with production-equivalent configuration, `minReplicas=1`, and zero traffic. Keep `DF_FINOPS_ACTIONS_ENABLED=0`; do not change auth settings.

- [ ] **Step 5: Run candidate acceptance**

Verify candidate health, replica readiness, zero restarts, direct-owner capabilities latency, signed-in desktop and 390 px operations loading, retry state under a deliberately delayed mocked capability response, and root logo cache headers. Confirm production traffic is unchanged.

- [ ] **Step 6: Write evidence, commit, and push**

```powershell
git add web/nginx.conf.template web/src/nginxConfig.test.mjs docs/validation/2026-08-04-production-load-reliability-candidate.md
git commit -m "test(reliability): record production load candidate"
git push -u team codex/production-load-reliability
```

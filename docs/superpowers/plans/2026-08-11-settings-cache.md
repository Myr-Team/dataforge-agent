# Settings Cache and Re-entry Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authorization-scoped in-memory SWR caching so repeated Settings navigation is immediate and duplicate GETs are eliminated.

**Architecture:** Introduce a settings-only resource store with scoped keys, TTL, stale-while-revalidate, in-flight deduplication, abort guards, and precise invalidation. Wire existing Settings loaders to it without changing server data fields or adding CDN caching.

**Tech Stack:** React, JavaScript modules, Fetch/AbortController, Node test runner, Playwright.

## Global Constraints

- Never cache secrets, request/response bodies, raw identity, credentials, or arbitrary provider configuration.
- Never share entries across tenant, actor/session generation, workspace, or effective-permission fingerprint.
- Use memory only; do not add localStorage, Cache Storage, service workers, Cloudflare, or public API caching.
- Keep existing API response fields and optimistic-concurrency revisions intact.
- Use TDD and verify each regression test fails before production changes.

---

### Task 1: Settings resource store

**Files:**
- Create: `web/src/settingsDataStore.js`
- Create: `web/src/settingsDataStore.test.mjs`

**Interfaces:**
- Produces: `settingsDataKey(scope, resource, query)`, `loadSettingsResource(key, loader, options)`, `peekSettingsResource(key)`, `invalidateSettingsResource(key)`, and `clearSettingsScope(scopeKey)`.

- [ ] Write failing tests for fresh hits, stale immediate value plus one revalidation, shared in-flight promise, retained stale value on failure, abort/late-response guard, and scope isolation.
- [ ] Run `node --test src/settingsDataStore.test.mjs` and verify RED.
- [ ] Implement the minimal Map-backed store. Default to 30,000ms fresh and 300,000ms stale-usable; allow readiness-specific overrides.
- [ ] Re-run the focused test and verify GREEN.
- [ ] Commit the store and tests as `feat(settings): add scoped resource cache`.

### Task 2: Settings home and navigation preload

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Modify: `web/src/api.js` only if an existing loader needs an `AbortSignal` option
- Test: `web/src/settingsNavigation.test.mjs`
- Test: `web/src/finopsLayout.test.mjs` only for stable navigation structure assertions

**Interfaces:**
- Consumes: Task 1 store APIs and existing settings GET loaders.
- Produces: `settingsPreloadScope(...)` and `prefetchSettingsHome(...)` with the same authorization boundary used by rendered Settings consumers.

- [ ] Add failing tests that repeat Settings navigation and require stable cached snapshots, no member load until the members tab is selected, and one system-status source from the workspace settings response.
- [ ] Verify RED with the focused Node tests.
- [ ] Build the settings authorization scope from safe tenant/session/workspace/permission inputs; clear the old scope before switching.
- [ ] Route budget, notification, alert, workspace settings, routing, and member reads through the store.
- [ ] Initialize system status from `workspaceSettings.system_status`; keep explicit manual reprobe as a forced resource refresh.
- [ ] Delay full member loading until `tab === "members"`, `governanceOnly`, or a member action explicitly needs the contract.
- [ ] Add hover/focus/idle preloading for Settings home without preloading member search, Entra search, governance history, Trace, or service readiness.
- [ ] Re-run focused tests and commit as `fix(settings): reuse authorized snapshots on re-entry`.

### Task 3: Child-page reuse and precise invalidation

**Files:**
- Modify: `web/src/MemberBudgetSettingsPage.jsx`
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/ProviderConnectionsPage.jsx`
- Modify: `web/src/IdentityAccessPage.jsx`
- Modify: `web/src/ServiceReadinessPage.jsx`
- Test: corresponding `*.test.mjs` view-model/client tests

**Interfaces:**
- Consumes: the same scoped keys as Settings home.
- Produces: resource-family invalidation after successful typed writes.

- [ ] Add failing tests proving home and child consumers share one budget/routing request and that each write invalidates only its resource family.
- [ ] Verify RED.
- [ ] Replace child mount-only loads with shared resource loads; render stale data with an explicit updating/error state rather than resetting to an empty page.
- [ ] Apply 15s/60s policy to readiness; keep explicit refresh.
- [ ] Invalidate budget/alerts, notification, members, routing/pricing/provider, or identity families only after their corresponding successful write.
- [ ] Verify focused tests and commit as `fix(settings): dedupe child resource loading`.

### Task 4: Browser performance and isolation acceptance

**Files:**
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: completed Settings resource behavior.
- Produces: request-count, timing, stale-failure, and cross-scope browser evidence.

- [ ] Add a failing Playwright scenario that records first entry, leaves Settings, and re-enters within 30 seconds.
- [ ] Require repeat content within 200ms, no full-page skeleton, and no duplicate same-key GET.
- [ ] Add delayed/500 refresh coverage proving stale content stays visible.
- [ ] Add workspace and permission-boundary switches proving old snapshots never render.
- [ ] Run focused Playwright with a unique `DF_PLAYWRIGHT_PORT`, then `node --test`, `npm run build`, and `git diff --check`.
- [ ] Commit acceptance coverage as `test(settings): verify cached re-entry and scope isolation`.

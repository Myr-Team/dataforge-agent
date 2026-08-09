# DeepSeek and Entra Governance Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a governed DeepSeek connection selectable and billable per workspace, show the real trusted Entra identity in production, and provide a credible two-user role-switching demonstration without weakening authorization.

**Architecture:** Keep Azure routes in the server-owned static allowlist and add a tenant-scoped external route registry derived from persisted provider records. A provider must be secret-backed, successfully tested, model-discovered, officially priced, and explicitly governed before its models become selectable. Workspace routing persists route IDs with optimistic concurrency; runtime selection resolves the same merged registry and remains protected by the external-routing feature gate. A new backend auth-session endpoint converts trusted Container Apps headers into a minimal public identity view so the browser never guesses identity from local storage.

**Tech Stack:** Python 3 / FastAPI / Pydantic, Azure SQL provider repository, React / Vite, Node test runner, Playwright, Azure Container Apps, Easy Auth / Microsoft Entra ID.

## Global Constraints

- Do not expose provider secrets, raw Entra claims, object IDs, tenant IDs, group claims, or provider response IDs.
- Preserve existing Easy Auth and trusted reverse-proxy verification; browser-supplied identity headers are never authoritative.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`.
- Keep `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0` until candidate execution, telemetry, price attribution, and fallback tests pass.
- Do not silently fall back on authentication, balance, policy, or invalid-parameter failures. Fallback is allowed only before output/side effects for bounded network, 429, and provider 5xx failures.
- Dynamic provider routes are tenant-scoped and repository-derived. Do not write them into environment variables or return another tenant's routes.
- Preserve unrelated dirty workspace files and commit only files named by each task.
- Follow red-green-refactor for every behavior change.

---

## Task 1: Trusted production identity session

**Files:**
- Create: `backend/auth_session_router.py`
- Modify: `backend/app.py`
- Modify: `web/src/api.js`
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Test: `tests/test_auth_session_api.py`
- Test: `web/src/accountMenu.test.mjs`

- [ ] Add failing backend tests for `GET /api/auth/session`: trusted proxy headers return only `authenticated`, `name`, `email`, `identity_provider`, and `identity_source`; absent or untrusted identity returns an explicit unauthenticated/unavailable state and never echoes claims, OID, tenant ID, or group data.
- [ ] Run `python -m pytest tests/test_auth_session_api.py -q` and confirm the new tests fail because the route does not exist.
- [ ] Implement a focused router using `actor_from_request(..., fallback=False)`, `is_trusted_tenant_identity`, and `public_actor`; register it in `backend/app.py`.
- [ ] Add failing frontend tests that production auth-session failure does not show `Demo User`, `local.demo@dataforge`, or `local`; localhost preview may still use the explicit local preview identity.
- [ ] Run `node --test src/accountMenu.test.mjs` from `web/` and confirm failure.
- [ ] Add `loadAuthSession()` to `web/src/api.js`; initialize `App` with a pending identity, load `/api/auth/session`, and expose a clear unavailable state instead of a fake local identity in production.
- [ ] Update the account menu to show Entra / trusted-proxy source and the current workspace role without exposing directory identifiers.
- [ ] Re-run the focused Python and Node tests and commit as `feat(auth): expose trusted Entra session to the portal`.

## Task 2: Explicit provider governance lifecycle

**Files:**
- Modify: `backend/model_providers.py`
- Modify: `backend/model_provider_service.py`
- Modify: `backend/model_provider_router.py`
- Modify: `backend/model_provider_repository.py` only if persistence fields are missing
- Test: `tests/test_model_provider_api.py`
- Test: `tests/test_model_provider_repository.py`

- [ ] Add failing API tests for `POST /api/model-providers/{provider_id}/govern` and `/suspend`: only a workspace owner/admin in the trusted tenant may act; `base_revision` is required after revision zero; successful govern increments revision and writes audit; stale writes return 409; no secret material appears in responses or logs.
- [ ] Add failing eligibility tests: governance requires a stored secret reference, a successful connection timestamp, at least one supported discovered model, and an official price key for every model being admitted. A currently degraded provider may retain its governed record but its routes are not selectable until connectivity is healthy again.
- [ ] Run `python -m pytest tests/test_model_provider_api.py tests/test_model_provider_repository.py -q` and verify red failures.
- [ ] Add a public `route_eligibility` view with `state`, `selectable`, and a stable safe reason code; keep raw provider errors private.
- [ ] Implement govern/suspend service transitions with optimistic concurrency and required audit persistence. Suspending must not delete the provider or pricing history.
- [ ] Re-run focused tests and commit as `feat(providers): add audited routing governance`.

## Task 3: Tenant-scoped dynamic route registry

**Files:**
- Create: `backend/model_provider_routes.py`
- Modify: `backend/model_policy.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/workspace_model_config.py` if route protocol typing is needed
- Test: `tests/test_model_policy.py`
- Test: `tests/test_model_routing_api.py`
- Test: `tests/test_model_provider_api.py`

- [ ] Add failing unit tests that merge static Azure routes with only the current tenant's governed DeepSeek models; route IDs are deterministic and collision-safe; suspended, unpriced, unsupported, disconnected, or foreign-tenant records remain visible only as non-selectable diagnostics where authorized and can never validate a routing write.
- [ ] Add failing routing API tests showing governed DeepSeek models in `GET /api/workspaces/{workspace_id}/model-routing`, including `provider_type`, `provider_label`, `pricing_state`, `health_state`, `selectable`, and `unavailable_reason`.
- [ ] Add failing write tests proving an eligible dynamic route can be assigned with `base_revision`, while a disabled route is rejected and a stale write returns 409 without persistence.
- [ ] Implement `model_provider_routes.py` as the single adapter from provider repository records to `ModelRoute` plus safe public metadata.
- [ ] Extend model-policy request scope to accept a resolved route registry. Ensure workspace policy selection and runtime selection consume the same scoped registry instead of re-reading only `DF_MODEL_ROUTE_ALLOWLIST`.
- [ ] Update control-plane read/write endpoints to derive tenant from the trusted actor, fetch tenant-scoped dynamic routes, and validate against selectable routes.
- [ ] Re-run focused tests and commit as `feat(routing): merge governed provider models per tenant`.

## Task 4: Runtime DeepSeek execution and bounded fallback

**Files:**
- Modify: `backend/provider_gateway.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/model_policy.py`
- Test: `tests/test_maf_provider_routing.py`
- Test: `tests/test_model_policy.py`

- [ ] Add failing tests for an assigned DeepSeek route executing through the saved provider connection when the feature gate is enabled, with route/provider/model identifiers recorded but no secret or raw response ID persisted.
- [ ] Add failing tests that disabled feature gate blocks external execution, and that auth/balance/invalid-request failures do not fall back.
- [ ] Add failing tests that pre-output network, 429, and provider 5xx failures may fall back once to the configured Azure route and record a safe fallback reason; partial output or side effects prevent fallback.
- [ ] Pass the request-scoped route registry and provider connection metadata into the MAF/provider gateway path; retain Azure behavior unchanged.
- [ ] Re-run focused tests and commit as `feat(runtime): execute governed DeepSeek routes safely`.

## Task 5: Official DeepSeek pricing and cost attribution UI

**Files:**
- Modify: `web/src/modelRoutingViewModel.js`
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/providerConnectionsViewModel.js`
- Modify: `web/src/ProviderConnectionsPage.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/modelRoutingViewModel.test.mjs`
- Test: `web/src/providerConnectionsViewModel.test.mjs`
- Test: `tests/test_finops_official_pricing.py`
- Test: `tests/test_finops_pricing_api.py`

- [ ] Add failing view-model tests for Azure/DeepSeek grouping, disabled route reasons, and three DeepSeek price dimensions: cached-input hit, uncached-input miss, and output, each per million tokens with currency, official source, and revision.
- [ ] Add failing backend tests that DeepSeek usage maps cached and uncached input separately, returns `estimated` with the active official revision, and remains `unpriced` when no reliable mapping exists.
- [ ] Run the focused Node and Python tests and verify red failures where presentation or mapping is incomplete.
- [ ] Update the provider page with explicit `纳入模型路由` / `暂停路由` controls and a compact lifecycle stepper. Disable actions with an actionable reason instead of hiding them.
- [ ] Update model routing selects to group Azure Foundry and DeepSeek; show unavailable routes disabled with a short reason, and show the three-part official price card beside each route.
- [ ] Keep the existing official DeepSeek documentation URL and revision server-owned; the UI only renders safe catalog fields.
- [ ] Re-run focused tests, run `npm run build`, and commit as `feat(web): surface governed providers and official pricing`.

## Task 6: Entra role demonstration readiness

**Files:**
- Modify: `web/src/IdentityAccessPage.jsx`
- Modify: `web/src/identityAccessViewModel.js`
- Modify: `web/src/styles.css`
- Create: `docs/validation/2026-08-09-entra-role-demo-runbook.md`
- Test: `web/src/identityAccessViewModel.test.mjs`
- Test: `tests/test_workspace_authz.py`
- Test: `tests/test_entra_member_invites.py`

- [ ] Add failing tests for an identity summary that distinguishes authenticated Entra identity, workspace role, role source, and tenant trust without exposing raw IDs.
- [ ] Add/retain authorization tests that Viewer cannot change provider governance or routing, Member sees only allowed workspace data, and Owner can manage routing; cross-workspace and cross-tenant access stays denied.
- [ ] Present the current signed-in Entra user and workspace role clearly on the identity page, with concise guidance for switching accounts through the real Microsoft sign-in flow.
- [ ] Write a runbook using an existing second tenant user: assign Viewer, verify read-only behavior, change to Member, sign out/in, verify the expanded scope. State that tenant user creation requires a directory administrator and is not simulated in the application.
- [ ] Re-run focused tests and commit as `feat(identity): clarify Entra role governance`.

## Task 7: Integrated acceptance, candidate release, and production rollout

**Files:**
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify or create: `web/tests/model-provider-routing.spec.mjs`
- Modify: `docs/validation/2026-08-09-entra-role-demo-runbook.md`
- Create: `docs/validation/2026-08-09-deepseek-production-acceptance.md`

- [ ] Add Playwright coverage for: real identity label (mocked trusted session), connected-but-pending provider, govern action, DeepSeek appearing in route selects, disabled reason, three-part price display, save with revision, and responsive desktop/mobile layout.
- [ ] Run `python -m pytest -q`; record exact passed/skipped counts.
- [ ] Run `node --test` and `npm run build` in `web/`; record exact results and bundle warnings.
- [ ] Start a unique-port preview with no reused server, run `npx playwright test`, and visually inspect desktop/mobile screenshots for clipping, overflow, alignment, and discoverability.
- [ ] Run `git diff --check` and a repository secret scan over tracked diff/history candidates; verify no credentials, PATs, keys, production payloads, or generated test artifacts are staged.
- [ ] Build immutable backend/web candidate images; deploy zero-traffic candidate revisions; apply only additive SQL if required.
- [ ] Validate candidate health, authenticated identity, tenant isolation, provider list, govern/suspend, DeepSeek route save, one governed DeepSeek request, one Azure fallback case, three-rate cost attribution, and model-level FinOps aggregation. Keep `DF_FINOPS_ACTIONS_ENABLED=0`.
- [ ] After candidate evidence passes, enable `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=1` on the candidate only, repeat the runtime checks, then switch production traffic to the verified revisions.
- [ ] Re-run production smoke tests with the real Entra session and verify the previous Azure route remains a rollback target.
- [ ] Commit acceptance documentation as `docs: record DeepSeek and Entra production acceptance`, push the branch, and report the commit/PR/production revision links.

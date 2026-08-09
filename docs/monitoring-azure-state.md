# Monitoring Azure Deployment State

Last verified: `2026-08-03`.

## FinOps governance production

- Team main release commit:
  `098e2ca8f9a78b5f73739f0261f5651abf15c2bf`.
- Backend production revision:
  `ca-dataforge-backend--gov098e2ca` at `100%` traffic.
- Web production revision:
  `ca-dataforge-web--gov098e2ca` at `100%` traffic.
- Both revisions use immutable image digests and were verified `Healthy` and
  `Running` after zero-traffic candidate checks.
- Stable backend health returned HTTP 200 five consecutive times; stable web
  authentication returned HTTP 401 anonymously and loaded successfully in the
  existing authenticated browser session.
- FinOps read access remains enabled and production governance execution
  remains disabled.
- Rollback revisions are `ca-dataforge-backend--opsaug3cd8d44` and
  `ca-dataforge-web--finexec12be269`.
- Detailed evidence is recorded in
  `docs/validation/2026-08-03-finops-governance-production.md`.

## Operations Management production

- Backend production revision:
  `ca-dataforge-backend--opsmgmt03602adv2` at `100%` traffic, using
  `dataforge-backend:opsmgmt-03602ad`.
- Web production revision:
  `ca-dataforge-web--opsmgmt03602adp` at `100%` traffic, using
  `dataforge-web:opsmgmt-03602ad`.
- Both revisions were verified `Healthy` after zero-traffic candidate checks.
- Rollback revisions remain retained:
  `ca-dataforge-backend--finopsmi15d03c9` and
  `ca-dataforge-web--finopsprod8ed23f4`.
- FinOps read access is enabled and governance execution remains disabled.
- Detailed evidence is recorded in
  `docs/validation/2026-07-26-operations-management-production.md`.

## Deployment Identity

- Azure CLI deployment identity: `sp-dataforge-codex` service principal.
- Scope: subscription `赞助订阅2-2`.
- Verified role: `Owner` at subscription scope.
- Target application resource group: `rg-dataforge-dev` in `eastus2`.
- Backend runtime identity: the system-assigned identity on `ca-dataforge-backend`.
- Backend runtime identity has `Log Analytics Reader` on
  `log-dataforge-dev`; the Azure Monitor query client is therefore authorized
  to read the workspace-backed Application Insights telemetry.

The deployment identity is sufficient to create APIM resources, enable managed
identities, and create role assignments. Do not record client secrets, access
tokens, APIM keys, or model API keys in this file.

## Foundry and APIM

- Foundry resource: `Agent-Demo-Foundry-fuzh` in resource group `Agent-Demo-Fuzh`.
- Foundry project endpoint: `Agent-Demo-proj`.
- Current text deployment: `gpt-5.1`.
- Existing APIM instance before this change: none.
- Preview gateway deployment: `df-monitoring-apim-20260721`.
- Preview APIM service name: `dfmonapim721`.
- Selected SKU: `StandardV2`.
- APIM provisioning state: `Succeeded`.
- APIM managed identity has the Foundry-scoped `Cognitive Services User` role.
- APIM text API deployments: `df-monitoring-text-api-20260721`,
  `df-monitoring-text-api-role-20260721`, and
  `df-monitoring-text-api-v1issuer-20260721` (`Succeeded`).
- APIM telemetry deployment: `df-monitoring-apim-telemetry-20260721`
  (`Succeeded`), using the existing `appi-dataforge-dev` Application Insights
  resource for API diagnostics and APIM token metrics.
- APIM telemetry metric deployment:
  `df-monitoring-apim-telemetry-metrics-20260721` (`Succeeded`). The API
  diagnostic has `metrics: true`, 100% sampling, and information verbosity.
- APIM workspace metric deployment:
  `df-monitoring-text-api-workspace-metric-20260721` (`Succeeded`).
- APIM diagnostic privacy deployment:
  `df-monitoring-apim-telemetry-privacy-20260721` (`Succeeded`).
- The text API validates the real single-tenant Entra audience and requires
  the `invoke_as_application` application role. Anonymous callers and tokens
  without that role are rejected before requests are forwarded to Foundry.
- The backend managed identity was verified to receive a v1.0 access token
  whose `roles` claim contains `invoke_as_application`. The APIM policy uses
  the corresponding v1 OpenID metadata endpoint; using v2 metadata caused a
  reproducible HTTP 401 despite the correct audience and role claim.
- APIM policy correlation deployment:
  `df-monitoring-text-policy-correlation-20260721` (`Succeeded`). The policy
  returns a caller correlation marker, removes DataForge lineage headers before
  forwarding to Foundry, and keeps prompt/completion content out of policy
  diagnostics.
- A minimal inference request through the APIM URL returned HTTP `200` and
  preserved the safe caller correlation marker. The same request through
  DataForge's configured `AzureOpenAI` client also succeeded, proving the
  application path uses APIM rather than a direct Foundry key route.

## Preview Validation

- Backend preview label: `monitor`.
- Current backend preview revision: `ca-dataforge-backend--monitor723`.
- Preview image: `acrdataforgedev.azurecr.io/dataforge-backend:monitoring-20260721-v2`.
- Revision health: `Healthy`, one replica, and zero production traffic.
- A direct preview health check returned HTTP `200` with required Foundry,
  Search, MCP, Speech, Blob, and Content Safety dependency probes healthy.
- The APIM endpoint rejects anonymous text requests with HTTP `401`; it cannot
  relay an unauthenticated caller to Foundry. A managed-identity request with
  the application role returned HTTP `200`.
- Browser automation cannot reuse the signed-in in-app session in this
  environment (`ERR_BLOCKED_BY_CLIENT`), so authenticated owner-page UI
  validation remains a manual preview check rather than a claimed automation
  result.

## Production Activation

- Backend production revision: `ca-dataforge-backend--monitorcachee08b4d2` at
  `100%` traffic, using `dataforge-backend:monitor-cache-e08b4d2`.
  `ca-dataforge-backend--apimprod723` remains retained as a zero-traffic
  rollback revision.
- Web production revision: `ca-dataforge-web--monitorcachee08b4d2` at `100%`
  traffic, using `dataforge-web:monitor-cache-e08b4d2`.
  `ca-dataforge-web--monprod722` remains retained as a zero-traffic rollback
  revision.
- The production web revision explicitly proxies to the production backend
  root hostname. The `monitor` preview label remains at `0%` and points to
  the independently validated preview revisions.
- The new backend and web revisions were created at `0%`, reached `Healthy`,
  then were promoted backend-first. Post-cutover
  `GET /api/health` returned HTTP `200` with Foundry, Blob, and Content Safety
  probes healthy. The public web endpoint continues to route unauthenticated
  requests to Easy Auth, as expected.
- Automated verification before build: `1063 passed, 1 skipped` in the full
  backend suite; `86` frontend tests and the Vite production build passed.
- An authenticated production browser session loaded the owner-only `监视`
  navigation after cutover. The browser automation session reset while reading
  a larger monitor-page snapshot, so this record does not claim an
  authenticated cache miss/hit interaction from that session.
- The production gateway candidate passed its direct health check and a
  DataForge-client inference through APIM before the traffic cutover. Existing
  text-model and MAF application calls now use the managed-identity APIM route.
  Image generation remains on its existing direct Azure OpenAI integration and
  is explicitly outside the current APIM coverage.

## Cache and Request Observability

- Feasibility-analysis cache events are now persisted with only safe Redis
  meter fields: state, provider, elapsed time, and on a hit, valid source token
  usage and a price-card estimate. Cache keys, prompts, responses, credentials,
  and provider error text are not persisted in this meter.
- `/api/monitoring` now exposes a separate `summary.cache` aggregation. It
  reports eligible Redis events, hits, misses, unavailable events, hit rate,
  avoided source tokens, and an `estimated` / `partial` / `unavailable` avoided
  cost state. It never treats cache savings as an Azure billing amount.
- The same endpoint returns at most 30 newest request records. Each record is
  an allow-listed projection of event time, safe member pseudonym when the
  tenant identity is trusted, non-identifying workspace label, route,
  deployment, normalized status, observed tokens, model latency, Redis state,
  and validated trace metadata. Raw Entra IDs, email addresses, prompts,
  errors, headers, cache keys, response IDs, and workspace IDs are excluded.
- The production `监视` page presents APIM governance, observed tokens,
  estimated cost, and Redis reuse as separate KPI cards. It also provides a
  bounded recent-request table and a details drawer containing only the safe
  request fields above.
- APIM evidence and Redis reuse measure different boundaries: APIM verifies
  governed text-model ingress; Redis describes application-side reuse. Their
  token and cost figures are deliberately not added together.

## APIM Metric Ingestion

- The APIM API diagnostic is attached to `appi-dataforge-dev` with 100%
  sampling, custom metrics enabled, and explicit zero-byte body and empty
  header capture for both frontend and backend pipelines. Prompt, completion,
  and Authorization data are therefore not emitted by this diagnostic.
- The `llm-emit-token-metric` policy is enabled. Azure Monitor metric and
  Application Insights ingestion is asynchronous. After the metric diagnostic
  deployment, Application Insights recorded `Total Tokens`, `Prompt Tokens`,
  and `Completion Tokens` for `API ID=dataforge-text` and
  `Gateway ID=managed`; the dimensioned metric path is therefore verified.
- APIM additionally emits a `Workspace Hash` dimension from a one-way
  SHA-256 header. The backend queries that hash only to mark a workspace's
  governance view as verified; it never returns raw Application Insights rows.
- This workspace-scoped query was verified in the production candidate: it
  returned one governed call and 25 aggregate tokens for the synthetic smoke
  workspace, without returning a prompt, completion, URL, or identity.
- The Application Insights **Usage and estimated costs** -> **Custom metrics
  (Preview)** -> **With dimensions** setting is active in the current
  environment. Recheck this setting only if the Application Insights resource
  is replaced.

## Entra Registration State

The one-time Entra portal setup is complete for the runtime gateway app:

1. A single-tenant application registration exposes the gateway audience.
2. Its enabled application role is `invoke_as_application`.
3. The `ca-dataforge-backend` managed identity is assigned that role.
4. The runtime token was decoded inside the Container App without exposing the
   token and confirmed to contain the role.

No client secret, APIM subscription key, or user token is stored in this repo.

## Recheck Commands

```powershell
az account show --query '{subscription:name,subscriptionId:id,user:user}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-apim-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az apim show -g rg-dataforge-dev -n dfmonapim721 `
  --query '{name:name,sku:sku.name,state:provisioningState,principalId:identity.principalId}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-text-api-v1issuer-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-apim-telemetry-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-apim-telemetry-metrics-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-text-api-workspace-metric-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az deployment group show -g rg-dataforge-dev -n df-monitoring-apim-telemetry-privacy-20260721 `
  --query '{state:properties.provisioningState,error:properties.error}' -o json
az containerapp show -g rg-dataforge-dev -n ca-dataforge-backend `
  --query 'properties.configuration.ingress.traffic' -o json
```

If the deployment identity changes, rerun the role verification before making
any production routing change.

## 2026-08-09 DeepSeek routing and Entra identity production update

- Current backend: `ca-dataforge-backend--dse9135641`, Healthy, Running, 100%
  traffic.
- Current web: `ca-dataforge-web--dse9135641`, Healthy, Running, 100% traffic.
- Provider connectors and tenant-scoped external routing are enabled; direct
  provider routing is active and `DF_FINOPS_ACTIONS_ENABLED` remains `0`.
- The production health path verifies Foundry, Search, MCP, Speech, Blob, and
  Content Safety. Anonymous users are still redirected through Easy Auth.
- Python, Node, Vite, Playwright, focused pricing checks, log checks, and the
  committed-code secret scan passed before and after cutover.
- The signed-in browser identity, provider connection, and governed DeepSeek
  selection remain the final tenant-specific human acceptance checks.
- Full evidence:
  `docs/validation/2026-08-09-deepseek-entra-production.md`.

## 2026-08-02 FinOps ROI / risk UI production update

- Current web: `ca-dataforge-web--finui11c19f3`, Healthy, 100% traffic,
  digest `sha256:9b76a6c0d491cedd9fa7564a919f013074efe10d514b4b309d46458b1760b449`.
- Previous rollback web: `ca-dataforge-web--opsaug3cd8d44`, Healthy, 0%
  traffic.
- Backend remained `ca-dataforge-backend--opsaug3cd8d44`, Healthy, 100%
  traffic; `DF_FINOPS_ACTIONS_ENABLED` remained `0`.
- Python, Node, Vite, and Playwright gates passed before cutover. Anonymous
  stable-domain requests continue to enforce Easy Auth and backend
  `/api/health` returns HTTP 200.
- Full evidence:
  `docs/validation/2026-08-02-finops-ui-polish-production.md`.

## 2026-07-26 Operations Management production update

- Current backend: `ca-dataforge-backend--ops1596d4f`, Healthy, 100% traffic,
  digest `sha256:1514a5d5ca2b567983936ea89cc2b8406ed761c3ee98ebef10f41c8b596a10dd`.
- Current web: `ca-dataforge-web--ops4769b33`, Healthy, 100% traffic, digest
  `sha256:a459ad22a061e5cc006751f25fb65b17bf72dc952b3a33702aef669466ba2993`.
- FinOps read access is enabled; governance execution remains disabled.
- Authenticated UI acceptance verified immediate primary navigation, aligned
  update status, real trend proportions, compact persisted Operations AI,
  friendly evidence names, and no application console errors.
- The APIM reconciliation job is healthy and scheduled, but its latest
  ten-minute window contained zero application and APIM observations.
  Non-zero request correlation remains an explicit open acceptance gate.
- Full evidence:
  `docs/validation/2026-07-26-operations-management-evidence-pricing-production.md`.

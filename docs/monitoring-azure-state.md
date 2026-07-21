# Monitoring Azure Deployment State

Last verified: `2026-07-21`.

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

- Backend production revision: `ca-dataforge-backend--apimprod723` at `100%`
  traffic, using `dataforge-backend:monitoring-20260721-v4` with APIM gateway
  mode enabled. `ca-dataforge-backend--apimprod721` is retained as a rollback
  revision with zero traffic.
- Web production revision: `ca-dataforge-web--monprod722` at `100%` traffic,
  using `dataforge-web:monitoring-20260721-v2`.
- The production web revision explicitly proxies to the production backend
  root hostname. The `monitor` preview label remains at `0%` and points to
  the independently validated preview revisions.
- Post-cutover backend `GET /api/health` returned HTTP `200`; required
  dependency probes were healthy. The public web endpoint returned Easy Auth
  HTTP `401` when unauthenticated, as expected.
- The production gateway candidate passed its direct health check and a
  DataForge-client inference through APIM before the traffic cutover. Existing
  text-model and MAF application calls now use the managed-identity APIM route.
  Image generation remains on its existing direct Azure OpenAI integration and
  is explicitly outside the current APIM coverage.

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

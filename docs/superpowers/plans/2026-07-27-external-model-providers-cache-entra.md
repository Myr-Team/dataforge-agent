# External Model Providers, Cache Evidence, and Entra Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an organization-level DeepSeek provider connection, per-Agent Azure/DeepSeek primary and fallback routing, separate Redis and provider-cache evidence, provider-aware estimated cost, and Entra group-to-role governance without changing the MAF business graph.

**Architecture:** Keep Agent responsibilities and orchestration intact. Add a provider-neutral invocation layer below the current model policy, store organization provider metadata in additive FinOps SQL tables and credentials only in Key Vault, route production external-provider calls through a typed APIM bridge, normalize all attempts into FinOps events, and extend the existing Settings and Operations Management surfaces instead of adding navigation.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Azure SQL, Azure Key Vault, Azure API Management, Microsoft Graph, Redis, React, Vite, Node test runner, Playwright, Azure Container Apps.

## Global Constraints

- Preserve trusted Easy Auth claim derivation and every existing workspace authorization check.
- Never persist or return provider keys, prompt/completion bodies, provider response IDs, raw Graph object IDs in business views, APIM XML, Azure resource IDs, or internal error text.
- Provider management is organization-scoped and derives `tenant_ref` from trusted identity; the client never submits a tenant.
- Use Key Vault in candidate and production. A process-local secret is permitted only in unit tests.
- Keep `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`, `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`, and `DF_FINOPS_ACTIONS_ENABLED=0` until their acceptance gates pass.
- Keep pricing explicitly estimated and revision-pinned. Unknown model or incomplete usage is `unpriced`, never zero.
- Redis result reuse and DeepSeek provider KV cache are different populations and different metrics.
- Automatic fallback is allowed only for timeout, 429, or retryable 5xx before content/tool output starts.
- Additive SQL only. Resolve the existing production DDL permission gate before any candidate can depend on the new schema.
- Do not switch production traffic until candidate health, authenticated UI, real DeepSeek, Redis, APIM, Entra, and rollback checks are recorded and the user gives final traffic approval.
- Preserve unrelated untracked worktree files and do not stage `.superpowers/brainstorm/`, `web/.playwright-cli/`, `web/test-results/`, or `workspaces/ws-*`.

---

### Task 0: Release baseline and SQL migration gate

**Files:**
- Modify: `backend/sql/finops_schema.sql`
- Modify: `backend/finops/migrate.py`
- Modify: `docs/validation/2026-07-27-finops-remediation-candidate-runbook.md`
- Test: `tests/test_finops_sql_migration.py`
- Test: `tests/test_finops_sql_repositories.py`

**Interfaces:**
- Existing production schema remains readable during deployment.
- New tables are created only when absent.
- Migration command must fail closed and report a safe category when the managed identity lacks DDL permission.

- [x] **Step 1: Record the exact baseline and rollback targets**

Run:

```powershell
git status --short --branch
git log -5 --oneline
az containerapp revision list --name ca-dataforge-backend --resource-group rg-dataforge-dev --query "[].{name:name,traffic:properties.trafficWeight,health:properties.healthState}" -o table
az containerapp revision list --name ca-dataforge-web --resource-group rg-dataforge-dev --query "[].{name:name,traffic:properties.trafficWeight,health:properties.healthState}" -o table
```

Expected: current production and rollback revisions are named in the runbook; no secret-bearing environment output is captured.

- [x] **Step 2: Write failing migration tests**

Add assertions that repeated execution is idempotent and that these organization-scoped tables and indexes are present:

```sql
df_finops.model_provider
df_finops.model_provider_model
df_finops.provider_route_revision
df_finops.entra_group_mapping
```

Run:

```powershell
python -m pytest -q tests/test_finops_sql_migration.py tests/test_finops_sql_repositories.py
```

Expected: RED because the tables and repositories do not exist.

- [x] **Step 3: Add the additive schema**

Use tenant-scoped composite keys and revisions:

```sql
IF OBJECT_ID(N'df_finops.model_provider', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.model_provider (
        tenant_ref NVARCHAR(160) NOT NULL,
        provider_id NVARCHAR(80) NOT NULL,
        provider_type NVARCHAR(40) NOT NULL,
        display_name NVARCHAR(120) NOT NULL,
        base_url NVARCHAR(320) NOT NULL,
        secret_ref NVARCHAR(240) NOT NULL,
        connection_state NVARCHAR(32) NOT NULL,
        governance_state NVARCHAR(32) NOT NULL,
        revision INT NOT NULL,
        created_by_ref NVARCHAR(160) NOT NULL,
        updated_by_ref NVARCHAR(160) NOT NULL,
        created_at DATETIME2(7) NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_model_provider PRIMARY KEY (tenant_ref, provider_id)
    );
END;
```

Add state checks, unique display-name rules per tenant, model rows, immutable routing revisions, mapping priority, enabled state, and workspace-scope JSON with bounded size.

- [x] **Step 4: Verify DDL authority before application deployment**

Run the existing migration entry point against the candidate database identity.

Expected: success and idempotent second execution. If permission is denied, stop here, record the exact missing database role/action without adding broad Owner credentials, and do not deploy application code that reads the new tables.

- [x] **Step 5: Commit the migration gate**

```powershell
git add backend/sql/finops_schema.sql backend/finops/migrate.py tests/test_finops_sql_migration.py tests/test_finops_sql_repositories.py docs/validation/2026-07-27-finops-remediation-candidate-runbook.md
git commit -m "feat(finops): add provider and Entra governance schema"
```

---

### Task 1: Provider domain, SQL repository, and Key Vault-only secret lifecycle

**Files:**
- Create: `backend/model_providers.py`
- Create: `backend/model_provider_repository.py`
- Create: `backend/model_provider_secrets.py`
- Modify: `backend/connector_secret_store.py`
- Modify: `backend/requirements.txt`
- Test: `tests/test_model_providers.py`
- Test: `tests/test_model_provider_repository.py`
- Test: `tests/test_model_provider_secrets.py`

**Interfaces:**

```python
class ModelProviderRecord(BaseModel):
    provider_id: str
    provider_type: Literal["deepseek"]
    display_name: str
    base_url: str
    connection_state: Literal["testing", "connected", "degraded", "invalid", "disabled"]
    governance_state: Literal["pending", "governed", "degraded", "unmanaged"]
    available_models: list["ProviderModel"]
    last_tested_at: datetime | None
    last_success_at: datetime | None
    safe_error_category: str | None
    revision: int

class ModelProviderRepository(Protocol):
    def list(self, tenant_ref: str) -> list[ModelProviderRecord]: ...
    def get(self, tenant_ref: str, provider_id: str) -> ModelProviderRecord: ...
    def create(self, tenant_ref: str, draft: ProviderCreateRecord) -> ModelProviderRecord: ...
    def update(self, tenant_ref: str, provider_id: str, base_revision: int, patch: ProviderPatch) -> ModelProviderRecord: ...
```

- [x] **Step 1: Write RED tests for tenant isolation, masking, and revision conflicts**

Assert:

- provider reads always require a tenant;
- public serialization excludes `secret_ref`;
- duplicate or stale revisions return a typed conflict;
- `api_key` does not survive request-model serialization;
- SQL parameters never contain key material.

Run:

```powershell
python -m pytest -q tests/test_model_providers.py tests/test_model_provider_repository.py
```

- [x] **Step 2: Add provider models and SQL repository**

Follow the existing FinOps SQL repository connection pattern. Keep SQL errors behind `ModelProviderPersistenceError`; never include SQL or values in public error text.

- [x] **Step 3: Add a provider-specific Key Vault facade**

Reuse the existing Key Vault client mechanics but derive a tenant/provider-scoped opaque secret name:

```python
class ModelProviderSecretStore(Protocol):
    def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str: ...
    def get(self, tenant_ref: str, provider_id: str, secret_ref: str) -> str: ...
    def rotate(self, tenant_ref: str, provider_id: str, api_key: str) -> str: ...

def provider_secret_name(tenant_ref: str, provider_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_ref}:{provider_id}".encode()).hexdigest()[:40]
    return f"df-model-provider-{digest}"
```

Candidate/production construction must raise `provider_key_vault_required` if `DF_KEY_VAULT_URL` is absent. Do not silently fall back to session storage.

- [x] **Step 4: Write and pass secret redaction tests**

Simulate Key Vault set/get failures containing a marker key and assert the marker is absent from exceptions, logs, audit payloads, and API-shaped results.

Run:

```powershell
python -m pytest -q tests/test_model_provider_secrets.py
```

- [x] **Step 5: Commit the provider persistence layer**

```powershell
git add backend/model_providers.py backend/model_provider_repository.py backend/model_provider_secrets.py backend/connector_secret_store.py backend/requirements.txt tests/test_model_providers.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py
git commit -m "feat(providers): persist tenant providers with Key Vault secrets"
```

---

### Task 2: Safe DeepSeek endpoint validation and adapter

**Files:**
- Create: `backend/provider_endpoint.py`
- Create: `backend/provider_client.py`
- Create: `backend/deepseek_provider.py`
- Create: `backend/provider_usage.py`
- Test: `tests/test_provider_endpoint.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_provider_usage.py`

**Interfaces:**

```python
class ProviderInvocation(BaseModel):
    request_ref: str
    correlation_ref: str
    workspace_id: str
    agent_id: str | None
    execution_kind: str
    model_id: str
    messages: list[ProviderMessage]
    tools: list[ProviderTool] = []
    stream: bool = False

class ProviderUsage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    provider_cache_hit_tokens: int | None
    provider_cache_miss_tokens: int | None
    total_tokens: int | None

class ProviderResult(BaseModel):
    text: str | None
    tool_calls: list[NormalizedToolCall]
    usage: ProviderUsage
    latency_ms: int
    output_started: bool
    safe_error_category: str | None
```

- [x] **Step 1: Write RED endpoint/SSRF tests**

Cover HTTPS, exact allowlisted host, userinfo, query, fragment, port, loopback, RFC1918, link-local, metadata endpoints, DNS rebinding, redirects, and response-size limits.

```python
assert validate_provider_endpoint("deepseek", "https://api.deepseek.com") == "https://api.deepseek.com"
with pytest.raises(ProviderEndpointError):
    validate_provider_endpoint("deepseek", "https://127.0.0.1")
```

- [x] **Step 2: Implement endpoint validation**

The client owns the allowlist:

```python
PROVIDER_HOSTS = {"deepseek": frozenset({"api.deepseek.com"})}
```

Resolve and validate every address before connect and every redirect target before carrying the Authorization header. Use fixed connect/read timeouts and a bounded response reader.

- [x] **Step 3: Write RED adapter and usage tests**

Test normal text, tool calls, thinking fields, non-streaming usage, SSE comments, streaming start, malformed JSON, and safe mappings for 400/401/402/422/429/timeout/5xx/503.

- [x] **Step 4: Implement the DeepSeek Chat Completions adapter**

Use the official OpenAI-compatible endpoint, set Authorization only after endpoint validation, and normalize:

```python
details = usage.get("prompt_tokens_details") or {}
ProviderUsage(
    input_tokens=usage.get("prompt_tokens"),
    output_tokens=usage.get("completion_tokens"),
    reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
    provider_cache_hit_tokens=usage.get("prompt_cache_hit_tokens"),
    provider_cache_miss_tokens=usage.get("prompt_cache_miss_tokens"),
    total_tokens=usage.get("total_tokens"),
)
```

Unknown or absent fields remain `None`; do not infer hit tokens from total input.

- [x] **Step 5: Pass focused tests and commit**

```powershell
python -m pytest -q tests/test_provider_endpoint.py tests/test_deepseek_provider.py tests/test_provider_usage.py
git add backend/provider_endpoint.py backend/provider_client.py backend/deepseek_provider.py backend/provider_usage.py tests/test_provider_endpoint.py tests/test_deepseek_provider.py tests/test_provider_usage.py
git commit -m "feat(providers): add safe DeepSeek invocation adapter"
```

---

### Task 3: Organization provider APIs with durable audit

**Files:**
- Create: `backend/model_provider_service.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/app.py`
- Test: `tests/test_model_provider_api.py`
- Test: `tests/test_model_provider_audit.py`

**Interfaces:**

- `GET /api/model-providers`
- `POST /api/model-providers`
- `POST /api/model-providers/{provider_id}/test`
- `POST /api/model-providers/{provider_id}/rotate-secret`
- `PATCH /api/model-providers/{provider_id}`
- `POST /api/model-providers/{provider_id}/disable`

Creation accepts:

```json
{
  "provider_type": "deepseek",
  "display_name": "DeepSeek",
  "base_url": "https://api.deepseek.com",
  "api_key": "<write-only>"
}
```

Public responses include masked secret status but never `api_key` or `secret_ref`.

- [ ] **Step 1: Write RED API authorization and contract tests**

Cover trusted-tenant derivation, Owner/Admin write access, Viewer denial, cross-tenant provider IDs, masked responses, 409 revision drift, disabled route behavior, and feature flag off.

- [ ] **Step 2: Implement the service transaction order**

For create:

1. authorize tenant Owner/Admin;
2. validate endpoint and request;
3. write Key Vault secret;
4. persist provider metadata;
5. record durable audit;
6. test connection;
7. return masked state.

If persistence or audit fails, delete the newly written secret version/reference where recoverable and return a safe failure. Never report success before durable audit.

- [ ] **Step 3: Add API routes and exception mappings**

Use `DF_PROVIDER_CONNECTORS_ENABLED`. Map invalid endpoint to 400, forbidden to 403, missing to 404, revision conflict to 409, Key Vault/audit/provider persistence failure to 503.

- [ ] **Step 4: Pass tests and commit**

```powershell
python -m pytest -q tests/test_model_provider_api.py tests/test_model_provider_audit.py
git add backend/model_provider_service.py backend/control_plane.py backend/app.py tests/test_model_provider_api.py tests/test_model_provider_audit.py
git commit -m "feat(api): manage organization model providers"
```

---

### Task 4: Provider-neutral routing, fallback, and APIM governance bridge

**Files:**
- Modify: `backend/model_policy.py`
- Modify: `backend/workspace_model_config.py`
- Create: `backend/provider_gateway.py`
- Create: `backend/provider_fallback.py`
- Create: `backend/provider_apim.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/foundry_client.py`
- Test: `tests/test_model_policy.py`
- Test: `tests/test_model_routing_api.py`
- Test: `tests/test_provider_fallback.py`
- Test: `tests/test_provider_apim.py`
- Test: `tests/test_maf_provider_routing.py`

**Interfaces:**

```python
class ModelRoute(BaseModel):
    route_id: str
    provider_id: str | None
    provider_type: Literal["azure_foundry", "deepseek"]
    model_id: str
    label: str
    capabilities: frozenset[str]

class RouteSelection(BaseModel):
    primary: ModelRoute
    fallback: ModelRoute | None
    policy_revision: int
    price_mapping_revision: int | None
```

- [ ] **Step 1: Write RED route compatibility tests**

Assert provider/model/capability compatibility, disabled provider rejection, stale route revision 409, and tenant/workspace scoping.

- [ ] **Step 2: Extend route serialization and storage**

Keep existing Azure route IDs valid. Add provider identity without rewriting old policies. Reject unknown provider/model combinations server-side.

- [ ] **Step 3: Write RED fallback state-machine tests**

Expected decision:

```python
def may_fallback(error: ProviderFailure, *, output_started: bool, side_effect_started: bool) -> bool:
    return (
        not output_started
        and not side_effect_started
        and error.category in {"timeout", "rate_limited", "provider_unavailable"}
    )
```

Explicitly test no fallback for 400, 401, 402, 422, content policy, emitted stream content/tool call, or uncertain tool side effects.

- [ ] **Step 4: Implement the provider gateway below MAF**

MAF passes normalized messages/tools to one gateway. The gateway selects a typed adapter, records each attempt independently, and returns the existing Agent-facing result shape. Do not change Agent prompts, responsibilities, or graph edges.

- [ ] **Step 5: Implement the typed APIM bridge**

`provider_apim.py` accepts only server-owned provider data and renders a server-owned template. No endpoint accepts XML, policy fragments, resource IDs, or scripts.

Candidate verification must check:

- candidate API revision created;
- managed identity request returns 200;
- anonymous request returns 401;
- expected policy hash/ETag reads back;
- correlation header and usage fields survive;
- activation is not attempted while `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`.

- [ ] **Step 6: Pass focused tests and commit**

```powershell
python -m pytest -q tests/test_model_policy.py tests/test_model_routing_api.py tests/test_provider_fallback.py tests/test_provider_apim.py tests/test_maf_provider_routing.py
git add backend/model_policy.py backend/workspace_model_config.py backend/provider_gateway.py backend/provider_fallback.py backend/provider_apim.py backend/maf_agents.py backend/maf_team_runtime.py backend/foundry_client.py tests/test_model_policy.py tests/test_model_routing_api.py tests/test_provider_fallback.py tests/test_provider_apim.py tests/test_maf_provider_routing.py
git commit -m "feat(runtime): route Agents across governed model providers"
```

---

### Task 5: Redis result-cache eligibility and provider KV evidence

**Files:**
- Modify: `backend/cache_store.py`
- Create: `backend/result_cache_policy.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Modify: `backend/finops/models.py`
- Modify: `backend/finops/normalization.py`
- Modify: `backend/finops/query.py`
- Modify: `backend/finops/request_detail.py`
- Test: `tests/test_result_cache_policy.py`
- Test: `tests/test_orchestrator_cache.py`
- Test: `tests/test_finops_normalization.py`
- Test: `tests/test_finops_query.py`

**Interfaces:**

```python
class ResultCacheEvidence(BaseModel):
    eligible: bool
    state: Literal["hit", "miss", "bypassed", "unavailable"]
    reason: str
    lookup_latency_ms: int | None
    policy_revision: int
    source_result_version: str | None

class ProviderCacheEvidence(BaseModel):
    state: Literal["hit", "partial_hit", "miss", "unavailable"]
    hit_tokens: int | None
    miss_tokens: int | None
    evidence_state: Literal["observed", "partial", "unavailable"]
```

- [ ] **Step 1: Write RED eligibility and cache-key tests**

The key must include tenant, workspace, authorized data revision, execution kind, Agent, provider/model route, prompt template, tool/schema revision, material generation parameters, and cache-policy revision.

Test bypass reasons for live data, side-effecting tools, unstable conversation state, missing data revision, changed route/config, and explicit disable.

- [ ] **Step 2: Implement fail-open Redis lookup**

Redis failure records `unavailable` and continues to the provider. A hit returns the exact stored result version and skips DeepSeek, so provider cache evidence for that request is unavailable.

- [ ] **Step 3: Normalize provider cache evidence**

Compute provider hit rate only when both populations are known:

```python
denominator = hit_tokens + miss_tokens
hit_rate_pct = round(hit_tokens / denominator * 100, 2) if denominator else None
```

Do not mix Redis request hit rate with provider token hit rate.

- [ ] **Step 4: Preserve run and request evidence**

Store only safe cache reasons, versions, token counts, provider/model, and revisions. Do not place result bodies in FinOps request facts.

- [ ] **Step 5: Verify real miss-to-hit behavior locally and commit**

```powershell
python -m pytest -q tests/test_result_cache_policy.py tests/test_orchestrator_cache.py tests/test_finops_normalization.py tests/test_finops_query.py
git add backend/cache_store.py backend/result_cache_policy.py backend/orchestrator.py backend/run_store.py backend/finops/models.py backend/finops/normalization.py backend/finops/query.py backend/finops/request_detail.py tests/test_result_cache_policy.py tests/test_orchestrator_cache.py tests/test_finops_normalization.py tests/test_finops_query.py
git commit -m "feat(cache): separate Redis reuse from provider cache evidence"
```

---

### Task 6: DeepSeek official price revision and provider-aware estimated cost

**Files:**
- Modify: `backend/finops/data/official_model_prices.json`
- Modify: `backend/finops/official_pricing.py`
- Modify: `backend/finops/management.py`
- Modify: `backend/finops/sql_management.py`
- Test: `tests/test_finops_official_pricing.py`
- Test: `tests/test_finops_management.py`
- Test: `tests/test_finops_sql_management.py`

**Interfaces:**

Each immutable catalog record includes:

```json
[
  {
    "provider_type": "deepseek",
    "model": "deepseek-v4-flash",
    "currency": "USD",
    "input_cache_hit_per_million": 0.0028,
    "input_cache_miss_per_million": 0.14,
    "output_per_million": 0.28,
    "locally_effective_at": "2026-07-27T00:00:00Z",
    "source_url": "https://api-docs.deepseek.com/quick_start/pricing/",
    "reviewed_at": "2026-07-27T00:00:00Z",
    "revision": "deepseek-2026-07-27-v1"
  },
  {
    "provider_type": "deepseek",
    "model": "deepseek-v4-pro",
    "currency": "USD",
    "input_cache_hit_per_million": 0.003625,
    "input_cache_miss_per_million": 0.435,
    "output_per_million": 0.87,
    "locally_effective_at": "2026-07-27T00:00:00Z",
    "source_url": "https://api-docs.deepseek.com/quick_start/pricing/",
    "reviewed_at": "2026-07-27T00:00:00Z",
    "revision": "deepseek-2026-07-27-v1"
  }
]
```

These values were re-verified against the official DeepSeek pricing page on 2026-07-27. `locally_effective_at` is the start of DataForge's catalog revision, not a claim about when DeepSeek first introduced the price. Re-verify before committing implementation and create a new revision if the official page has changed.

- [ ] **Step 1: Re-verify the official catalog at implementation time**

Use only the official DeepSeek pricing page. Record model IDs, cache-hit input, cache-miss input, output rates, effective date, source URL, and review timestamp. Do not infer future or alias-model rates.

- [ ] **Step 2: Write RED price and calculation tests**

Cover cache hit/miss input, output, reasoning treatment, per-attempt fallback cost, unsupported model, missing usage, historical revision stability, and decimal rounding.

- [ ] **Step 3: Extend the immutable catalog and calculator**

Calculate each actual attempt separately. Redis savings require a source result with pinned model/price/observed usage. Provider KV savings use hit-vs-miss price difference only.

- [ ] **Step 4: Pass tests and commit**

```powershell
python -m pytest -q tests/test_finops_official_pricing.py tests/test_finops_management.py tests/test_finops_sql_management.py
git add backend/finops/data/official_model_prices.json backend/finops/official_pricing.py backend/finops/management.py backend/finops/sql_management.py tests/test_finops_official_pricing.py tests/test_finops_management.py tests/test_finops_sql_management.py
git commit -m "feat(finops): price DeepSeek usage with immutable revisions"
```

---

### Task 7: Entra group mapping, Graph overage fallback, and fail-closed authorization

**Files:**
- Create: `backend/entra_group_mapping.py`
- Create: `backend/entra_membership.py`
- Modify: `backend/identity.py`
- Modify: `backend/graph_client.py`
- Modify: `backend/workspace_authz.py`
- Modify: `backend/control_plane.py`
- Test: `tests/test_entra_group_mapping.py`
- Test: `tests/test_entra_membership.py`
- Test: `tests/test_workspace_authz.py`
- Test: `tests/test_entra_group_api.py`

**Interfaces:**

- `GET /api/identity-governance`
- `GET /api/identity-governance/groups?query=finance`
- `POST /api/identity-governance/group-mappings`
- `PATCH /api/identity-governance/group-mappings/{mapping_id}`
- `POST /api/identity-governance/group-mappings/{mapping_id}/disable`

```python
ROLE_ORDER = {"viewer": 1, "editor": 2, "admin": 3}

def resolve_access(owner, explicit_member, group_matches):
    if owner:
        return owner
    if explicit_member and explicit_member.active:
        return explicit_member.role
    highest = highest_unambiguous_priority(group_matches)
    if highest.conflict:
        raise GroupMappingConflict()
    return highest.role if highest else None
```

- [ ] **Step 1: Write RED precedence and fail-closed tests**

Cover protected Owner, explicit membership, one mapping, equal-priority conflict, disabled mapping, workspace scope, tenant isolation, and prohibition on group-granted Owner.

- [ ] **Step 2: Detect group claims and overage safely**

Use trusted Easy Auth claims. When the token indicates overage, call only the service-constructed Graph transitive-membership endpoint for the signed-in user. Use a short-lived privacy-safe Redis cache.

- [ ] **Step 3: Enforce least-privileged Graph behavior**

Expose permission state for `User.ReadBasic.All` and `GroupMember.Read.All`. If membership resolution fails, ignore group grants and fall back to explicit membership only; do not retain stale elevation.

- [ ] **Step 4: Add audited, revisioned management APIs**

Only Owner/Admin may manage mappings; Owner cannot be a mapping target. Stale base revisions return 409 and durable audit is required before success.

- [ ] **Step 5: Pass tests and commit**

```powershell
python -m pytest -q tests/test_entra_group_mapping.py tests/test_entra_membership.py tests/test_workspace_authz.py tests/test_entra_group_api.py
git add backend/entra_group_mapping.py backend/entra_membership.py backend/identity.py backend/graph_client.py backend/workspace_authz.py backend/control_plane.py tests/test_entra_group_mapping.py tests/test_entra_membership.py tests/test_workspace_authz.py tests/test_entra_group_api.py
git commit -m "feat(identity): govern workspace access with Entra groups"
```

---

### Task 8: Settings UI for providers, routing, cache, and identity

**Files:**
- Create: `web/src/ProviderConnectionsPage.jsx`
- Create: `web/src/providerConnectionsViewModel.js`
- Create: `web/src/IdentityAccessPage.jsx`
- Create: `web/src/identityAccessViewModel.js`
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/GovernanceCenter.jsx`
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/modelRoutingViewModel.js`
- Modify: `web/src/styles.css`
- Test: `web/src/providerConnectionsViewModel.test.mjs`
- Test: `web/src/identityAccessViewModel.test.mjs`
- Test: `web/src/modelRoutingViewModel.test.mjs`
- Test: `web/tests/model-provider-settings.spec.mjs`
- Test: `web/tests/model-provider-routing.spec.mjs`
- Test: `web/tests/entra-identity-governance.spec.mjs`

**User experience:**

- Keep one `设置` entry.
- Add internal tabs/cards `模型提供商`, `Agent 模型`, `身份与访问`.
- Provider form accepts the key once, clears it immediately after submission, and never repopulates it.
- Agent routing options show provider badge, model, governance state, pricing state, and capability.
- Identity mapping shows display name and role; raw group ID appears only in an admin technical-detail drawer.

- [ ] **Step 1: Write RED Node view-model tests**

Test masked connection state, safe unavailable states, provider/model route labels, revision preservation, conflict presentation, and aggregate-only identity coverage for non-admin users.

- [ ] **Step 2: Write RED desktop/mobile Playwright flows**

Cover:

- create/test/rotate/disable DeepSeek connection;
- key input cleared and absent from DOM after save;
- per-Agent Azure primary + DeepSeek fallback;
- 409 reload and review;
- group search, mapping, conflict, disable;
- keyboard focus, help tooltip placement, drawer not crossing topbar;
- mobile stacking without horizontal overflow.

- [ ] **Step 3: Implement API clients and view models**

Every write includes `base_revision`. Map safe server codes to concise Chinese text; never render raw response bodies.

- [ ] **Step 4: Implement Settings panels**

Reuse the existing card, modal, drawer, icon, and button system. Do not put provider setup into the Data Workbench connector list because its scope is organization-level, not workspace data.

- [ ] **Step 5: Extend Model Routing**

Group route options by provider and disable routes that are ungoverned, disabled, capability-incompatible, or unpriced when the relevant policy requires pricing.

- [ ] **Step 6: Pass focused frontend tests and commit**

```powershell
Set-Location web
node --test src/providerConnectionsViewModel.test.mjs src/identityAccessViewModel.test.mjs src/modelRoutingViewModel.test.mjs
npx playwright test tests/model-provider-settings.spec.mjs tests/model-provider-routing.spec.mjs tests/entra-identity-governance.spec.mjs
Set-Location ..
git add web/src/ProviderConnectionsPage.jsx web/src/providerConnectionsViewModel.js web/src/IdentityAccessPage.jsx web/src/identityAccessViewModel.js web/src/api.js web/src/components.jsx web/src/GovernanceCenter.jsx web/src/ModelRoutingPage.jsx web/src/modelRoutingViewModel.js web/src/styles.css web/src/providerConnectionsViewModel.test.mjs web/src/identityAccessViewModel.test.mjs web/src/modelRoutingViewModel.test.mjs web/tests/model-provider-settings.spec.mjs web/tests/model-provider-routing.spec.mjs web/tests/entra-identity-governance.spec.mjs
git commit -m "feat(web): manage providers routing and Entra access"
```

---

### Task 9: Operations Management provider, cache, cost, and identity evidence

**Files:**
- Modify: `backend/finops/models.py`
- Modify: `backend/finops/query.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/request_detail.py`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsInteraction.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_finops_query.py`
- Test: `tests/test_finops_router.py`
- Test: `tests/test_finops_request_detail.py`
- Test: `web/src/finopsViewModel.test.mjs`
- Test: `web/src/finopsInteraction.test.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Response additions:**

```json
{
  "provider": {"id": "provider_demo", "type": "deepseek", "model": "deepseek-v4-flash"},
  "cache": {
    "redis": {"eligible_requests": 0, "hit": 0, "miss": 0, "bypassed": 0, "unavailable": 0},
    "provider_kv": {"hit_tokens": 0, "miss_tokens": 0, "hit_rate_pct": null}
  },
  "fallback": {"attempts": 0, "reasons": []},
  "identity_attribution": {
    "explicit_member": 0,
    "group_mapping": 0,
    "unmapped": 0,
    "graph_state": "available"
  }
}
```

- [ ] **Step 1: Write RED API aggregation tests**

Verify provider/Agent grouping, separate cache denominators, per-attempt cost, fallback reasons, identity aggregates, partial/unavailable/unpriced states, and actor-detail authorization.

- [ ] **Step 2: Extend the query/API contracts**

Add `provider` to filters and breakdowns. Preserve backward-compatible fields and existing cursor behavior.

- [ ] **Step 3: Write RED view-model and Playwright tests**

Assert bars and charts scale from actual values, cache tooltip identifies Redis vs provider KV, provider and identity filters alter displayed evidence, and zero remains observed zero rather than unavailable.

- [ ] **Step 4: Implement the compact UI**

Do not add top metric cards. Put provider, cache-layer, fallback, pricing, and identity evidence in filters, trend switchers, metric help, data-trust panels, and request detail.

- [ ] **Step 5: Pass focused tests and commit**

```powershell
python -m pytest -q tests/test_finops_query.py tests/test_finops_router.py tests/test_finops_request_detail.py
Set-Location web
node --test src/finopsViewModel.test.mjs src/finopsInteraction.test.mjs
npx playwright test tests/finops-operations-management.spec.mjs
Set-Location ..
git add backend/finops/models.py backend/finops/query.py backend/finops/router.py backend/finops/request_detail.py web/src/finopsViewModel.js web/src/finopsInteraction.js web/src/FinOpsPortal.jsx web/src/styles.css tests/test_finops_query.py tests/test_finops_router.py tests/test_finops_request_detail.py web/src/finopsViewModel.test.mjs web/src/finopsInteraction.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "feat(finops): expose provider cache and identity evidence"
```

---

### Task 10: Full verification and zero-traffic candidate deployment

**Files:**
- Modify: `docs/validation/2026-07-27-external-provider-candidate.md`
- Modify: `docs/validation/2026-07-27-finops-remediation-candidate-runbook.md`

- [ ] **Step 1: Run repository verification**

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
git status --short
```

Expected: all suites pass. Any failure blocks deployment; do not label a partial run as full validation.

- [ ] **Step 2: Scan for secret and privacy regressions**

Search tracked diffs and built assets for test marker keys, Authorization values, secret references, raw prompts/completions, raw provider IDs, and raw Graph IDs.

Expected: no secret material and no forbidden payloads.

- [ ] **Step 3: Build immutable backend and web images**

Tag both images with the same Git commit SHA. Push the exact tested source state. Record image digests, not credentials.

- [ ] **Step 4: Apply additive SQL and verify twice**

Run the migration with the production database identity before starting candidate application revisions. Run it a second time to prove idempotence.

Expected: both complete; if permission fails, stop and leave production unchanged.

- [ ] **Step 5: Deploy zero-traffic candidates**

Create backend and web revisions at 0% traffic. Configure:

```text
DF_PROVIDER_CONNECTORS_ENABLED=1
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0
DF_PROVIDER_CACHE_EVIDENCE_ENABLED=1
DF_ENTRA_GROUP_MAPPING_ENABLED=1
DF_FINOPS_ACTIONS_ENABLED=0
```

Expected: both revisions Healthy at 0%; current production remains 100%.

- [ ] **Step 6: Run authenticated candidate acceptance**

Record evidence for:

- organization Owner can add/test/rotate/disable DeepSeek without the key appearing in DOM, logs, audit, API, screenshots, or SQL;
- one Azure and one DeepSeek call normalize to correct provider/model/Token/cost;
- APIM correlation joins to DataForge `request_ref`;
- one allowed pre-output fallback and one blocked post-output fallback;
- same eligible analysis produces Redis miss then hit;
- DeepSeek call reports actual provider cache hit/miss tokens or truthful unavailable;
- one official price is hand-calculated and matches the Portal;
- Entra explicit membership wins over group mapping;
- equal-priority group conflict denies access;
- Graph overage failure falls back to explicit membership only;
- desktop/mobile Settings and Operations Management load without delayed navigation, failed fetches, overlap, overflow, or raw IDs.

- [ ] **Step 7: Enable APIM provisioning only in the candidate**

After the typed template review, set `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=1` on the zero-traffic candidate and verify candidate revision, 200 managed identity, 401 anonymous, ETag/hash readback, correlation, and usage preservation. Keep production routing disabled.

- [ ] **Step 8: Enable candidate external routing**

Set `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=1` only on the zero-traffic candidate and repeat real DeepSeek, fallback, FinOps, and cache checks.

- [ ] **Step 9: Record rollback**

Verify the known backend/web rollback revisions remain available and that the schema is backward-compatible. Document the command to restore 100% traffic without executing it.

- [ ] **Step 10: Request explicit production traffic approval**

Present commit SHA, PR, full test totals, image digests, SQL evidence, candidate revision health, authenticated screenshots, APIM/Redis/Entra/DeepSeek acceptance, and rollback target.

Do not switch production traffic in this task until the user explicitly approves the evidenced candidate.

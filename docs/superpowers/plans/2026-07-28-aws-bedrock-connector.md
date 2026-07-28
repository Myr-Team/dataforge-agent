# AWS Bedrock Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an organization-scoped AWS Bedrock configuration surface that securely stores AWS credentials and performs a read-only model-list connection test without enabling Agent routing.

**Architecture:** Extend the existing model-provider registry with `aws_bedrock`, but keep credentials in Azure Key Vault as a write-only JSON bundle. A focused Bedrock control-plane adapter uses boto3 and AWS SigV4 to call `ListFoundationModels`; the existing service and API dispatch by provider type while all runtime-routing gates remain off.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, boto3 1.43.51, Azure Key Vault, SQL Server, React, Node test runner, Vite, Playwright.

## Global Constraints

- AWS Bedrock is configuration and connection testing only; do not add it to Agent routing, fallback, APIM provisioning, pricing, or FinOps invocation metrics.
- Keep `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`, `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`, and `DF_FINOPS_ACTIONS_ENABLED=0`.
- Require both `DF_PROVIDER_CONNECTORS_ENABLED=1` and `DF_AWS_BEDROCK_CONNECTOR_ENABLED=1` before exposing Bedrock writes.
- Derive tenant and actor only from trusted Easy Auth claims; only Owner/Admin may read or mutate provider configuration.
- Access Key ID, Secret Access Key, and Session Token are write-only and must never enter SQL, API responses, logs, audits, screenshots, fixtures, Git, or deployment commands.
- Use server-owned supported regions and server-constructed HTTPS endpoints; reject arbitrary Bedrock endpoints.
- Preserve `base_revision` optimistic concurrency and durable audit-before-mutation behavior.

## File Structure

- Create `backend/aws_bedrock_provider.py`: Bedrock region validation, credential type, boto3 client factory, read-only model discovery, and safe error mapping.
- Modify `backend/model_providers.py`: add `aws_bedrock` and optional provider region.
- Modify `backend/model_provider_service.py`: dispatch connection tests by provider type.
- Modify `backend/model_provider_router.py`: accept a discriminated Bedrock request and enforce the Bedrock feature flag.
- Modify `backend/model_provider_repository.py`: persist and reload `region`.
- Modify `backend/model_provider_secrets.py`: store/retrieve a validated credential bundle without changing the public secret contract.
- Modify `backend/sql/finops_schema.sql`: add the nullable region column and permit `aws_bedrock`.
- Modify `backend/requirements.txt`: pin boto3.
- Create `tests/test_aws_bedrock_provider.py`: adapter and error mapping tests.
- Modify `tests/test_model_providers.py`, `tests/test_model_provider_repository.py`, `tests/test_model_provider_secrets.py`, `tests/test_model_provider_api.py`, and `tests/test_finops_sql_migration.py`.
- Create `web/src/AwsBedrockConnectionForm.jsx`: isolated Bedrock creation form.
- Modify `web/src/ProviderConnectionsPage.jsx`, `web/src/providerConnectionsViewModel.js`, `web/src/api.js`, and `web/src/styles.css`.
- Modify `web/src/providerConnectionsViewModel.test.mjs`, `web/src/finopsApi.test.mjs`, `web/tests/finopsMockApi.mjs`, and `web/tests/finops-pricing-routing-remediation.spec.mjs`.
- Modify `backend/.env.example`, `README.md`, and add candidate evidence under `docs/validation/`.

---

### Task 1: Bedrock Domain Types, Secret Bundle, and Additive SQL

**Files:**
- Create: `backend/aws_bedrock_provider.py`
- Modify: `backend/model_providers.py:10-107`
- Modify: `backend/model_provider_secrets.py:1-130`
- Modify: `backend/model_provider_repository.py`
- Modify: `backend/sql/finops_schema.sql:450-494`
- Modify: `backend/requirements.txt`
- Test: `tests/test_aws_bedrock_provider.py`
- Test: `tests/test_model_providers.py`
- Test: `tests/test_model_provider_repository.py`
- Test: `tests/test_model_provider_secrets.py`
- Test: `tests/test_finops_sql_migration.py`

**Interfaces:**
- Produces: `AwsBedrockCredential`, `bedrock_control_endpoint(region)`, and `ModelProviderRecord.region`.
- Consumes: existing `ModelProviderSecretStore.put/get/rotate` and model-provider repository contracts.

- [ ] **Step 1: Write failing domain and migration tests**

```python
def test_bedrock_region_builds_server_owned_control_endpoint() -> None:
    assert bedrock_control_endpoint("ap-southeast-1") == (
        "https://bedrock.ap-southeast-1.amazonaws.com"
    )
    with pytest.raises(ValueError, match="bedrock_region_unsupported"):
        bedrock_control_endpoint("https://evil.example")


def test_bedrock_credential_serialization_is_write_only() -> None:
    value = AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker",
        session_token=None,
    )
    encoded = value.to_secret_value()
    assert AwsBedrockCredential.from_secret_value(encoded) == value
    assert "secret-marker" not in repr(value)


def test_finops_schema_allows_bedrock_and_adds_region() -> None:
    sql = Path("backend/sql/finops_schema.sql").read_text(encoding="utf-8")
    assert "region NVARCHAR(32) NULL" in sql
    assert "N'aws_bedrock'" in sql
```

- [ ] **Step 2: Run tests and confirm the expected failure**

Run:

```powershell
python -m pytest tests/test_aws_bedrock_provider.py tests/test_model_providers.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py tests/test_finops_sql_migration.py -q
```

Expected: FAIL because `AwsBedrockCredential`, `bedrock_control_endpoint`, and the Bedrock schema support do not exist.

- [ ] **Step 3: Add the types, pinned dependency, and schema migration**

Use these exact public shapes:

```python
# backend/model_providers.py
ProviderType = Literal["deepseek", "aws_bedrock"]

class ModelProviderRecord(BaseModel):
    # existing fields remain unchanged
    region: str | None = Field(default=None, max_length=32)

class ProviderPatch(BaseModel):
    # existing fields remain unchanged
    region: str | None = Field(default=None, max_length=32)
```

```python
# backend/aws_bedrock_provider.py
SUPPORTED_BEDROCK_REGIONS = frozenset({
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "eu-central-1",
    "eu-west-1",
    "us-east-1",
    "us-east-2",
    "us-west-2",
})

class AwsBedrockCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_key_id: str = Field(min_length=8, max_length=128, repr=False)
    secret_access_key: str = Field(min_length=16, max_length=256, repr=False)
    session_token: str | None = Field(default=None, max_length=4096, repr=False)

    def to_secret_value(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_secret_value(cls, value: str) -> "AwsBedrockCredential":
        return cls.model_validate_json(value)

def bedrock_control_endpoint(region: str) -> str:
    normalized = str(region or "").strip().lower()
    if normalized not in SUPPORTED_BEDROCK_REGIONS:
        raise ValueError("bedrock_region_unsupported")
    return f"https://bedrock.{normalized}.amazonaws.com"
```

Add `boto3==1.43.51` to `backend/requirements.txt`.

In `backend/sql/finops_schema.sql`, conditionally add `region NVARCHAR(32) NULL`; then conditionally replace `CK_finops_model_provider_type` so its allowed values are exactly `deepseek` and `aws_bedrock`. Do not delete or rewrite provider rows.

Store a Bedrock credential bundle using `AwsBedrockCredential.to_secret_value()`. Keep the current Key Vault secret name and one-secret-per-provider pattern; do not create separate SQL columns for credentials.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_aws_bedrock_provider.py tests/test_model_providers.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py tests/test_finops_sql_migration.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/aws_bedrock_provider.py backend/model_providers.py backend/model_provider_secrets.py backend/model_provider_repository.py backend/sql/finops_schema.sql backend/requirements.txt tests/test_aws_bedrock_provider.py tests/test_model_providers.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py tests/test_finops_sql_migration.py
git commit -m "feat(providers): add Bedrock configuration domain"
```

### Task 2: Read-Only Bedrock Connection Test

**Files:**
- Modify: `backend/aws_bedrock_provider.py`
- Modify: `backend/model_provider_service.py:18-240`
- Test: `tests/test_aws_bedrock_provider.py`
- Test: `tests/test_model_provider_api.py`

**Interfaces:**
- Consumes: `AwsBedrockCredential.from_secret_value()` and `ModelProviderRecord.region`.
- Produces: `AwsBedrockControlPlane.list_models(region, credential) -> list[ProviderModel]`.

- [ ] **Step 1: Write failing adapter tests**

```python
class _BedrockClient:
    def list_foundation_models(self):
        return {
            "modelSummaries": [{
                "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
                "modelName": "Claude Sonnet 4",
                "providerName": "Anthropic",
                "inputModalities": ["TEXT"],
                "outputModalities": ["TEXT"],
                "responseStreamingSupported": True,
            }]
        }

class _FailingBedrockClient:
    def __init__(self, error):
        self.error = error

    def list_foundation_models(self):
        raise self.error

def credential() -> AwsBedrockCredential:
    return AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker-value",
    )

def test_bedrock_list_models_returns_non_routable_discovery() -> None:
    adapter = Boto3BedrockControlPlane(client_factory=lambda **_: _BedrockClient())
    models = adapter.list_models("us-east-1", credential())
    assert models[0].model_id == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert models[0].support_state == "unsupported"
    assert models[0].price_key is None

@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("UnrecognizedClientException", "authentication_failed"),
        ("AccessDeniedException", "access_denied"),
        ("ThrottlingException", "throttled"),
        ("InternalServerException", "provider_unavailable"),
    ],
)
def test_bedrock_errors_are_mapped_without_raw_body(code, category) -> None:
    error = ClientError(
        {"Error": {"Code": code, "Message": "secret-marker raw provider body"}},
        "ListFoundationModels",
    )
    adapter = Boto3BedrockControlPlane(
        client_factory=lambda **_: _FailingBedrockClient(error),
    )
    with pytest.raises(BedrockConnectionFailure) as exc:
        adapter.list_models("us-east-1", credential())
    assert exc.value.category == category
    assert "secret-marker" not in str(exc.value)
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_aws_bedrock_provider.py -q
```

Expected: FAIL because the control-plane adapter and safe failure type are missing.

- [ ] **Step 3: Implement the control-plane adapter and service dispatch**

```python
class BedrockConnectionFailure(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)

def _safe_bedrock_category(exc: Exception) -> str:
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)):
        return "timeout"
    code = ""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "")
    return {
        "UnrecognizedClientException": "authentication_failed",
        "InvalidSignatureException": "authentication_failed",
        "AccessDeniedException": "access_denied",
        "ValidationException": "configuration_conflict",
        "ThrottlingException": "throttled",
        "InternalServerException": "provider_unavailable",
        "ServiceUnavailableException": "provider_unavailable",
    }.get(code, "provider_unavailable")

def _bedrock_capabilities(item: Mapping[str, object]) -> list[str]:
    values = [
        str(value).strip().lower()
        for value in [
            *(item.get("inputModalities") or []),
            *(item.get("outputModalities") or []),
        ]
        if str(value).strip()
    ]
    if item.get("responseStreamingSupported") is True:
        values.append("streaming")
    return list(dict.fromkeys(values))

class AwsBedrockControlPlane(Protocol):
    def list_models(
        self,
        region: str,
        credential: AwsBedrockCredential,
    ) -> list[ProviderModel]:
        raise NotImplementedError

class Boto3BedrockControlPlane:
    def __init__(self, client_factory=None) -> None:
        self._client_factory = client_factory or boto3.client

    def list_models(self, region, credential):
        try:
            client = self._client_factory(
                "bedrock",
                region_name=region,
                aws_access_key_id=credential.access_key_id,
                aws_secret_access_key=credential.secret_access_key,
                aws_session_token=credential.session_token,
                config=Config(
                    connect_timeout=5,
                    read_timeout=10,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
            summaries = client.list_foundation_models().get("modelSummaries") or []
        except (ClientError, BotoCoreError) as exc:
            raise BedrockConnectionFailure(_safe_bedrock_category(exc)) from None
        return [
            ProviderModel(
                model_id=str(item["modelId"]),
                display_name=str(item.get("modelName") or item["modelId"]),
                capabilities=_bedrock_capabilities(item),
                support_state="unsupported",
                price_key=None,
            )
            for item in summaries[:64]
            if isinstance(item, dict) and str(item.get("modelId") or "").strip()
        ]
```

Change `ModelProviderService` to accept a `bedrock_control_plane` dependency. Rename its provider-neutral secret arguments from `api_key` to `secret_value`, add `region: str | None` to `create`, and keep DeepSeek callers serializing their API key as the same plain string:

```python
def create(
    self,
    *,
    tenant_ref: str,
    actor_ref: str,
    provider_type: str,
    display_name: str,
    base_url: str,
    secret_value: str,
    region: str | None = None,
    provider_id: str | None = None,
) -> dict[str, object]:
    raise NotImplementedError

def rotate(
    self,
    *,
    tenant_ref: str,
    provider_id: str,
    secret_value: str,
    base_revision: int,
    actor_ref: str,
) -> dict[str, object]:
    raise NotImplementedError
```

Implement these signatures by applying the current create/rotate flow with `secret_value` passed to `put/rotate`. Dispatch `_test_record`:

```python
if value.provider_type == "aws_bedrock":
    credential = AwsBedrockCredential.from_secret_value(secret_value)
    models = self._bedrock.list_models(value.region or "", credential)
    governance_state = "unmanaged"
elif value.provider_type == "deepseek":
    DeepSeekProvider(transport=self._transport).invoke(
        ProviderInvocation(
            request_ref=f"test_{uuid.uuid4().hex[:24]}",
            correlation_ref=f"test_{uuid.uuid4().hex[:24]}",
            workspace_id="provider-connection-test",
            agent_id=None,
            execution_kind="connection_test",
            model_id="deepseek-v4-flash",
            messages=[ProviderMessage(role="user", content="Reply with OK.")],
            max_tokens=1,
        ),
        api_key=secret_value,
        base_url=value.base_url,
    )
    models = _deepseek_models()
    governance_state = value.governance_state
else:
    raise BedrockConnectionFailure("configuration_conflict")
```

Map only the categories listed in the approved specification. Set `connected` only after a successful list call; set `invalid` for authentication/access/region failures and `degraded` for timeout, throttle, or provider availability failures.

- [ ] **Step 4: Run adapter and service tests**

Run:

```powershell
python -m pytest tests/test_aws_bedrock_provider.py tests/test_model_provider_api.py -q
```

Expected: PASS, with no raw botocore exception text in public assertions.

- [ ] **Step 5: Commit**

```powershell
git add backend/aws_bedrock_provider.py backend/model_provider_service.py tests/test_aws_bedrock_provider.py tests/test_model_provider_api.py
git commit -m "feat(providers): test Bedrock connections safely"
```

### Task 3: Bedrock API Contract, Audit, and Feature Gate

**Files:**
- Modify: `backend/model_provider_router.py:39-220`
- Modify: `backend/app.py:40-175`
- Modify: `backend/.env.example`
- Test: `tests/test_model_provider_api.py`
- Test: `tests/test_model_provider_audit.py`

**Interfaces:**
- Consumes: the provider service dispatch from Task 2.
- Produces: existing `/api/model-providers` endpoints with a typed `aws_bedrock` request variant.

- [ ] **Step 1: Write failing API and audit tests**

```python
def test_owner_creates_masked_bedrock_provider(monkeypatch) -> None:
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")
    response = client.post(
        "/api/model-providers",
        headers=owner_headers,
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-value",
        },
    )
    assert response.status_code == 201
    assert response.json()["region"] == "ap-southeast-1"
    assert "access_key" not in response.text.lower()
    assert "secret-marker" not in response.text
    assert "secret-marker" not in str(audits)

def test_bedrock_create_is_hidden_when_specific_flag_is_off(monkeypatch) -> None:
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "0")
    response = client.post(
        "/api/model-providers",
        headers=owner_headers,
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-value",
        },
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_model_provider_api.py tests/test_model_provider_audit.py -q
```

Expected: FAIL because the request model accepts only DeepSeek.

- [ ] **Step 3: Implement a discriminated create body**

```python
class DeepSeekProviderCreate(BaseModel):
    provider_type: Literal["deepseek"]
    display_name: str = Field(min_length=1, max_length=120)
    base_url: Literal["https://api.deepseek.com"]
    api_key: str = Field(min_length=8, max_length=512, exclude=True, repr=False)

class BedrockProviderCreate(BaseModel):
    provider_type: Literal["aws_bedrock"]
    display_name: str = Field(min_length=1, max_length=120)
    region: str
    access_key_id: str = Field(min_length=8, max_length=128, exclude=True, repr=False)
    secret_access_key: str = Field(min_length=16, max_length=256, exclude=True, repr=False)
    session_token: str | None = Field(default=None, max_length=4096, exclude=True, repr=False)

ProviderCreateBody = Annotated[
    DeepSeekProviderCreate | BedrockProviderCreate,
    Field(discriminator="provider_type"),
]

class BedrockProviderRotate(BaseModel):
    provider_type: Literal["aws_bedrock"]
    access_key_id: str = Field(min_length=8, max_length=128, exclude=True, repr=False)
    secret_access_key: str = Field(min_length=16, max_length=256, exclude=True, repr=False)
    session_token: str | None = Field(default=None, max_length=4096, exclude=True, repr=False)
    base_revision: int = Field(ge=1)
```

For Bedrock, build `base_url` using `bedrock_control_endpoint(region)`, serialize the credential bundle, and pass it as `secret_value` to the provider-neutral service methods. Bedrock rotation uses `BedrockProviderRotate`; DeepSeek rotation retains its `api_key` body. Write only `provider_id`, `provider_type`, `display_name`, `region`, and safe reason codes to audit metadata.

Retain the global provider gate. Add the specific Bedrock gate only for Bedrock create/test/rotate; existing DeepSeek behavior must not change.

- [ ] **Step 4: Run the provider API suite**

Run:

```powershell
python -m pytest tests/test_model_provider_api.py tests/test_model_provider_audit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/model_provider_router.py backend/app.py backend/.env.example tests/test_model_provider_api.py tests/test_model_provider_audit.py
git commit -m "feat(api): expose governed Bedrock configuration"
```

### Task 4: Bedrock Provider UI

**Files:**
- Create: `web/src/AwsBedrockConnectionForm.jsx`
- Modify: `web/src/ProviderConnectionsPage.jsx`
- Modify: `web/src/providerConnectionsViewModel.js`
- Modify: `web/src/api.js:587-625`
- Modify: `web/src/styles.css:5124-5265`
- Modify: `web/src/providerConnectionsViewModel.test.mjs`
- Modify: `web/src/finopsApi.test.mjs`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-pricing-routing-remediation.spec.mjs`

**Interfaces:**
- Consumes: `POST /api/model-providers` with `provider_type=aws_bedrock`.
- Produces: a write-only Bedrock form and safe provider card; no routing controls.

- [ ] **Step 1: Write failing Node tests**

```javascript
test("Bedrock discovery is connected but never assignable", () => {
  const view = providerConnectionsViewModel({
    items: [{
      provider_id: "provider_bedrock",
      provider_type: "aws_bedrock",
      display_name: "AWS Bedrock",
      region: "ap-southeast-1",
      base_url: "https://bedrock.ap-southeast-1.amazonaws.com",
      connection_state: "connected",
      governance_state: "unmanaged",
      secret_status: "stored",
      revision: 1,
      available_models: [{
        model_id: "anthropic.claude-sonnet-4-20250514-v1:0",
        display_name: "Claude Sonnet 4",
        capabilities: ["text", "streaming"],
        support_state: "unsupported",
        price_key: null,
      }],
    }],
  });
  assert.equal(view.items[0].providerLabel, "AWS Bedrock");
  assert.equal(view.items[0].region, "ap-southeast-1");
  assert.equal(view.items[0].canAssign, false);
});
```

Add this API helper test:

```javascript
test("Bedrock create sends credentials once", async () => {
  let captured;
  globalThis.fetch = async (_url, options) => {
    captured = JSON.parse(options.body);
    return new Response(JSON.stringify({ provider_id: "provider_bedrock" }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  };
  await createModelProvider({
    provider_type: "aws_bedrock",
    display_name: "AWS Bedrock",
    region: "ap-southeast-1",
    access_key_id: "AKIAEXAMPLE",
    secret_access_key: "secret-marker-value",
    session_token: null,
  });
  assert.equal(captured.region, "ap-southeast-1");
  assert.equal(captured.secret_access_key, "secret-marker-value");
});
```

- [ ] **Step 2: Run Node tests and verify failure**

Run:

```powershell
Set-Location web
node --test src/providerConnectionsViewModel.test.mjs src/finopsApi.test.mjs
```

Expected: FAIL because Bedrock labels, region, and creation UI are absent.

- [ ] **Step 3: Implement the isolated form**

`AwsBedrockConnectionForm` owns only ephemeral credential state:

```jsx
export function AwsBedrockConnectionForm({ busy, onSubmit }) {
  const [draft, setDraft] = useState({
    displayName: "AWS Bedrock",
    region: "ap-southeast-1",
    accessKeyId: "",
    secretAccessKey: "",
    sessionToken: "",
  });

  async function submit(event) {
    event.preventDefault();
    await onSubmit({
      provider_type: "aws_bedrock",
      display_name: draft.displayName.trim() || "AWS Bedrock",
      region: draft.region,
      access_key_id: draft.accessKeyId.trim(),
      secret_access_key: draft.secretAccessKey,
      session_token: draft.sessionToken || null,
    });
    setDraft((value) => ({
      ...value,
      accessKeyId: "",
      secretAccessKey: "",
      sessionToken: "",
    }));
  }
  return (
    <form className="provider-create-card" onSubmit={submit}>
      <label>显示名称<input value={draft.displayName} onChange={(event) => setDraft((value) => ({ ...value, displayName: event.target.value }))} /></label>
      <label>区域<select value={draft.region} onChange={(event) => setDraft((value) => ({ ...value, region: event.target.value }))}>
        <option value="ap-southeast-1">亚太地区（新加坡）</option>
        <option value="ap-northeast-1">亚太地区（东京）</option>
        <option value="us-east-1">美国东部（弗吉尼亚北部）</option>
        <option value="us-west-2">美国西部（俄勒冈）</option>
      </select></label>
      <label>Access Key ID<input type="password" autoComplete="new-password" value={draft.accessKeyId} onChange={(event) => setDraft((value) => ({ ...value, accessKeyId: event.target.value }))} /></label>
      <label>Secret Access Key<input type="password" autoComplete="new-password" value={draft.secretAccessKey} onChange={(event) => setDraft((value) => ({ ...value, secretAccessKey: event.target.value }))} /></label>
      <label>Session Token（可选）<input type="password" autoComplete="new-password" value={draft.sessionToken} onChange={(event) => setDraft((value) => ({ ...value, sessionToken: event.target.value }))} /></label>
      <button type="submit" disabled={busy}>保存并测试连接</button>
    </form>
  );
}
```

Render it below the DeepSeek form. Bedrock provider cards show region, “配置测试可用”, and “尚未进入 Agent 路由”; do not show price or governance success. Use `AWS` as the text mark, not a rough custom image icon. A Bedrock card uses the same five credential fields for rotation and sends `provider_type`, the credential bundle, and `base_revision`; it must not reuse the DeepSeek single-Key input.

- [ ] **Step 4: Run Node, build, and Playwright acceptance**

Run:

```powershell
Set-Location web
node --test
npm run build
npx playwright test tests/finops-pricing-routing-remediation.spec.mjs
```

Expected: all Node tests PASS, Vite build succeeds, and the Playwright test shows:

- Bedrock form on desktop and mobile;
- credential fields cleared after save;
- provider persists after reload;
- no Bedrock option in Agent model assignment;
- safe 409 and connection error presentation.

- [ ] **Step 5: Commit**

```powershell
git add web/src/AwsBedrockConnectionForm.jsx web/src/ProviderConnectionsPage.jsx web/src/providerConnectionsViewModel.js web/src/api.js web/src/styles.css web/src/providerConnectionsViewModel.test.mjs web/src/finopsApi.test.mjs web/tests/finopsMockApi.mjs web/tests/finops-pricing-routing-remediation.spec.mjs
git commit -m "feat(web): configure and test Bedrock connections"
```

### Task 5: Full Regression and Candidate Evidence

**Files:**
- Modify: `README.md`
- Create: `docs/validation/2026-07-28-bedrock-connector-candidate.md`

**Interfaces:**
- Consumes: all Bedrock work from Tasks 1-4.
- Produces: reproducible evidence without enabling production routing.

- [ ] **Step 1: Document configuration and explicit non-goals**

Record:

```text
DF_PROVIDER_CONNECTORS_ENABLED=1
DF_AWS_BEDROCK_CONNECTOR_ENABLED=1
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0
DF_FINOPS_ACTIONS_ENABLED=0
```

State that AWS credentials are entered only in the authenticated write-only UI and are never copied into the runbook.

- [ ] **Step 2: Run the full automated gate**

Run:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
```

Expected: Python, Node, Vite, and Playwright all pass; `git diff --check` is clean.

- [ ] **Step 3: Perform zero-traffic candidate acceptance**

Against a candidate revision with no production traffic:

1. Save one Bedrock connection using the authenticated settings UI.
2. Confirm Key Vault has a new secret version without printing its value.
3. Run one successful `ListFoundationModels` test.
4. Verify bad credentials, denied permission, and unsupported region produce only safe categories.
5. Refresh and sign in from another device/session; confirm configuration remains while credentials do not return.
6. Confirm Agent model routing contains no Bedrock model.
7. Confirm critical backend log signals and secret-like values are zero.

Record timestamps, revision names, image digests, HTTP statuses, screenshot paths, and the previous healthy backend/web revision names as rollback targets in the candidate document. Do not record subscription IDs, access keys, account IDs, ARNs, raw AWS request IDs, or response bodies.

- [ ] **Step 4: Commit evidence**

```powershell
git add README.md docs/validation/2026-07-28-bedrock-connector-candidate.md
git commit -m "docs: record Bedrock connector acceptance"
```

- [ ] **Step 5: Stop at the release gate**

Do not switch traffic or enable runtime routing. Report the candidate evidence and request explicit user approval for any production traffic change.

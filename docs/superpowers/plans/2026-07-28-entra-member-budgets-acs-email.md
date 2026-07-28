# Entra Member Budgets and ACS Email Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let organization administrators assign UTC monthly USD budgets to Entra-attributed members, see estimated spend and pricing coverage, and send deduplicated threshold reminders to one active administrator through Azure Communication Services Email.

**Architecture:** Add a dedicated member-budget domain over the existing FinOps request ledger, with SQL as the source of truth and a focused router separate from the large FinOps router. A member-directory adapter resolves safe Entra display information, an ACS adapter sends through managed identity, and a durable evaluator creates idempotent alert rows after rollup refreshes and during a 15-minute scheduled sweep.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQL Server, Azure Identity 1.19.0, azure-communication-email 1.1.0, React, Node test runner, Vite, Playwright, Azure Container Apps/Jobs, ACS Email.

## Global Constraints

- Budgets use UTC calendar months, USD, and request-level `estimated_cost`; they are not Azure or AWS bills.
- Unpriced/unavailable requests are not counted as zero; every response and email includes pricing coverage.
- Member identity is `tenant_ref + actor_ref`, derived from trusted Easy Auth/Entra data. Display name and email are presentation fields, not authorization keys.
- Only Owner/Admin may read or mutate member budgets, mail settings, alerts, or member-level cost details.
- The only automatic recipient is one active Owner/Admin in the current tenant; members never receive automatic email in this version.
- Email uses ACS Email, Azure Managed Domain, and the backend system-assigned managed identity. Do not use Graph mail, Exchange, SMTP credentials, or a stored ACS connection string.
- Templates are plain text and accept only the eight approved variables; no HTML, script, remote images, or attachments.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`; over-budget events never block users, change models, or alter APIM.
- Keep automated sends off until separate acceptance: `DF_FINOPS_EMAIL_ALERTS_ENABLED=0`.
- All writes require durable audit persistence and `base_revision`; a stale write returns `409` with no partial mutation.

## File Structure

- Create `backend/finops/member_budgets.py`: budget, notification-setting, alert, progress, and summary types plus validation.
- Create `backend/finops/member_budget_repository.py`: repository protocols and in-memory test implementation.
- Create `backend/finops/sql_member_budgets.py`: SQL persistence, optimistic concurrency, alert claiming, and cost aggregation.
- Create `backend/finops/member_directory.py`: safe Entra/member resolution and administrator-recipient validation.
- Modify `backend/control_plane.py`: expose a bounded internal member-identity loader without creating a public raw-identity API.
- Create `backend/finops/acs_email.py`: managed-identity ACS client, template rendering, test send, and safe errors.
- Create `backend/finops/member_budget_service.py`: application service for CRUD, progress, mail settings, and alerts.
- Create `backend/finops/member_budget_evaluator.py`: threshold coalescing, dedupe, retries, and delivery transitions.
- Create `backend/finops/member_budget_router.py`: typed `/api/finops/member-budgets`, notification settings, test email, and alert APIs.
- Create `backend/finops/member_budget_refresh.py`: scheduled command that evaluates all due tenants.
- Modify `backend/finops/rollup_refresh.py`, `backend/app.py`, `backend/sql/finops_schema.sql`, `backend/requirements.txt`, and `backend/.env.example`.
- Create Python tests `tests/test_finops_member_budgets.py`, `tests/test_finops_member_budget_sql.py`, `tests/test_finops_member_directory.py`, `tests/test_finops_member_budget_api.py`, `tests/test_finops_acs_email.py`, and `tests/test_finops_member_budget_evaluator.py`.
- Create `web/src/MemberBudgetSettingsPage.jsx` and `web/src/memberBudgetViewModel.js`.
- Modify `web/src/components.jsx`, `web/src/api.js`, and `web/src/styles.css`.
- Create `web/src/memberBudgetViewModel.test.mjs`.
- Modify `web/src/finopsApi.test.mjs`, `web/tests/finopsMockApi.mjs`, and `web/tests/finops-portal-acceptance.spec.mjs`.
- Add deployment/acceptance documentation under `docs/validation/`.

---

### Task 1: Member Budget Domain and SQL Source of Truth

**Files:**
- Create: `backend/finops/member_budgets.py`
- Create: `backend/finops/member_budget_repository.py`
- Create: `backend/finops/sql_member_budgets.py`
- Modify: `backend/sql/finops_schema.sql`
- Test: `tests/test_finops_member_budgets.py`
- Test: `tests/test_finops_member_budget_sql.py`
- Test: `tests/test_finops_sql_migration.py`

**Interfaces:**
- Produces: `MemberBudget`, `NotificationSetting`, `BudgetAlert`, `MemberCostSummary`, and `MemberBudgetRepository`.
- Consumes: existing SQL connection factory and `FinOpsPersistenceError`.

- [ ] **Step 1: Write failing domain validation tests**

```python
def test_member_budget_requires_sorted_unique_thresholds() -> None:
    value = MemberBudgetDraft(
        member_ref="actor_safe",
        amount_usd=200,
        thresholds_pct=[80, 95, 100],
        enabled=True,
    )
    assert value.thresholds_pct == (80, 95, 100)
    with pytest.raises(ValueError, match="thresholds"):
        MemberBudgetDraft(
            member_ref="actor_safe",
            amount_usd=200,
            thresholds_pct=[95, 80, 95],
            enabled=True,
        )

def test_member_cost_summary_preserves_unpriced_coverage() -> None:
    value = MemberCostSummary(
        actor_ref="actor_safe",
        estimated_spend_usd=190,
        priced_requests=19,
        total_requests=20,
    )
    assert value.pricing_coverage_pct == 95
    assert value.data_status == "partial"
```

- [ ] **Step 2: Write failing SQL contract tests**

Assert that:

- each table contains `tenant_ref`;
- `member_budget` has one active row per `tenant_ref + actor_ref`;
- `budget_alert` has a unique key on `tenant_ref + budget_id + period_key + threshold_pct`;
- alert delivery states are exactly `pending/sending/sent/failed/suppressed`;
- request events have an index beginning with `tenant_ref, actor_ref, occurred_at`;
- no table stores email bodies, ACS message IDs, Entra object IDs, or credentials.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_finops_member_budgets.py tests/test_finops_member_budget_sql.py tests/test_finops_sql_migration.py -q
```

Expected: FAIL because the domain, repositories, and tables do not exist.

- [ ] **Step 4: Implement the domain**

Use these exact core types:

```python
class MemberBudgetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_ref: str = Field(min_length=8, max_length=128)
    amount_usd: Decimal = Field(gt=0, max_digits=19, decimal_places=8)
    thresholds_pct: tuple[int, ...] = (80, 95, 100)
    enabled: bool = True

    @field_validator("thresholds_pct")
    @classmethod
    def validate_thresholds(cls, value):
        normalized = tuple(int(item) for item in value)
        if (
            not normalized
            or normalized != tuple(sorted(set(normalized)))
            or any(item < 1 or item > 100 for item in normalized)
        ):
            raise ValueError("thresholds must be unique ascending integers from 1 to 100")
        return normalized

class MemberBudget(MemberBudgetDraft):
    budget_id: str
    period_type: Literal["calendar_month_utc"] = "calendar_month_utc"
    revision: int = Field(ge=1)
    created_by_ref: str
    updated_by_ref: str
    created_at: datetime
    updated_at: datetime

class MemberCostSummary(BaseModel):
    actor_ref: str
    estimated_spend_usd: Decimal | None
    priced_requests: int = Field(ge=0)
    total_requests: int = Field(ge=0)
    pricing_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    primary_model: str | None = None
    data_status: Literal["complete", "partial", "unavailable"]

class BudgetAlert(BaseModel):
    alert_id: str
    tenant_ref: str = Field(exclude=True, repr=False)
    budget_id: str
    actor_ref: str
    period_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    threshold_pct: int = Field(ge=1, le=100)
    budget_amount_usd: Decimal
    estimated_spend_usd: Decimal
    pricing_coverage_pct: float | None
    budget_revision: int = Field(ge=1)
    notification_revision: int = Field(ge=1)
    delivery_state: Literal["pending", "sending", "sent", "failed", "suppressed"]
    safe_error_category: str | None = None
    attempt_count: int = Field(default=0, ge=0, le=3)
    triggered_at: datetime
    sent_at: datetime | None = None
    updated_at: datetime

class NotificationSetting(BaseModel):
    recipient_actor_ref: str = Field(min_length=8, max_length=128)
    recipient_email: str = Field(min_length=3, max_length=320, repr=False)
    sender_display_name: str = Field(min_length=1, max_length=120)
    subject_template: str = Field(min_length=1, max_length=200)
    body_template: str = Field(min_length=1, max_length=4000)
    enabled: bool
    revision: int = Field(ge=1)
    created_by_ref: str
    updated_by_ref: str
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 5: Add SQL and repositories**

```sql
CREATE TABLE df_finops.member_budget (
    tenant_ref NVARCHAR(128) NOT NULL,
    budget_id NVARCHAR(64) NOT NULL,
    actor_ref NVARCHAR(128) NOT NULL,
    period_type NVARCHAR(32) NOT NULL,
    amount_usd DECIMAL(19,8) NOT NULL,
    thresholds_json NVARCHAR(256) NOT NULL,
    enabled BIT NOT NULL,
    revision INT NOT NULL,
    created_by_ref NVARCHAR(128) NOT NULL,
    updated_by_ref NVARCHAR(128) NOT NULL,
    created_at DATETIME2(7) NOT NULL,
    updated_at DATETIME2(7) NOT NULL,
    CONSTRAINT PK_finops_member_budget PRIMARY KEY (tenant_ref, budget_id),
    CONSTRAINT CK_finops_member_budget_period CHECK (period_type = N'calendar_month_utc'),
    CONSTRAINT CK_finops_member_budget_amount CHECK (amount_usd > 0),
    CONSTRAINT CK_finops_member_budget_thresholds CHECK (ISJSON(thresholds_json) = 1),
    CONSTRAINT CK_finops_member_budget_revision CHECK (revision >= 1)
);
CREATE UNIQUE INDEX UQ_finops_member_budget_active
ON df_finops.member_budget (tenant_ref, actor_ref)
WHERE enabled = 1;

CREATE TABLE df_finops.notification_setting (
    tenant_ref NVARCHAR(128) NOT NULL,
    recipient_actor_ref NVARCHAR(128) NOT NULL,
    recipient_email NVARCHAR(320) NOT NULL,
    sender_display_name NVARCHAR(120) NOT NULL,
    subject_template NVARCHAR(200) NOT NULL,
    body_template NVARCHAR(4000) NOT NULL,
    enabled BIT NOT NULL,
    revision INT NOT NULL,
    created_by_ref NVARCHAR(128) NOT NULL,
    updated_by_ref NVARCHAR(128) NOT NULL,
    created_at DATETIME2(7) NOT NULL,
    updated_at DATETIME2(7) NOT NULL,
    CONSTRAINT PK_finops_notification_setting PRIMARY KEY (tenant_ref),
    CONSTRAINT CK_finops_notification_revision CHECK (revision >= 1)
);

CREATE TABLE df_finops.budget_alert (
    tenant_ref NVARCHAR(128) NOT NULL,
    alert_id NVARCHAR(64) NOT NULL,
    budget_id NVARCHAR(64) NOT NULL,
    actor_ref NVARCHAR(128) NOT NULL,
    period_key CHAR(7) NOT NULL,
    threshold_pct INT NOT NULL,
    budget_amount_usd DECIMAL(19,8) NOT NULL,
    estimated_spend_usd DECIMAL(19,8) NOT NULL,
    pricing_coverage_pct DECIMAL(8,4) NULL,
    budget_revision INT NOT NULL,
    notification_revision INT NOT NULL,
    delivery_state NVARCHAR(16) NOT NULL,
    safe_error_category NVARCHAR(64) NULL,
    attempt_count INT NOT NULL,
    triggered_at DATETIME2(7) NOT NULL,
    sent_at DATETIME2(7) NULL,
    updated_at DATETIME2(7) NOT NULL,
    CONSTRAINT PK_finops_budget_alert PRIMARY KEY (tenant_ref, alert_id),
    CONSTRAINT UQ_finops_budget_alert_threshold UNIQUE (
        tenant_ref, budget_id, period_key, threshold_pct
    ),
    CONSTRAINT CK_finops_budget_alert_state CHECK (
        delivery_state IN (N'pending', N'sending', N'sent', N'failed', N'suppressed')
    ),
    CONSTRAINT CK_finops_budget_alert_attempt CHECK (attempt_count BETWEEN 0 AND 3)
);
```

Wrap each table and index in the repository's existing `IF OBJECT_ID(...) IS NULL` / `IF NOT EXISTS` guards so rerunning the migration is safe. Store thresholds as JSON with `ISJSON` validation. Use `MERGE ... WITH (HOLDLOCK)` only after checking `base_revision`; conflicting updates raise `MemberBudgetConflictError`. Use a unique constraint to claim one alert per threshold and period. Add:

```sql
CREATE INDEX IX_finops_request_actor_window
ON df_finops.request_event (tenant_ref, actor_ref, occurred_at)
INCLUDE (cost_amount, evidence_state);
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python -m pytest tests/test_finops_member_budgets.py tests/test_finops_member_budget_sql.py tests/test_finops_sql_migration.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/finops/member_budgets.py backend/finops/member_budget_repository.py backend/finops/sql_member_budgets.py backend/sql/finops_schema.sql tests/test_finops_member_budgets.py tests/test_finops_member_budget_sql.py tests/test_finops_sql_migration.py
git commit -m "feat(finops): persist Entra member budgets"
```

### Task 2: Entra Member Directory and Cost Attribution

**Files:**
- Create: `backend/finops/member_directory.py`
- Modify: `backend/control_plane.py:1470-1585`
- Modify: `backend/finops/sql_member_budgets.py`
- Test: `tests/test_finops_member_directory.py`
- Test: `tests/test_finops_member_budget_sql.py`
- Test: `tests/test_ui_truthfulness_contract.py`

**Interfaces:**
- Produces: `MemberDirectory.list_members(tenant_ref, workspace_ids)` and `MemberCostReader.summarize_month(tenant_ref, month_start, month_end)`.
- Consumes: trusted workspace member metadata, `opaque_ref()`, `DF_FINOPS_HMAC_SECRET`, and `df_finops.request_event`.

- [ ] **Step 1: Write failing identity and cost tests**

```python
def test_directory_deduplicates_member_by_tenant_actor_and_keeps_friendly_name() -> None:
    directory = MemberDirectory(
        identity_loader=lambda _workspace: [
            {
                "tenant_id": "tenant-a",
                "actor_id": "oid-a",
                "name": "Finance Admin",
                "email": "finance.admin@company.com",
                "role": "admin",
                "status": "active",
            }
        ],
        hmac_secret="test-secret",
    )
    items = directory.list_members("tenant-safe", ("ws-a", "ws-b"))
    assert len(items) == 1
    assert items[0].display_name == "Finance Admin"
    assert items[0].email == "finance.admin@company.com"
    assert "oid-a" not in items[0].model_dump_json()

def test_cost_summary_does_not_treat_unpriced_as_zero() -> None:
    summary = repository.summarize_member_costs(
        tenant_ref="tenant-a",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
    )["actor-safe"]
    assert summary.estimated_spend_usd == Decimal("190")
    assert summary.priced_requests == 19
    assert summary.total_requests == 20
    assert summary.data_status == "partial"
    assert summary.primary_model == "gpt-5.6-terra"
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q
```

Expected: FAIL because member directory and actor cost queries are missing.

- [ ] **Step 3: Add a bounded internal identity loader**

Add a non-route helper to `backend/control_plane.py`:

```python
def workspace_finops_member_identities(workspace_id: str) -> list[dict[str, str]]:
    """Internal-only trusted identities; never return directly from an API."""
    members = _workspace_members_by_key(workspace_id)
    result = []
    for member in members.values():
        actor_id = str(member.get("actor_id") or "").strip()
        tenant_id = str(member.get("tenant_id") or "").strip()
        if not actor_id or not tenant_id or not is_trusted_tenant_identity(member):
            continue
        result.append({
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "name": _clean_text(member.get("user") or member.get("name")),
            "email": _member_email(member.get("email")) or "",
            "role": str(member.get("role") or "viewer").lower(),
            "status": str(member.get("status") or "active").lower(),
        })
    return result
```

The `MemberDirectory` converts the raw IDs to the same `actor_ref` formula used by FinOps normalization, merges workspace membership, retains workspace labels, resolves department labels with `FinOpsManagementService.workspace_department()`, and drops raw IDs before returning. Its public member result contains only `member_ref`, `display_name`, `email`, `role`, `identity_state`, `workspace_ids`, and `department_labels`.

If the identity disappears, return the persisted budget with `identity_state="inactive"` and no new automatic alerts. Historical cost remains.

- [ ] **Step 4: Add the actor cost query**

Use UTC month boundaries and aggregate `request_event`:

```sql
SELECT actor_ref,
       SUM(CASE WHEN cost_amount IS NOT NULL THEN cost_amount END) AS estimated_spend,
       SUM(CASE WHEN cost_amount IS NOT NULL THEN 1 ELSE 0 END) AS priced_requests,
       COUNT_BIG(*) AS total_requests
FROM df_finops.request_event
WHERE tenant_ref = ?
  AND actor_ref IS NOT NULL
  AND occurred_at >= ?
  AND occurred_at < ?
GROUP BY actor_ref;
```

Run a second bounded query using `ROW_NUMBER() OVER (PARTITION BY actor_ref ORDER BY COUNT_BIG(*) DESC, model_deployment)` to select one `primary_model` per actor. Return `estimated_spend_usd=None` when `priced_requests=0`; never coalesce missing cost to zero.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q
```

Expected: PASS.

```powershell
git add backend/finops/member_directory.py backend/control_plane.py backend/finops/sql_member_budgets.py tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py
git commit -m "feat(finops): attribute cost to Entra members"
```

### Task 3: Member Budget and Notification Settings APIs

**Files:**
- Create: `backend/finops/member_budget_service.py`
- Create: `backend/finops/member_budget_router.py`
- Modify: `backend/app.py:40-175`
- Modify: `backend/.env.example`
- Test: `tests/test_finops_member_budget_api.py`
- Test: `tests/test_actor_audit_usage.py`

**Interfaces:**
- Produces:
  - `GET/POST/PATCH /api/finops/member-budgets`
  - `POST /api/finops/member-budgets/{budget_id}/disable`
  - `GET/PUT /api/finops/notification-settings`
  - `GET /api/finops/budget-alerts`
- Consumes: Task 1 repositories and Task 2 directory/cost reader.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_owner_lists_friendly_member_budget_with_partial_coverage(client) -> None:
    response = client.get("/api/finops/member-budgets", headers=owner_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["member"]["display_name"] == "Finance Admin"
    assert item["progress"]["estimated_spend_usd"] == 190
    assert item["progress"]["pricing_coverage_pct"] == 95
    assert item["data_status"] == "partial"
    assert "oid-" not in response.text

def test_member_budget_rejects_member_role(client) -> None:
    assert client.get(
        "/api/finops/member-budgets",
        headers=viewer_headers,
    ).status_code == 403

def test_stale_member_budget_write_returns_409_without_mutation(client) -> None:
    response = client.patch(
        "/api/finops/member-budgets/budget-safe",
        headers=owner_headers,
        json={"base_revision": 1, "amount_usd": 300, "thresholds_pct": [80, 95, 100], "enabled": True},
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_finops_member_budget_api.py tests/test_actor_audit_usage.py -q
```

Expected: FAIL because the router and service do not exist.

- [ ] **Step 3: Implement the focused router**

Use:

```python
router = APIRouter(prefix="/api/finops", tags=["finops-member-budgets"])

def _member_budget_context(request: Request) -> tuple[str, str, tuple[str, ...]]:
    if not _enabled("DF_FINOPS_MEMBER_BUDGETS_ENABLED"):
        raise HTTPException(status_code=404, detail="Not found")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=403, detail="Trusted tenant identity required")
    roles = _authorized_workspace_roles(actor)
    if not roles or any(role not in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="Member budgets require admin or owner")
    return _tenant_ref(actor), _actor_ref(actor), tuple(sorted(roles))

@router.get("/member-budgets")
async def list_member_budgets(request: Request, cursor: str | None = None, limit: int = 50):
    tenant_ref, _actor_ref_value, workspace_ids = _member_budget_context(request)
    return get_member_budget_service().list_budgets(
        tenant_ref=tenant_ref,
        workspace_ids=workspace_ids,
        cursor=cursor,
        limit=min(max(limit, 1), 100),
    )
```

Every handler:

1. checks `DF_FINOPS_MEMBER_BUDGETS_ENABLED`;
2. derives trusted tenant/actor and authorized workspace roles;
3. requires all effective roles to be Owner/Admin;
4. writes a safe audit event before mutations;
5. passes only `member_ref`, amount, thresholds, enabled, and `base_revision`;
6. maps conflicts to `409`, missing rows to `404`, persistence failures to `503`;
7. returns `freshness`, `coverage`, `data_status`, `currency="USD"`, and cursor metadata.

Notification settings validation must resolve `recipient_actor_ref` to an active Owner/Admin. Do not accept a free-form recipient that cannot be resolved to the current tenant.

- [ ] **Step 4: Run API tests**

Run:

```powershell
python -m pytest tests/test_finops_member_budget_api.py tests/test_actor_audit_usage.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/finops/member_budget_service.py backend/finops/member_budget_router.py backend/app.py backend/.env.example tests/test_finops_member_budget_api.py tests/test_actor_audit_usage.py
git commit -m "feat(api): manage Entra member budgets"
```

### Task 4: ACS Email Managed-Identity Adapter and Test Email

**Files:**
- Create: `backend/finops/acs_email.py`
- Modify: `backend/finops/member_budget_service.py`
- Modify: `backend/finops/member_budget_router.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`
- Test: `tests/test_finops_acs_email.py`
- Test: `tests/test_finops_member_budget_api.py`

**Interfaces:**
- Produces: `AcsEmailSender.send(message, operation_id) -> EmailDeliveryResult`.
- Consumes: `DF_ACS_EMAIL_ENDPOINT`, `DF_ACS_EMAIL_SENDER_ADDRESS`, `DefaultAzureCredential`, and notification settings.

- [ ] **Step 1: Write failing ACS and template tests**

```python
def test_template_renderer_accepts_only_approved_variables() -> None:
    rendered = render_template(
        "{{member_name}} 已使用 {{usage_percent}}%",
        {"member_name": "Finance Admin", "usage_percent": "95"},
    )
    assert rendered == "Finance Admin 已使用 95%"
    with pytest.raises(ValueError, match="template_variable_not_allowed"):
        render_template("{{secret}}", {"secret": "marker"})

def test_acs_sender_uses_token_credential_and_plain_text() -> None:
    result = sender.send(
        EmailMessage(
            recipient="admin@company.com",
            sender_display_name="DataForge",
            subject="[测试] 成员预算提醒",
            plain_text="这是一封测试邮件。",
        ),
        operation_id="11111111-1111-5111-8111-111111111111",
    )
    assert result.state == "sent"
    assert captured["message"]["content"].get("html") is None
    assert captured["message"]["recipients"]["to"][0]["address"] == "admin@company.com"
```

Test missing endpoint, missing sender, credential denied, timeout, and service failure. Public categories are only `not_configured`, `permission_required`, `timeout`, and `service_unavailable`.

The renderer allowlist is exactly:

```python
ALLOWED_TEMPLATE_VARIABLES = frozenset({
    "member_name",
    "budget_amount",
    "estimated_spend",
    "usage_percent",
    "threshold_percent",
    "period_label",
    "pricing_coverage",
    "portal_url",
})
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_finops_acs_email.py tests/test_finops_member_budget_api.py -q
```

Expected: FAIL because ACS support is absent.

- [ ] **Step 3: Add the pinned SDK and adapter**

Add `azure-communication-email==1.1.0`.

```python
def acs_email_sender_from_environment() -> AcsEmailSender:
    endpoint = os.environ.get("DF_ACS_EMAIL_ENDPOINT", "").strip()
    sender = os.environ.get("DF_ACS_EMAIL_SENDER_ADDRESS", "").strip()
    if not endpoint or not sender:
        raise AcsEmailError("not_configured")
    client = EmailClient(endpoint, DefaultAzureCredential())
    return AcsEmailSender(client=client, sender_address=sender)
```

Use `EmailClient.begin_send(message, operation_id=operation_id)` and poll with a bounded timeout. Never log the message, recipient, operation result body, or SDK exception body.

- [ ] **Step 4: Add the test-email endpoint**

`POST /api/finops/notification-settings/test-email`:

- requires `DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1`;
- works while `DF_FINOPS_EMAIL_ALERTS_ENABLED=0`;
- sends only to the persisted active administrator recipient;
- prefixes the subject with `[测试]`;
- does not create a real budget alert;
- returns `{state, sent_at, safe_error_category}` only.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
python -m pytest tests/test_finops_acs_email.py tests/test_finops_member_budget_api.py -q
```

Expected: PASS.

```powershell
git add backend/finops/acs_email.py backend/finops/member_budget_service.py backend/finops/member_budget_router.py backend/requirements.txt backend/.env.example tests/test_finops_acs_email.py tests/test_finops_member_budget_api.py
git commit -m "feat(finops): test ACS email with managed identity"
```

### Task 5: Durable Threshold Evaluation and Delivery

**Files:**
- Create: `backend/finops/member_budget_evaluator.py`
- Create: `backend/finops/member_budget_refresh.py`
- Modify: `backend/finops/rollup_refresh.py`
- Modify: `backend/finops/sql_member_budgets.py`
- Test: `tests/test_finops_member_budget_evaluator.py`
- Test: `tests/test_finops_member_budget_sql.py`

**Interfaces:**
- Produces: `evaluate_tenant_budgets(tenant_ref, now) -> EvaluationSummary`.
- Consumes: member budgets, current-month cost summaries, active identities, notification settings, and `AcsEmailSender`.

- [ ] **Step 1: Write failing evaluator tests**

```python
def test_crossing_95_percent_creates_one_alert() -> None:
    summary = evaluator.evaluate_tenant("tenant-a", now=utc("2026-07-28T08:00:00Z"))
    assert summary.created == 1
    alert = repository.list_alerts("tenant-a")[0]
    assert alert.threshold_pct == 95
    assert alert.estimated_spend_usd == Decimal("190")

def test_one_evaluation_coalesces_multiple_thresholds() -> None:
    # $210 / $200 crosses 80, 95, and 100.
    evaluator.evaluate_tenant("tenant-a", now=utc("2026-07-28T08:00:00Z"))
    alerts = repository.list_alerts("tenant-a")
    assert [(a.threshold_pct, a.delivery_state) for a in alerts] == [
        (80, "suppressed"),
        (95, "suppressed"),
        (100, "sent"),
    ]

def test_repeat_evaluation_and_retry_do_not_duplicate_send() -> None:
    evaluator.evaluate_tenant("tenant-a", now=now)
    evaluator.evaluate_tenant("tenant-a", now=now)
    assert sender.operation_ids == [expected_operation_id]

@pytest.mark.parametrize(
    "case",
    [
        "inactive_identity",
        "disabled_budget",
        "automatic_alerts_disabled",
        "acs_not_configured",
        "third_retry_failed",
        "downward_reconciliation",
        "next_month_reset",
    ],
)
def test_evaluator_boundary_cases(case, scenario_factory) -> None:
    scenario = scenario_factory(case)
    result = scenario.evaluator.evaluate_tenant("tenant-a", now=scenario.now)
    assert result == scenario.expected_summary
    assert scenario.repository.list_alerts("tenant-a") == scenario.expected_alerts
    assert scenario.sender.operation_ids == scenario.expected_operation_ids
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m pytest tests/test_finops_member_budget_evaluator.py tests/test_finops_member_budget_sql.py -q
```

Expected: FAIL because evaluator and atomic claims are absent.

- [ ] **Step 3: Implement threshold selection and atomic claims**

```python
crossed = [
    threshold
    for threshold in budget.thresholds_pct
    if usage_pct >= threshold and threshold not in existing_thresholds
]
if crossed:
    highest = max(crossed)
    for threshold in crossed:
        repository.claim_alert(
            tenant_ref=tenant_ref,
            budget_id=budget.budget_id,
            actor_ref=budget.member_ref,
            period_key=period_key,
            threshold_pct=threshold,
            budget_amount_usd=budget.amount_usd,
            estimated_spend_usd=summary.estimated_spend_usd,
            pricing_coverage_pct=summary.pricing_coverage_pct,
            budget_revision=budget.revision,
            notification_revision=notification.revision,
            delivery_state="pending" if threshold == highest else "suppressed",
            triggered_at=now,
        )
```

Derive a stable UUIDv5 `operation_id` from the server-owned alert namespace and `alert_id`. Retries reuse the same operation ID. Transition `pending -> sending -> sent|failed`; increment attempts atomically; stop after three attempts.

When identity is inactive, do not create alerts. When the automatic alert flag is off, calculate summary counts but do not claim or send.

- [ ] **Step 4: Wire refresh and scheduled sweep**

After each successful tenant rollup refresh, call the evaluator in a failure-isolated block. `member_budget_refresh.py` lists tenants with enabled budgets and evaluates them; its process exit code is nonzero only when infrastructure prevents the sweep, not when one tenant email fails.

Configure the deployment runbook to execute:

```powershell
python -m backend.finops.member_budget_refresh
```

every 15 minutes as a Container Apps Job using the same immutable backend image and managed identity.

- [ ] **Step 5: Run evaluator tests and commit**

Run:

```powershell
python -m pytest tests/test_finops_member_budget_evaluator.py tests/test_finops_member_budget_sql.py tests/test_finops_rollups.py -q
```

Expected: PASS.

```powershell
git add backend/finops/member_budget_evaluator.py backend/finops/member_budget_refresh.py backend/finops/rollup_refresh.py backend/finops/sql_member_budgets.py tests/test_finops_member_budget_evaluator.py tests/test_finops_member_budget_sql.py tests/test_finops_rollups.py
git commit -m "feat(finops): deliver deduplicated budget alerts"
```

### Task 6: Approved Settings Entry and Dedicated Budget Page

**Files:**
- Create: `web/src/MemberBudgetSettingsPage.jsx`
- Create: `web/src/memberBudgetViewModel.js`
- Create: `web/src/memberBudgetViewModel.test.mjs`
- Modify: `web/src/components.jsx:3128-3822`
- Modify: `web/src/api.js`
- Modify: `web/src/styles.css:857-1020`
- Modify: `web/src/finopsApi.test.mjs`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes: member-budget, notification-setting, test-email, and alert endpoints.
- Produces: approved A-style `⚙ 配置` entry and C-style dedicated settings page.

- [ ] **Step 1: Write failing view-model tests**

```javascript
test("member budget view preserves partial and inactive states", () => {
  const view = memberBudgetViewModel({
    items: [{
      budget_id: "budget-safe",
      revision: 3,
      member: {
        member_ref: "actor-safe",
        display_name: "Finance Admin",
        email: "finance.admin@company.com",
        identity_state: "active",
      },
      amount_usd: 200,
      progress: {
        estimated_spend_usd: 190,
        usage_pct: 95,
        pricing_coverage_pct: 90,
      },
      alert_state: "sent",
      primary_model: "gpt-5.6-terra",
      data_status: "partial",
    }],
  });
  assert.equal(view.rows[0].spendLabel, "$190.00");
  assert.equal(view.rows[0].coverageLabel, "90% 已计价");
  assert.equal(view.rows[0].statusLabel, "95% · 已提醒");
});

test("missing spend stays unavailable instead of zero", () => {
  const view = memberBudgetViewModel({ items: [{
    member: { member_ref: "actor-safe", display_name: "IT Operator" },
    amount_usd: 200,
    progress: { estimated_spend_usd: null, usage_pct: null },
    data_status: "unavailable",
  }]});
  assert.equal(view.rows[0].spendLabel, "未计价");
});
```

- [ ] **Step 2: Run Node tests and verify failure**

Run:

```powershell
Set-Location web
node --test src/memberBudgetViewModel.test.mjs src/finopsApi.test.mjs
```

Expected: FAIL because the page, API helpers, and view model do not exist.

- [ ] **Step 3: Implement the settings-home entry**

Replace each settings-card “管理” text link with:

```jsx
<button type="button" className="settings-config-button" onClick={onConfigure}>
  <Settings2 size={13} />
  配置
</button>
```

Add the approved compact “成本预算与提醒” card after the existing settings configuration group:

- title: `成本预算与提醒`;
- subtitle: `Entra 成员预算、用量与管理员邮件`;
- badges show real “接近预算” count and mail configuration state;
- right-side `⚙ 配置` button opens the dedicated page;
- if the API is unavailable, show `状态不可用`, never sample counts.

Do not use a right-side drawer for the member budget page.

- [ ] **Step 4: Implement the dedicated page**

`MemberBudgetSettingsPage` renders:

1. back button, title `成员成本预算`, and the approved explanatory sentence;
2. `邮件设置` and `设置成员预算` buttons;
3. four summary cards: current-month estimated cost, configured members, near budget, sent alerts;
4. search and optional department filter;
5. member table columns: Entra member, cost, budget, progress, primary model, action;
6. compact administrator email strip with test and configure buttons;
7. recent alert history;
8. small budget modal and mail-settings modal.

On mobile, render member rows as cards and keep primary actions at the top. Use skeletons with fixed height, `partial/unavailable/not_configured/permission_required/conflict/empty` states, and 409 reload behavior.

Credential, raw actor ID, ACS operation ID, and raw service errors must never enter component state.

- [ ] **Step 5: Run Node, build, and Playwright**

Run:

```powershell
Set-Location web
node --test
npm run build
npx playwright test tests/finops-portal-acceptance.spec.mjs
```

Expected: all pass. Playwright must capture and assert:

- desktop settings entry;
- desktop member budget page;
- mobile member budget page;
- edit `$200` budget with `80/95/100`;
- partial pricing coverage;
- mail configuration and test success;
- inactive identity;
- 409 reload;
- API failure/empty states;
- no raw IDs or secret-like content.

- [ ] **Step 6: Commit**

```powershell
git add web/src/MemberBudgetSettingsPage.jsx web/src/memberBudgetViewModel.js web/src/memberBudgetViewModel.test.mjs web/src/components.jsx web/src/api.js web/src/styles.css web/src/finopsApi.test.mjs web/tests/finopsMockApi.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "feat(web): manage member budgets and email alerts"
```

### Task 7: Azure Resource Setup, Full Acceptance, and Release Gates

**Files:**
- Modify: `README.md`
- Create: `docs/validation/2026-07-28-member-budget-email-candidate-runbook.md`
- Create: `docs/validation/2026-07-28-member-budget-email-candidate.md`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: a zero-traffic candidate and explicit evidence for the final automatic-email gate.

- [ ] **Step 1: Prepare ACS resources without secrets**

In the approved Azure resource group:

1. create Email Communication Services;
2. provision `AzureManagedDomain`;
3. create or select the Communication Services resource;
4. link the Azure Managed Domain;
5. grant the backend system-assigned managed identity only the data-plane permission required to send email;
6. configure `DF_ACS_EMAIL_ENDPOINT` and `DF_ACS_EMAIL_SENDER_ADDRESS` on the zero-traffic backend candidate.

Record resource names and role assignment result in the candidate evidence, but do not record subscription IDs, principal IDs, access tokens, connection strings, or service keys.

- [ ] **Step 2: Apply SQL and run automated gates**

Apply `backend/sql/finops_schema.sql` as an additive migration, then run:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
```

Expected: all Python, Node, Vite, and Playwright checks pass and the diff check is clean.

- [ ] **Step 3: Validate with automatic sending still off**

Candidate flags:

```text
DF_FINOPS_MEMBER_BUDGETS_ENABLED=1
DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1
DF_FINOPS_EMAIL_ALERTS_ENABLED=0
DF_FINOPS_ACTIONS_ENABLED=0
```

Validate:

1. save an active administrator recipient;
2. send one `[测试]` ACS email to that administrator;
3. confirm the email arrives;
4. verify another active member or external address cannot be saved as recipient;
5. verify a member cannot read the page or APIs;
6. manually recompute one member’s monthly spend from priced ledger rows;
7. verify unpriced rows reduce coverage without becoming zero cost;
8. verify configuration persists after reload and on another device;
9. review desktop/mobile screenshots and candidate logs.

- [ ] **Step 4: Validate threshold delivery in a controlled candidate**

With the candidate still at zero traffic:

1. create a `$200` test member budget with thresholds `80/95/100`;
2. ingest controlled priced request facts totaling `$190`;
3. temporarily enable automatic alerts only on the isolated candidate/job;
4. run `python -m backend.finops.member_budget_refresh`;
5. confirm one 95% email and one alert row;
6. rerun the job and confirm no duplicate email;
7. test a direct jump above 100% and confirm only the highest threshold sends;
8. return the candidate automatic-alert flag to `0`.

- [ ] **Step 5: Record evidence and commit**

The evidence document includes immutable image digests, candidate revision names, SQL migration result, HTTP statuses, email timestamps, alert IDs, screenshot paths, log-query results, and rollback targets. It excludes member emails from screenshots unless the test tenant explicitly uses non-sensitive demo addresses.

```powershell
git add README.md docs/validation/2026-07-28-member-budget-email-candidate-runbook.md docs/validation/2026-07-28-member-budget-email-candidate.md
git commit -m "docs: record member budget email acceptance"
```

- [ ] **Step 6: Stop at the production gate**

Do not enable `DF_FINOPS_EMAIL_ALERTS_ENABLED` or switch candidate traffic until the user explicitly approves. When approval is received, promote backend before web, recheck health and critical logs, then enable the scheduled job last.

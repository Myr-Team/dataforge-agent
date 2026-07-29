# Tenant FinOps Budget Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align all member-budget and budget-alert API/UI access with the tenant-wide evaluator by requiring one trusted Entra FinOps administrator application role.

**Architecture:** A shared router context performs feature gating, trusted Easy Auth validation, exact application-role authorization, canonical tenant/actor derivation, and same-tenant workspace discovery. Existing services continue receiving workspace IDs, but they receive the complete trusted tenant workspace set rather than caller membership scope. The frontend distinguishes authorization from availability and hides budget evidence when the tenant role is absent.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, Node test runner, React, Vite, Playwright.

## Global Constraints

- Default application role is exactly `DataForge.FinOpsAdmin`.
- `DF_FINOPS_TENANT_ADMIN_ROLE` takes precedence over the deprecated `DF_FINOPS_EMAIL_ADMIN_ROLE` fallback.
- Only trusted Easy Auth `roles` claims authorize access; workspace roles and client actors never authorize it.
- Tenant discovery includes only workspaces containing an internal trusted identity from the same canonical tenant and fails closed when empty.
- The evaluator remains tenant-wide.
- No cloud deployment, Azure mutation, authentication middleware change, secret output, or unrelated refactor.
- Preserve shared actor/model-provider WIP and stage only files named in this plan.

---

### Task 1: Shared tenant FinOps administrator context

**Files:**
- Modify: `tests/test_finops_member_budget_api.py`
- Modify: `backend/finops/member_budget_router.py`

**Interfaces:**
- Consumes: `actor_from_request`, `is_trusted_tenant_identity`, `list_workspaces`, `workspace_finops_member_identities`, `canonical_tenant_id`, `canonical_tenant_ref`, and `canonical_actor_ref`.
- Produces: `_context(request) -> tuple[tenant_ref, actor_ref, tenant_workspace_ids, actor]` authorized by the tenant FinOps application role.

- [ ] **Step 1: Add failing API authorization tests**

Parameterize `GET /member-budgets`, `GET /member-budget-members`,
`POST /member-budgets`, `PATCH /member-budgets/{id}`,
`POST /member-budgets/{id}/disable`, and `GET /budget-alerts`. Use invalid
query/body values and assertion-raising workspace/service/audit doubles to
prove a workspace Owner/Admin without the application role returns `403`
before those operations. Add cases for a suffixed role and an untrusted client
actor.

- [ ] **Step 2: Add failing tenant-scope and environment precedence tests**

Provide workspaces from tenant A, tenant B, and an empty/untrusted workspace.
Assert a role holder receives only tenant A workspace IDs in service calls,
regardless of caller workspace role. Assert no matching tenant workspace
returns `403`. Assert `DF_FINOPS_TENANT_ADMIN_ROLE=DataForge.BudgetOperator`
wins over `DF_FINOPS_EMAIL_ADMIN_ROLE=DataForge.LegacyOperator`, while the
legacy value works only when the new value is absent.

- [ ] **Step 3: Run RED tests**

Run:

```powershell
python -m pytest -q tests/test_finops_member_budget_api.py -k "tenant_finops or member_budget_routes_require"
```

Expected: tests fail because current `_context` authorizes workspace
Owner/Admin before checking any tenant application role and scopes to caller
workspace membership.

- [ ] **Step 4: Implement the shared context**

Add these focused helpers in `member_budget_router.py`:

```python
_DEFAULT_TENANT_ADMIN_ROLE = "DataForge.FinOpsAdmin"

def _required_tenant_admin_role() -> str:
    return (
        str(os.environ.get("DF_FINOPS_TENANT_ADMIN_ROLE") or "").strip()
        or str(os.environ.get("DF_FINOPS_EMAIL_ADMIN_ROLE") or "").strip()
        or _DEFAULT_TENANT_ADMIN_ROLE
    )

def _has_tenant_admin_role(actor: Mapping[str, Any]) -> bool:
    roles = actor.get("roles")
    trusted_roles = roles if isinstance(roles, (list, tuple, set, frozenset)) else ()
    required = _required_tenant_admin_role().casefold()
    return (
        str(actor.get("source") or "").strip().casefold() == "easy_auth"
        and required in {
            role.strip().casefold()
            for role in trusted_roles
            if isinstance(role, str) and role.strip()
        }
    )
```

After trusted identity and role validation, discover candidate workspace IDs
from `list_workspaces()`. Retain a workspace only if
`workspace_finops_member_identities(workspace_id)` contains a record whose
`canonical_tenant_id(record["tenant_id"])` equals the actor's canonical tenant.
Sort and deduplicate the result; return `403` when it is empty. Remove
`active_workspace_role` from this authorization path.

- [ ] **Step 5: Run GREEN API tests**

Run:

```powershell
python -m pytest -q tests/test_finops_member_budget_api.py
```

Expected: all member-budget API tests pass.

### Task 2: Frontend permission contract

**Files:**
- Modify: `web/src/memberBudgetViewModel.test.mjs`
- Modify: `web/src/memberBudgetViewModel.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/MemberBudgetSettingsPage.jsx`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes: HTTP `403` from member-budget, eligible-member, alert, and notification endpoints.
- Produces: `memberBudgetHomeSummaryViewModel({status: "permission_required"})` with a role-required summary and a detail page that displays no budget evidence.

- [ ] **Step 1: Add failing view-model and Playwright tests**

Add a Node assertion that `status: "permission_required"` returns:

```js
{
  state: "permission_required",
  stateLabel: "需要权限",
  nearBudgetLabel: "需要租户 FinOps 管理员角色",
  mailLabel: "预算与提醒已受限",
  actionLabel: "查看权限说明",
}
```

Extend the mock API with `memberBudgetAccessState: "permission_required"` so
all four primary budget reads return `403`. Add Playwright coverage proving the
settings entry uses the role-required copy/action, the detail page contains the
role explanation, no member table or estimated amount is rendered, and default
role-holder mocks retain the existing full page.

- [ ] **Step 2: Run RED frontend tests**

Run:

```powershell
Set-Location web
node --test src/memberBudgetViewModel.test.mjs
npx playwright test tests/finops-portal-acceptance.spec.mjs -g "tenant FinOps"
```

Expected: Node collapses the status into the normal empty/unavailable model and
Playwright sees generic unavailable copy.

- [ ] **Step 3: Implement the minimal permission states**

In `components.jsx`, map a rejected primary budget request with status `403` to
`memberBudgetHomeSummaryViewModel({status: "permission_required"})`. Render the
summary's `actionLabel` in the entry button.

In `memberBudgetViewModel.js`, return the exact permission summary above before
normal budget aggregation.

In `MemberBudgetSettingsPage.jsx`, change the primary permission state to:

```jsx
<strong>需要租户 FinOps 管理员角色</strong>
<p>成员成本预算按租户汇总。请联系租户管理员分配应用角色后重新登录。</p>
```

Keep tables, metrics, alerts, and mutation controls outside the permission
branch.

- [ ] **Step 4: Run GREEN frontend tests**

Run:

```powershell
node --test src/memberBudgetViewModel.test.mjs
npm run build
npx playwright test tests/finops-portal-acceptance.spec.mjs -g "tenant FinOps"
```

Expected: all focused tests pass and Vite builds successfully.

### Task 3: Operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/validation/2026-07-28-member-budget-email-candidate-runbook.md`

**Interfaces:**
- Consumes: the shared tenant role and deterministic tenant workspace context from Task 1.
- Produces: operator prerequisites and candidate acceptance steps matching runtime behavior.

- [ ] **Step 1: Update environment-variable documentation**

Document:

```text
DF_FINOPS_TENANT_ADMIN_ROLE=DataForge.FinOpsAdmin
```

Mark `DF_FINOPS_EMAIL_ADMIN_ROLE` as a deprecated fallback used only when the
new variable is absent or blank. State that the new variable wins when both are
set.

- [ ] **Step 2: Align authorization and audit guidance**

State that every budget, member, alert, and email endpoint requires the trusted
tenant application role; workspace Owner/Admin alone is insufficient. Explain
that tenant workspace discovery uses trusted same-tenant identities and fails
closed, while mutation audit persistence uses the lexicographically first
tenant workspace only as an internal deterministic audit scope.

- [ ] **Step 3: Update candidate commands and acceptance checks**

Set `DF_FINOPS_TENANT_ADMIN_ROLE=$ApprovedTenantFinOpsAdminRole` in backend
candidate commands. Add positive role-holder and negative no-role/near-match
checks across budget and alert endpoints. Keep all production switches and
traffic unchanged.

### Task 4: Regression verification and scoped commit

**Files:**
- Test only the files changed in Tasks 1-3.

**Interfaces:**
- Consumes: completed backend, frontend, and docs work.
- Produces: reproducible test evidence and a commit containing no shared WIP.

- [ ] **Step 1: Run focused backend tests**

```powershell
python -m pytest -q tests/test_finops_member_budget_api.py tests/test_finops_member_budget_sql_repository.py tests/test_finops_member_budget_evaluator.py tests/test_finops_member_budget_refresh.py tests/test_finops_acs_email.py
```

- [ ] **Step 2: Run frontend regression**

```powershell
Set-Location web
node --test
npm run build
npx playwright test
```

- [ ] **Step 3: Check the scoped diff**

Run `git diff --check` against only the files in this plan. Verify
`tests/test_model_provider_api.py`, `.superpowers/sdd/task-1-report.md`, local
Playwright output, and workspace fixtures remain unstaged.

- [ ] **Step 4: Commit implementation**

Stage only the Task 1-3 files and commit:

```powershell
git commit -m "fix(finops): align budgets to tenant admin scope"
```


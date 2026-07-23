# Governance Navigation and Enterprise Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Implement B+ governance navigation, enterprise identity display, and role-gated governance screens without changing Easy Auth.

**Architecture:** The backend owns authorization, domain policy, member projection, and lineage filtering. The frontend asks for a capability projection, then renders only the permitted navigation entries. Existing member, Monitor BI, cost/ROI, model-route, and connector read models are reused in focused pages rather than duplicated.

**Tech Stack:** FastAPI, Python, React, Vite, Lucide React, pytest, Node test runner, Azure Container Apps.

## Global Constraints

- Do not change Easy Auth, Entra authentication, login flow, or token storage.
- Do not expose actor IDs, tenant IDs, claims, prompts, credentials, connection strings, or raw telemetry.
- Show name and enterprise email only for an active workspace member whose email matches an explicit stored domain allowlist.
- Use backend capability data for visibility. A hidden client button is never an authorization boundary.
- Unavailable telemetry, cost, ROI, and connector health must remain unavailable or not recorded.
- Keep fixed rail and frame dimensions at 1536 x 960 and 1024 x 800.
- Use existing DataWorkbench modal language for policy configuration.
- Candidate revisions remain at zero traffic until the user approves the evidence.

## File Structure

| File | Responsibility |
| --- | --- |
| backend/identity.py | Safe email-domain normalizer. |
| backend/control_plane.py | Domain policy, member projection, capabilities, scoped lineage. |
| backend/workspace_authz.py | Owner-only actions. |
| tests/test_actor_audit_usage.py | Identity and lineage API tests. |
| tests/test_workspace_roles.py | Role enforcement tests. |
| web/src/constants.js | Grouped navigation schema. |
| web/src/api.js | Capability, policy, and lineage client functions. |
| web/src/governanceViewModel.js | Display-safe member and lineage view models. |
| web/src/GovernanceCenter.jsx | Members, lineage, cost/value, models/connections, settings pages. |
| web/src/EnterpriseIdentityPolicyModal.jsx | Owner-only domain configuration modal. |
| web/src/App.jsx | Capability loading and view dispatch. |
| web/src/components.jsx | ShellNav grouping and SettingsCenter extraction. |
| web/src/styles.css | Stable dimensions, table overflow, dialog layout. |
| web/src/*.test.mjs | Client contracts and component helpers. |

## Task 1: Add enterprise identity policy and safe member display

**Files:**
- Modify: backend/identity.py
- Modify: backend/control_plane.py:120-235,1171-1270,2499-2520
- Modify: backend/workspace_authz.py:20-110
- Test: tests/test_actor_audit_usage.py
- Test: tests/test_workspace_roles.py

**Interfaces:**
- GET /api/workspaces/{workspace_id}/members adds identity_visibility and optional display.
- PUT /api/workspaces/{workspace_id}/governance/identity-policy accepts trusted_email_domains.

- [ ] **Step 1: Write the failing tests**

~~~python
def test_member_projection_only_discloses_verified_enterprise_identity(monkeypatch):
    meta = {
        "workspace_owner": {
            "name": "Owner", "email": "owner@corp.example",
            "actor_id": "owner", "tenant_id": "tenant",
        },
        "enterprise_identity_policy": {"trusted_email_domains": ["corp.example"]},
        "workspace_members": [{
            "name": "External", "email": "external@outside.example",
            "actor_id": "external", "tenant_id": "tenant",
            "role": "editor", "status": "active",
        }],
    }
    monkeypatch.setattr(control_plane, "_load_workspace_meta", lambda _id: meta)
    monkeypatch.setattr(control_plane, "_workspace_usage_by_actor", lambda _id: {"members": []})
    owner, external = control_plane.workspace_member_roles("ws-enterprise", RequestStub())["members"]
    assert owner["identity_visibility"] == "verified_enterprise"
    assert owner["display"] == {"name": "Owner", "email": "owner@corp.example"}
    assert external["identity_visibility"] == "pseudonymous"
    assert "display" not in external
    assert "actor_id" not in external
    assert "tenant_id" not in external


def test_owner_saves_normalized_enterprise_domains(monkeypatch):
    saved = {}
    monkeypatch.setattr(control_plane, "_load_workspace_meta", lambda _id: {})
    monkeypatch.setattr(control_plane, "_save_workspace_meta", lambda _id, meta: saved.update(meta))
    monkeypatch.setattr(control_plane, "_require_workspace_owner", lambda *_args: "owner")
    result = control_plane.update_workspace_enterprise_identity_policy(
        "ws-enterprise",
        {"trusted_email_domains": ["CORP.EXAMPLE", "corp.example", "invalid"]},
        RequestStub(),
    )
    assert result["trusted_email_domains"] == ["corp.example"]
    assert saved["enterprise_identity_policy"] == {"trusted_email_domains": ["corp.example"]}
~~~

- [ ] **Step 2: Run the test and confirm it fails**

Run:

~~~powershell
python -m pytest -q tests/test_actor_audit_usage.py -k "enterprise_identity or enterprise_domains"
~~~

Expected: FAIL because the policy and display fields do not exist.

- [ ] **Step 3: Implement the smallest safe contract**

Add to backend/identity.py:

~~~python
def email_domain(email: Any) -> str:
    text = _valid_email(email).lower()
    return text.rsplit("@", 1)[1] if "@" in text else ""


def normalized_email_domains(values: Any, *, maximum: int = 20) -> list[str]:
    candidates = values if isinstance(values, list) else []
    domains = []
    for candidate in candidates:
        value = str(candidate or "").strip().lower().rstrip(".")
        if re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
            value,
        ):
            domains.append(value)
    return list(dict.fromkeys(domains))[:maximum]
~~~

Add a policy reader and writer in backend/control_plane.py. The writer calls _require_workspace_owner(workspace_id, request, "identity_policy.write"), saves only trusted_email_domains under enterprise_identity_policy, and returns sanitized values.

Change _public_workspace_member to return:

~~~python
{
    "identity_visibility": "verified_enterprise",
    "display": {"name": clean_name, "email": clean_email},
}
~~~

only for active trusted identities with an allowed domain. Return identity_visibility set to pseudonymous and omit display in all other cases. Keep subject_label as a technical reference.

- [ ] **Step 4: Add the owner-only action and run the focused suite**

Run:

~~~powershell
python -m pytest -q tests/test_actor_audit_usage.py tests/test_workspace_roles.py
~~~

Expected: PASS and no member response contains actor_id or tenant_id.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/identity.py backend/control_plane.py backend/workspace_authz.py tests/test_actor_audit_usage.py tests/test_workspace_roles.py
git commit -m "feat: project verified enterprise member identities"
~~~

## Task 2: Add governance capability and lineage scope APIs

**Files:**
- Modify: backend/control_plane.py:160-235,490-545
- Modify: backend/workspace_authz.py:20-110
- Test: tests/test_actor_audit_usage.py
- Test: tests/test_workspace_roles.py
- Test: tests/test_azure_monitor_status.py

**Interfaces:**
- GET /api/workspaces/{workspace_id}/governance/capabilities returns members, lineage, cost_value, models_connections, and settings.
- GET /api/workspaces/{workspace_id}/governance/lineage?scope=self|workspace returns safe, paginated lineage.

- [ ] **Step 1: Write failing capability and authorization tests**

~~~python
def test_editor_capabilities_hide_owner_governance(monkeypatch):
    monkeypatch.setattr(control_plane, "_require_workspace_permission", lambda *_args: "editor")
    sections = control_plane.workspace_governance_capabilities("ws-caps", RequestStub())["sections"]
    assert sections["members"] == {"visible": True, "write": False}
    assert sections["lineage"] == {"visible": True, "scope": "self"}
    assert sections["cost_value"] == {"visible": False}
    assert sections["models_connections"] == {"visible": False}
    assert sections["settings"] == {"visible": False}


def test_editor_cannot_request_workspace_lineage(monkeypatch):
    monkeypatch.setattr(control_plane, "_require_workspace_permission", lambda *_args: "editor")
    with pytest.raises(HTTPException) as error:
        control_plane.workspace_governance_lineage("ws-caps", "workspace", RequestStub())
    assert error.value.status_code == 403
~~~

- [ ] **Step 2: Run the test and confirm it fails**

Run:

~~~powershell
python -m pytest -q tests/test_actor_audit_usage.py -k "capabilities or workspace_lineage"
~~~

Expected: FAIL because the APIs do not exist.

- [ ] **Step 3: Implement the role map and lineage filter**

Use one backend-only mapper:

~~~python
def _governance_sections(role: str) -> dict[str, dict[str, Any]]:
    owner = role == "owner"
    return {
        "members": {"visible": True, "write": owner},
        "lineage": {"visible": True, "scope": "workspace" if owner else "self"},
        "cost_value": {"visible": owner},
        "models_connections": {"visible": owner},
        "settings": {"visible": owner},
    }
~~~

For scope=self, filter records with canonical_actor_identity before public projection. For scope=workspace, require existing Owner audit permission. Each row includes title, timestamp, route, status, sanitized correlation reference, data revision, evidence revision, safe initiator label, and freshness. Exclude raw prompt, model reasoning, and raw telemetry payload.

- [ ] **Step 4: Run backend governance regression**

Run:

~~~powershell
python -m pytest -q tests/test_actor_audit_usage.py tests/test_workspace_roles.py tests/test_azure_monitor_status.py tests/test_monitoring_dashboard_api.py tests/test_cost_value_api.py
~~~

Expected: PASS. Existing Owner-only cost, ROI, and model reads remain denied to members.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/control_plane.py backend/workspace_authz.py tests/test_actor_audit_usage.py tests/test_workspace_roles.py tests/test_azure_monitor_status.py
git commit -m "feat: add role-aware governance capabilities and lineage"
~~~

## Task 3: Replace monitor-only navigation filtering

**Files:**
- Modify: web/src/constants.js
- Modify: web/src/api.js
- Modify: web/src/governanceViewModel.js
- Modify: web/src/App.jsx:130-620,1120-1180
- Modify: web/src/components.jsx:117-230,3060-3250
- Test: web/src/constants.test.mjs
- Test: web/src/governanceViewModel.test.mjs
- Create: web/src/governanceNavigation.test.mjs

**Interfaces:**
- Consumes Task 1 member projection and Task 2 capability projection.
- Produces B+ IDs: workspaces, data, runs, conversations, artifacts, members, lineage, cost-value, models-connections, settings.

- [ ] **Step 1: Write the failing client contracts**

~~~javascript
import assert from "node:assert/strict";
import test from "node:test";
import { visibleNavItems } from "./constants.js";
import { memberDirectoryViewModel } from "./governanceViewModel.js";

test("editor navigation excludes owner-only governance entries", () => {
  const items = visibleNavItems({
    sections: {
      members: { visible: true },
      lineage: { visible: true },
      cost_value: { visible: false },
      models_connections: { visible: false },
      settings: { visible: false },
    },
  });
  assert.deepEqual(items.map((item) => item.id), [
    "workspaces", "data", "runs", "conversations", "artifacts", "members", "lineage",
  ]);
});

test("trusted member display is used before a pseudonym", () => {
  const [member] = memberDirectoryViewModel([{
    subject_label: "member_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    identity_visibility: "verified_enterprise",
    display: { name: "Ava", email: "ava@corp.example" },
    role: "owner",
  }]);
  assert.equal(member.label, "Ava");
  assert.equal(member.detail, "ava@corp.example");
});
~~~

- [ ] **Step 2: Run the client contract tests and confirm failure**

Run:

~~~powershell
node --test web/src/constants.test.mjs web/src/governanceViewModel.test.mjs web/src/governanceNavigation.test.mjs
~~~

Expected: FAIL because current navigation only understands monitor.

- [ ] **Step 3: Implement API functions, grouped items, and safe labels**

Add these functions to web/src/api.js:

~~~javascript
export function loadGovernanceCapabilities(workspaceId) {
  return request("/api/workspaces/" + encodeURIComponent(workspaceId) + "/governance/capabilities");
}

export function loadGovernanceLineage(workspaceId, { scope = "self", cursor = "" } = {}) {
  const params = new URLSearchParams({ scope });
  if (cursor) params.set("cursor", cursor);
  return request("/api/workspaces/" + encodeURIComponent(workspaceId) + "/governance/lineage?" + params.toString());
}

export function updateEnterpriseIdentityPolicy(workspaceId, trustedEmailDomains) {
  return request("/api/workspaces/" + encodeURIComponent(workspaceId) + "/governance/identity-policy", {
    method: "PUT",
    body: JSON.stringify({ trusted_email_domains: trustedEmailDomains }),
  });
}
~~~

Replace monitor-only filtering in constants.js with workspace and governance groups. Use capabilityKey values members, lineage, cost_value, models_connections, and settings. visibleNavItems must use capability visibility only, never a local guessed role.

In governanceViewModel.js, use display.name and display.email only when identity_visibility equals verified_enterprise. Otherwise render a generic localized unverified label with no hash substring.

- [ ] **Step 4: Wire App and ShellNav**

Load capability data on workspace change. Redirect activeView to workspaces when the current view becomes unavailable. Render 工作空间 and 治理 labels only if their group has visible items. The compact footer action opens members rather than a Settings tab.

- [ ] **Step 5: Run client tests and build**

Run:

~~~powershell
node --test web/src/constants.test.mjs web/src/governanceViewModel.test.mjs web/src/governanceNavigation.test.mjs web/src/governanceApi.test.mjs
npm --prefix web run build
~~~

Expected: PASS without missing lazy imports.

- [ ] **Step 6: Commit**

~~~powershell
git add web/src/constants.js web/src/api.js web/src/governanceViewModel.js web/src/App.jsx web/src/components.jsx web/src/constants.test.mjs web/src/governanceViewModel.test.mjs web/src/governanceNavigation.test.mjs
git commit -m "feat: organize capability-driven governance navigation"
~~~

## Task 4: Build focused pages and configuration modal

**Files:**
- Create: web/src/GovernanceCenter.jsx
- Create: web/src/EnterpriseIdentityPolicyModal.jsx
- Modify: web/src/App.jsx
- Modify: web/src/components.jsx
- Modify: web/src/styles.css
- Test: web/src/governanceCenter.test.mjs
- Test: web/src/enterpriseIdentityPolicyModal.test.mjs

**Interfaces:**
- GovernanceCenter receives section, workspaceId, capabilities, members, settings, and user.
- EnterpriseIdentityPolicyModal receives initialDomains, busy, error, onSave, and onClose.

- [ ] **Step 1: Write failing component helper tests**

~~~javascript
import assert from "node:assert/strict";
import test from "node:test";
import { normalizeDomainDraft, resolveLineageScope } from "./GovernanceCenter.jsx";

test("lineage scope comes from backend capability", () => {
  assert.equal(resolveLineageScope({ sections: { lineage: { scope: "self" } } }), "self");
  assert.equal(resolveLineageScope({ sections: { lineage: { scope: "workspace" } } }), "workspace");
});

test("domain draft keeps valid unique domains", () => {
  assert.deepEqual(normalizeDomainDraft("CORP.EXAMPLE, corp.example, invalid"), ["corp.example"]);
});
~~~

- [ ] **Step 2: Run the test and confirm failure**

Run:

~~~powershell
node --test web/src/governanceCenter.test.mjs web/src/enterpriseIdentityPolicyModal.test.mjs
~~~

Expected: FAIL because neither component exists.

- [ ] **Step 3: Implement GovernanceCenter**

~~~jsx
export function GovernanceCenter(props) {
  switch (props.section) {
    case "members": return <MembersGovernancePage {...props} />;
    case "lineage": return <LineageGovernancePage {...props} />;
    case "cost-value": return <CostValueGovernancePage {...props} />;
    case "models-connections": return <ModelsConnectionsPage {...props} />;
    case "settings": return <WorkspaceSettingsPage {...props} />;
    default: return <GovernanceUnavailablePage />;
  }
}
~~~

Members extracts invitation and role controls from SettingsCenter. Lineage calls loadGovernanceLineage with granted scope. CostValue reuses MonitorPage view-model data. ModelsConnections uses connector status APIs and masks secrets. Settings retains lifecycle and preference content only. Each page uses the fixed states loading, ready, empty, denied, unavailable, and partial.

- [ ] **Step 4: Implement the Owner-only modal and fixed layout**

The modal trigger is a SlidersHorizontal icon button with a tooltip in the members header. It uses an explicit Save button, a Cancel button, a busy state, bounded error copy, and this input:

~~~jsx
<input
  aria-label="企业邮箱域"
  value={domainDraft}
  onChange={(event) => setDomainDraft(event.target.value)}
  placeholder="corp.example"
/>
~~~

On save, refresh members and capabilities before close. Do not mount trigger or modal for non-Owner capability.

Add these stable styles:

~~~css
.shell-nav { width: clamp(224px, 15vw, 240px); flex: 0 0 clamp(224px, 15vw, 240px); }
.nav-group-label { min-height: 24px; }
.governance-page { min-width: 0; }
.governance-data-frame { min-height: 272px; }
.governance-table-scroll { overflow: auto; max-width: 100%; }
~~~

Use grid/minmax tracks, radii no larger than 8px, no width or height transitions, and table-local horizontal overflow.

- [ ] **Step 5: Run UI tests and build**

Run:

~~~powershell
node --test web/src/governanceCenter.test.mjs web/src/enterpriseIdentityPolicyModal.test.mjs web/src/navigationContract.test.mjs web/src/costValuePanel.test.mjs
npm --prefix web run build
~~~

Expected: PASS. Hidden sections do not fetch data or render hidden controls.

- [ ] **Step 6: Commit**

~~~powershell
git add web/src/GovernanceCenter.jsx web/src/EnterpriseIdentityPolicyModal.jsx web/src/App.jsx web/src/components.jsx web/src/styles.css web/src/governanceCenter.test.mjs web/src/enterpriseIdentityPolicyModal.test.mjs
git commit -m "feat: split governance pages and policy configuration"
~~~

## Task 5: Validate candidate revisions before production

**Files:**
- Create: docs/validation/2026-07-23-governance-navigation-evidence.md
- Test: complete backend and frontend suites

**Interfaces:**
- Consumes Task 1-4 APIs and UI routes.
- Produces redacted candidate evidence and an explicit production promotion request.

- [ ] **Step 1: Define signed-in acceptance paths**

~~~text
Owner: members -> verified identity -> policy modal -> valid domain save
Member: members -> no policy trigger -> lineage self scope -> direct cost-value API is 403
Owner: lineage workspace scope -> cost/value -> models/connections -> settings
All: workspaces -> data -> runs -> conversations -> artifacts without layout shift
~~~

Record only route, HTTP status, response shape, viewport, and redacted screenshot path.

- [ ] **Step 2: Run full automated verification**

Run:

~~~powershell
python -m pytest -q
node --test web/src/*.test.mjs
npm --prefix web run build
~~~

Expected: all tests PASS. Any failure blocks candidate deployment.

- [ ] **Step 3: Build zero-traffic candidate revisions**

Use current ACR and Container Apps scripts. Deploy backend and web candidate revisions at 0 percent traffic. Verify backend health returns ok: true before browser validation.

- [ ] **Step 4: Run signed-in candidate checks**

At 1536 x 960 and 1024 x 800, test every Workspace and Governance destination, identity policy modal, member denial path, state-frame stability, and a real lineage record. Save redacted evidence.

- [ ] **Step 5: Commit evidence and request promotion approval**

~~~powershell
git add docs/validation/2026-07-23-governance-navigation-evidence.md
git commit -m "docs: record governance navigation validation"
~~~

Do not shift production traffic until the user explicitly approves the candidate evidence.

## Plan Self-Review

### Spec coverage

- B+ destinations and Settings separation: Tasks 3 and 4.
- Trusted enterprise name/email and pseudonymous fallback: Task 1.
- Owner/member enforcement and direct endpoint protection: Task 2.
- Low-frequency configuration in a modal: Task 4.
- Stable dimensions and truthful unavailable states: Tasks 3, 4, and 5.
- Candidate-first release: Task 5.

### Placeholder scan

The scan is clear. Each task contains concrete files, tests, commands, and expected behavior rather than deferred work markers or unspecified validation.

### Type consistency

Backend section keys are members, lineage, cost_value, models_connections, and settings. Frontend IDs are members, lineage, cost-value, models-connections, and settings. identity_visibility is verified_enterprise or pseudonymous; display is only present for verified_enterprise.

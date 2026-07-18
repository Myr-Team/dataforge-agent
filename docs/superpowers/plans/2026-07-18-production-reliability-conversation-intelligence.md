# Production Reliability and Conversation Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Restore strict, explainable Entra workspace access and make each human chat turn use the smallest truthful route, a consistent Markdown answer, and auditable route metadata.

**Architecture:** One backend WorkspaceAccessDecision becomes authoritative for ordinary and sensitive workspace operations. The conversation coordinator derives a typed route after model classification and applies deterministic evidence policy before execution. It keeps automatic analysis separate from conversations and retains current artifact lineage.

**Tech Stack:** FastAPI, Pydantic, Python pytest, Azure Container Apps, Azure Easy Auth headers, Blob-backed workspace metadata, React/Vite, Node test runner, SSE.

## Global Constraints

- Do not disable RBAC, grant access by email, change Easy Auth, tenant configuration, client redirects, or authentication routes.
- Grant owner access only when trusted Entra object ID and tenant ID match an existing owner record or a strict owner-member normalization.
- Return and audit only bounded access reason codes; do not log raw claims, tokens, credentials, or email-based decisions.
- Do not hard-code a domain, score, opportunity, clarification question, or business conclusion.
- Do not start full analysis for ordinary follow-up chat. Automatic analysis remains a non-conversation run.
- Retain already streamed browser content if a later stage fails.
- Preserve run_id, conversation_id, origin, persist_messages, and artifact source_run_id.
- Candidate validation must pass before traffic promotion. Production traffic changes require explicit user approval.

---

## File Structure

- Modify: backend/workspace_authz.py - identity-aware decision and legacy-owner normalization.
- Modify: backend/app.py, backend/data_workbench.py, backend/control_plane.py - one authorization HTTP envelope and audit reason code.
- Modify: backend/schemas.py, backend/orchestrator.py, backend/run_store.py - typed route, routing policy, Markdown, and trace fields.
- Create: web/src/workspaceAccess.js - maps structured authorization failures to recovery states.
- Modify: web/src/api.js, web/src/App.jsx, web/src/components.jsx - retain server detail, prevent fallback masking, and render route labels.
- Modify: tests/test_workspace_roles.py, tests/test_actor_audit_usage.py, tests/test_execution_identity.py, tests/test_followup_provisional_choice.py.
- Create: tests/test_conversation_routes.py and web/src/workspaceAccess.test.mjs.
- Create: docs/validation/production-reliability-conversation-intelligence.md - candidate evidence and promotion gate.

## Task 1: Canonical Workspace Access Decision

**Files:**
- Modify: backend/workspace_authz.py:1-280
- Test: tests/test_workspace_roles.py:1-170

**Interfaces:**
- Consumes: public_actor, canonical_actor_identity, is_trusted_tenant_identity, _load_workspace_meta, current_invited_member_role.
- Produces: WorkspaceAccessDecision, WorkspaceAuthorizationError, workspace_access_decision, with_action, workspace_role, active_workspace_role.
- Contract: reason_code is exactly one of owner_match, member_match, identity_missing, tenant_mismatch, membership_missing, role_denied.

- [ ] **Step 1: Write failing strict access tests**

~~~python
def _actor(oid: str, tid: str = "tenant-a") -> dict[str, str]:
    return {"actor_id": oid, "tenant_id": tid, "source": "easy_auth"}


def test_access_decision_normalizes_matching_legacy_owner_without_email_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict[str, object]] = []
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [],
    })
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda _id, meta: saved.append(dict(meta)))

    decision = workspace_authz.workspace_access_decision("ws-legacy", _actor("owner-oid"))

    assert (decision.allowed, decision.role, decision.reason_code) == (True, "owner", "owner_match")
    assert saved[0]["workspace_members"][0]["actor_id"] == "owner-oid"
    assert saved[0]["workspace_members"][0]["tenant_id"] == "tenant-a"


def test_access_decision_rejects_same_oid_from_another_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [],
    })

    decision = workspace_authz.workspace_access_decision("ws-legacy", _actor("owner-oid", "tenant-b"))

    assert (decision.allowed, decision.role, decision.reason_code) == (False, None, "tenant_mismatch")
~~~

- [ ] **Step 2: Run the new tests and verify failure**

Run: python -m pytest tests/test_workspace_roles.py::test_access_decision_normalizes_matching_legacy_owner_without_email_grant tests/test_workspace_roles.py::test_access_decision_rejects_same_oid_from_another_tenant -v

Expected: FAIL because workspace_access_decision does not exist.

- [ ] **Step 3: Implement the decision and normalization**

~~~python
@dataclass(frozen=True)
class WorkspaceAccessDecision:
    allowed: bool
    role: str | None
    reason_code: str


class WorkspaceAuthorizationError(PermissionError):
    def __init__(self, action: str, decision: WorkspaceAccessDecision) -> None:
        self.action = action
        self.decision = decision
        super().__init__("workspace access denied")


def with_action(decision: WorkspaceAccessDecision, action: str) -> WorkspaceAccessDecision:
    if not decision.role:
        return decision
    if authorize(decision.role, action):
        return decision
    return WorkspaceAccessDecision(False, decision.role, "role_denied")


def workspace_access_decision(workspace_id: str, actor: Mapping[str, Any] | None) -> WorkspaceAccessDecision:
    clean_actor = public_actor(dict(actor or {}))
    if rbac_enabled() and not is_trusted_tenant_identity(clean_actor):
        return WorkspaceAccessDecision(False, None, "identity_missing")
    meta = _load_workspace_meta(workspace_id)
    identity = canonical_actor_identity(clean_actor)
    owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    owner_identity = canonical_actor_identity(owner)
    if identity and identity == owner_identity:
        _normalize_owner_member(workspace_id, meta, clean_actor)
        return WorkspaceAccessDecision(True, "owner", "owner_match")
    if identity and owner_identity and identity[1] == owner_identity[1]:
        return WorkspaceAccessDecision(False, None, "tenant_mismatch")
    return _member_access_decision(workspace_id, meta, clean_actor)
~~~

Implement _normalize_owner_member so it adds exactly one active owner member only after the stored owner identity equals the trusted current identity. _member_access_decision must use only canonical tenant plus OID matching for invitations and active members. Make workspace_role and active_workspace_role delegate to this resolver. Make require_workspace_permission and require_sensitive_workspace_permission call with_action and raise WorkspaceAuthorizationError when the resulting decision is denied.

When normalization writes workspace metadata, append one bounded record to authorization_normalizations with kind="owner_membership", occurred_at as UTC ISO-8601, and an identity correlation digest produced with the existing audit correlation helper. Do not persist a raw object ID, tenant ID, email, or token in that record. The test must prove a second read for the same owner does not append a duplicate record.

- [ ] **Step 4: Add missing identity, active member, removed member, and role-denied tests**

~~~python
@pytest.mark.parametrize(
    ("actor", "action", "expected"),
    [
        ({}, "workspace.read", (False, None, "identity_missing")),
        (_actor("viewer-oid"), "workspace.read", (True, "viewer", "member_match")),
        (_actor("removed-oid"), "workspace.read", (False, None, "membership_missing")),
        (_actor("viewer-oid"), "file.edit", (False, "viewer", "role_denied")),
    ],
)
def test_access_decision_is_bounded(monkeypatch: pytest.MonkeyPatch, actor, action, expected) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [
            {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "role": "viewer", "status": "active"},
            {"actor_id": "removed-oid", "tenant_id": "tenant-a", "role": "editor", "status": "removed"},
        ],
    })

    decision = workspace_authz.workspace_access_decision("ws-access", actor)
    checked = workspace_authz.with_action(decision, action)

    assert (checked.allowed, checked.role, checked.reason_code) == expected
~~~

- [ ] **Step 5: Run role regression tests**

Run: python -m pytest tests/test_workspace_roles.py -q

Expected: PASS.

- [ ] **Step 6: Commit the authorization resolver**

~~~bash
git add backend/workspace_authz.py tests/test_workspace_roles.py
git commit -m "fix: unify workspace access decisions"
~~~

## Task 2: Audited HTTP Authorization Contract

**Files:**
- Modify: backend/app.py:843-885
- Modify: backend/data_workbench.py:1279-1302
- Modify: backend/control_plane.py:1-260
- Test: tests/test_actor_audit_usage.py:1-180

**Interfaces:**
- Consumes: WorkspaceAuthorizationError and WorkspaceAccessDecision.
- Produces: HTTP detail object with code, reason_code, role, message.
- Contract: identity_missing is 401; trusted identity without membership is 403; the payload contains no raw exception text.

- [ ] **Step 1: Write failing API and audit tests**

~~~python
def test_workspace_denial_is_structured_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, object]] = []
    denied = workspace_authz.WorkspaceAuthorizationError(
        "workspace.read",
        workspace_authz.WorkspaceAccessDecision(False, None, "membership_missing"),
    )
    monkeypatch.setattr(app_module, "require_workspace_permission", lambda *_args: (_ for _ in ()).throw(denied))
    monkeypatch.setattr(app_module, "record_audit_event", lambda *_args, **kwargs: recorded.append(kwargs))

    response = TestClient(app).get("/api/workspaces/ws-contract/dashboard", headers=_trusted_easy_auth_headers("member@contoso.com"))

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "workspace_access_denied",
        "reason_code": "membership_missing",
        "role": None,
        "message": "Your signed-in account does not have access to this workspace.",
    }
    assert recorded[0]["reason_code"] == "membership_missing"
~~~

- [ ] **Step 2: Run the API test and verify failure**

Run: python -m pytest tests/test_actor_audit_usage.py::test_workspace_denial_is_structured_and_audited -v

Expected: FAIL because the endpoint currently returns a raw permission string.

- [ ] **Step 3: Implement one response envelope at every workspace gate**

~~~python
def _workspace_access_http_error(exc: WorkspaceAuthorizationError, *, authenticated: bool) -> HTTPException:
    decision = exc.decision
    status_code = 401 if decision.reason_code == "identity_missing" and not authenticated else 403
    return HTTPException(
        status_code=status_code,
        detail={
            "code": "workspace_access_denied",
            "reason_code": decision.reason_code,
            "role": decision.role,
            "message": _workspace_access_message(decision.reason_code),
        },
    )
~~~

Import the exception in all three endpoint modules. Replace workspace-gate except PermissionError branches with except WorkspaceAuthorizationError, audit exc.decision.reason_code, and convert unknown PermissionError to operation_failed instead of presenting it as access policy.

- [ ] **Step 4: Add the identity-forwarding test**

~~~python
def test_missing_trusted_identity_returns_retryable_401_without_raw_claims() -> None:
    response = TestClient(app).get("/api/workspaces/ws-contract/dashboard")

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "workspace_access_denied"
    assert detail["reason_code"] == "identity_missing"
    assert "oid" not in json.dumps(detail).lower()
    assert "@" not in json.dumps(detail)
~~~

- [ ] **Step 5: Run authorization and audit regressions**

Run: python -m pytest tests/test_actor_audit_usage.py tests/test_workspace_roles.py tests/test_entra_member_invites.py -q

Expected: PASS.

- [ ] **Step 6: Commit the error contract**

~~~bash
git add backend/app.py backend/data_workbench.py backend/control_plane.py tests/test_actor_audit_usage.py
git commit -m "fix: return auditable workspace access errors"
~~~

## Task 3: Client Recovery for Access Denials

**Files:**
- Create: web/src/workspaceAccess.js
- Modify: web/src/api.js:1-150
- Modify: web/src/App.jsx:195-230
- Test: web/src/workspaceAccess.test.mjs

**Interfaces:**
- Consumes: Task 2 detail through DataForgeApiError.detail.
- Produces: workspaceAccessState(error) with kind and short customer-safe message.
- Contract: dashboard fallback rethrows 401/403 workspace authorization errors rather than making parallel unauthorized requests.

- [ ] **Step 1: Write failing browser-free tests**

~~~javascript
import test from "node:test";
import assert from "node:assert/strict";
import { workspaceAccessState } from "./workspaceAccess.js";

test("maps missing identity to retry", () => {
  const state = workspaceAccessState({
    status: 401,
    detail: { code: "workspace_access_denied", reason_code: "identity_missing" },
  });
  assert.deepEqual(state, {
    kind: "identity_not_forwarded",
    message: "The signed-in identity was not available to DataForge. Retry this page.",
  });
});

test("does not map a transport failure as authorization", () => {
  assert.equal(workspaceAccessState(new TypeError("Failed to fetch")).kind, "unknown");
});
~~~

- [ ] **Step 2: Run the JavaScript test and verify failure**

Run: node --test src/workspaceAccess.test.mjs

Expected: FAIL because workspaceAccess.js does not exist.

- [ ] **Step 3: Preserve server detail and stop fallback cascades**

~~~javascript
export class DataForgeApiError extends Error {
  constructor(message, { status, detail } = {}) {
    super(message);
    this.name = "DataForgeApiError";
    this.status = status;
    this.detail = detail;
  }
}

function isWorkspaceAuthorizationError(error) {
  return [401, 403].includes(error?.status) && error?.detail?.code === "workspace_access_denied";
}

export async function loadDashboard(workspaceId) {
  try {
    return await request("/api/workspaces/" + encodeURIComponent(workspaceId) + "/dashboard");
  } catch (error) {
    if (isWorkspaceAuthorizationError(error)) throw error;
    return loadDashboardCompatibilityFallback(workspaceId, error);
  }
}
~~~

In request, retain parsed JSON detail in DataForgeApiError. In App.jsx, call workspaceAccessState before existing workspace-not-found fallback logic. Preserve the last successful dashboard object on access denial and show only the mapped recovery message.

- [ ] **Step 4: Add member, tenant, and role mapping tests**

~~~javascript
test("maps member and tenant denials to identity-not-mapped", () => {
  for (const reasonCode of ["membership_missing", "tenant_mismatch", "role_denied"]) {
    const state = workspaceAccessState({
      status: 403,
      detail: { code: "workspace_access_denied", reason_code: reasonCode },
    });
    assert.equal(state.kind, "identity_not_mapped");
  }
});
~~~

- [ ] **Step 5: Run client checks**

Run: node --test src/workspaceAccess.test.mjs src/executionIdentity.test.mjs

Expected: PASS.

Run: npm run build

Expected: Vite build succeeds.

- [ ] **Step 6: Commit client recovery**

~~~bash
git add web/src/workspaceAccess.js web/src/workspaceAccess.test.mjs web/src/api.js web/src/App.jsx
git commit -m "fix: recover cleanly from workspace access denials"
~~~

## Task 4: Typed Conversation Routing Policy

**Files:**
- Modify: backend/schemas.py:85-105
- Modify: backend/orchestrator.py:1128-1320, 5400-5570, 6314-6525
- Create: tests/test_conversation_routes.py
- Modify: tests/test_followup_provisional_choice.py:370-620

**Interfaces:**
- Consumes: ChatRequest, compact history, workspace_context, latest canonical analysis, workspace generation.
- Produces: ConversationRoute(mode, reason, evidence_required, missing_information) attached to RoutingDecision.
- Contract: mode is one of direct, grounded_followup, plan_draft, reanalyze, clarify. Existing intent remains runtime-compatible, but execution derives from conversation_route.mode.

- [ ] **Step 1: Write failing route-policy tests**

~~~python
def test_simple_message_routes_direct_without_full_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "workspace_context", lambda _id: {"workspace_id": "ws", "doc_count": 2})
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda *_args: {"verdict": "conditional"})

    decision, meta = orchestrator._preflight_fast_route(
        ChatRequest(workspace_id="ws", message="Describe this workspace."),
        [],
    )

    assert decision.conversation_route.mode == "direct"
    assert decision.experts == []
    assert meta["execution_path"] == "direct"


def test_explicit_rerun_routes_reanalyze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "workspace_context", lambda _id: {"workspace_id": "ws", "doc_count": 2})
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda *_args: {"verdict": "conditional"})

    decision = orchestrator._routing_decision_from_llm(
        ChatRequest(workspace_id="ws", message="Rerun complete analysis using newly uploaded evidence."),
        {"intent": "followup_edit", "experts": [], "output_mode": "chat", "needs_clarification": False, "reason": "test"},
    )

    assert decision.conversation_route.mode == "reanalyze"
    assert {"df-corpus-analyst", "df-feasibility-analyst", "df-auditor"}.issubset(decision.experts)
~~~

- [ ] **Step 2: Run the route tests and verify failure**

Run: python -m pytest tests/test_conversation_routes.py -q

Expected: FAIL because RoutingDecision has no conversation_route field.

- [ ] **Step 3: Add route schema and policy-after-classification**

~~~python
ConversationRouteMode = Literal["direct", "grounded_followup", "plan_draft", "reanalyze", "clarify"]


class ConversationRoute(BaseModel):
    mode: ConversationRouteMode
    reason: str = Field(min_length=1, max_length=240)
    evidence_required: bool
    missing_information: list[str] = Field(default_factory=list, max_length=6)


class RoutingDecision(BaseModel):
    workspace_id: str
    intent: str
    experts: list[str]
    output_mode: Literal["chat", "report", "full_package"]
    needs_clarification: bool
    clarifying_question: str | None = None
    reason: str
    conversation_route: ConversationRoute = Field(
        default_factory=lambda: ConversationRoute(
            mode="direct", reason="Legacy route has not been normalized yet.", evidence_required=False
        )
    )
~~~

Create _conversation_route_for(req, decision, context, history). Apply these policy checks after model classification:

~~~python
if _explicit_heavy_analysis_requested(message) or _is_auto_analyze_request(req) or _evidence_changed_since_last_analysis(context):
    return ConversationRoute(mode="reanalyze", reason="Explicit rerun or newer evidence requires a refreshed analysis.", evidence_required=True)
if decision.needs_clarification:
    return ConversationRoute(mode="clarify", reason=_bounded_route_reason(decision.reason), evidence_required=False, missing_information=_missing_information(decision))
if _plan_draft_requested(message) and last_analysis:
    return ConversationRoute(mode="plan_draft", reason="The request turns established analysis into a plan.", evidence_required=True)
if _ordinary_workspace_qa_requested(message) or _analysis_followup_requested(message):
    return ConversationRoute(mode="grounded_followup", reason="The response needs current workspace evidence.", evidence_required=True)
return ConversationRoute(mode="direct", reason="The request does not assert workspace facts.", evidence_required=False)
~~~

Create _apply_conversation_route(decision). It maps direct to smalltalk_or_meta with no experts, grounded_followup to corpus_qa, plan_draft to followup_edit, reanalyze to feasibility_analysis, and clarify to clarify_needed. It also supplies output mode, expert list, and clarification flag for each route.

- [ ] **Step 4: Add execution-boundary and evidence-generation tests**

~~~python
def test_grounded_followup_never_starts_full_maf(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[bool] = []
    monkeypatch.setattr(orchestrator, "_try_full_maf_runtime", lambda *_args, **_kwargs: started.append(True))
    decision = _decision_with_route("grounded_followup")
    frames = list(_consume_chat(orchestrator, decision))
    assert frames[-1]["event"] == "final"
    assert started == []


def test_changed_workspace_generation_routes_reanalyze() -> None:
    route = orchestrator._conversation_route_for(
        ChatRequest(workspace_id="ws", message="What changed after this data update?"),
        _decision_with_route("grounded_followup"),
        {"workspace_generation": 9, "last_analysis_generation": 8},
        [],
    )
    assert route.mode == "reanalyze"
~~~

The _consume_chat fixture must monkeypatch workspace_context, _run_corpus_analyst, and run_coordinator_direct_reply with deterministic in-memory responses. It must change only generation values, never a file name or business keyword.

- [ ] **Step 5: Run route and existing follow-up tests**

Run: python -m pytest tests/test_conversation_routes.py tests/test_followup_provisional_choice.py tests/test_maf_integration.py -q

Expected: PASS.

- [ ] **Step 6: Commit typed routing**

~~~bash
git add backend/schemas.py backend/orchestrator.py tests/test_conversation_routes.py tests/test_followup_provisional_choice.py
git commit -m "feat: route conversations by evidence and intent"
~~~

## Task 5: Markdown Contract, Trace Metadata, and Identity Regression

**Files:**
- Modify: backend/orchestrator.py:4121-4235, 5400-5570, 6314-6525
- Modify: backend/run_store.py:92-175, 3657-3665
- Modify: web/src/components.jsx:2178-2335, 4880-4920
- Modify: tests/test_execution_identity.py, tests/test_followup_provisional_choice.py, web/src/executionIdentity.test.mjs
- Test: tests/test_conversation_routes.py

**Interfaces:**
- Consumes: RoutingDecision.conversation_route, evidence revision, existing SSE events, start_run.
- Produces: conversation_route object in run data with mode, reason, evidence_revision, elapsed_ms and SSE fields conversation_route, route_reason, evidence_revision.
- Contract: Markdown has only non-empty sections. Plan drafts keep producer offer and canonical source_analysis_run_id. Automatic analysis retains conversation_id null.

- [ ] **Step 1: Write failing response and trace tests**

~~~python
@pytest.mark.parametrize("mode", ["direct", "grounded_followup", "plan_draft", "clarify"])
def test_response_contract_has_no_empty_sections(mode: str) -> None:
    text = orchestrator._render_conversation_markdown(
        conclusion="A concise truthful conclusion.",
        evidence=["[D1] Current workspace signal"],
        gaps=["Conversion cost is not yet measured"],
        next_action="Confirm the measurement boundary.",
    )

    assert text.startswith("## ")
    assert "route:" not in text.lower()
    assert text.count("## ") == 4


def test_route_event_contains_display_safe_metadata() -> None:
    event = _route_event_for(_decision_with_route("direct"), evidence_revision=8)
    assert event["conversation_route"] == "direct"
    assert event["route_reason"]
    assert event["evidence_revision"] == 8
~~~

- [ ] **Step 2: Run the response test and verify failure**

Run: python -m pytest tests/test_conversation_routes.py::test_response_contract_has_no_empty_sections -q

Expected: FAIL because _render_conversation_markdown does not exist.

- [ ] **Step 3: Implement one Markdown renderer and persist route fields**

~~~python
def _render_conversation_markdown(
    *, conclusion: str, evidence: list[str], gaps: list[str], next_action: str
) -> str:
    sections = [f"## Summary\n{_clean_text(conclusion, 520)}"]
    if evidence:
        sections.append("## Evidence and assumptions\n" + "\n".join(f"- {item}" for item in evidence[:6]))
    if gaps:
        sections.append("## Risks and gaps\n" + "\n".join(f"- {item}" for item in gaps[:6]))
    if next_action:
        sections.append(f"## Next action\n- {_clean_text(next_action, 260)}")
    return "\n\n".join(sections)
~~~

Make _ensure_conversation_markdown_structure normalize model output through this renderer with the existing localized field-label map. In _orchestrate_chat_impl, calculate evidence revision before ready and emit it in ready, route, model_response, and final. Extend start_run with optional conversation_route and evidence_revision, then compute elapsed_ms at complete_run from stored timestamps.

Map typed routes to existing localized display labels in components.jsx. Use typed fields before legacy intent. Remove literal internal route values from display text.

- [ ] **Step 4: Extend separation and plan-linkage tests**

~~~python
def test_auto_analysis_keeps_no_human_conversation_when_route_metadata_is_present() -> None:
    request = ChatRequest(
        workspace_id="ws", message="Analyze", origin="workspace_auto_analysis", persist_messages=False
    )
    execution = execution_context(request)

    assert execution.conversation_id is None
    assert execution.origin == "workspace_auto_analysis"


def test_plan_draft_keeps_canonical_source_run_id_after_markdown_normalization() -> None:
    artifact = {"source_analysis_run_id": "analysis-v1", "answer": {"markdown": "## Summary\nDraft"}}
    assert orchestrator._plan_source_run_id("ws", "conversation-v2", artifact) == "analysis-v1"
~~~

- [ ] **Step 5: Run Python and browser regressions**

Run: python -m pytest tests/test_conversation_routes.py tests/test_execution_identity.py tests/test_followup_provisional_choice.py tests/test_followup_plan_version.py -q

Expected: PASS.

Run: node --test src/executionIdentity.test.mjs src/workspaceAccess.test.mjs

Expected: PASS.

- [ ] **Step 6: Commit trace and response contract**

~~~bash
git add backend/orchestrator.py backend/run_store.py web/src/components.jsx tests/test_conversation_routes.py tests/test_execution_identity.py tests/test_followup_provisional_choice.py web/src/executionIdentity.test.mjs
git commit -m "feat: make conversation routes observable and consistent"
~~~

## Task 6: Candidate Release Gate and Signed-In Acceptance Evidence

**Files:**
- Create: docs/validation/production-reliability-conversation-intelligence.md
- Modify: README.md only if its deployment section already documents candidate labels and production promotion.

**Interfaces:**
- Consumes: completed API, SSE, run, and UI contracts.
- Produces: candidate evidence record with test results, revision names, signed-in observations, and stop/promotion decision.
- Contract: no production traffic change occurs until automated checks pass and the signed-in owner journey produces no access banner.

- [ ] **Step 1: Create acceptance record**

~~~markdown
# Production Reliability and Conversation Intelligence Validation

## Automated evidence

~~~powershell
python -m pytest -q
node --test src/workspaceAccess.test.mjs src/executionIdentity.test.mjs
npm run build
~~~

Record command summaries, commit SHA, image tags, backend revision, and web revision.

## Signed-in candidate journey

1. Sign in as the workspace owner and load an existing workspace without an access banner.
2. Open Data, Runs, Conversations, Artifacts, and Settings and record visible state.
3. Start automatic analysis and verify origin is workspace_auto_analysis with no conversation entry.
4. Send a greeting, evidence question, plan request, and explicit rerun. Record route, Markdown sections, and run linkage.
5. Generate an artifact from the plan and verify source_run_id is the canonical analysis run.
~~~

- [ ] **Step 2: Run full automated suite**

Run: python -m pytest -q

Expected: all tests pass.

Run: node --test src/workspaceAccess.test.mjs src/executionIdentity.test.mjs

Expected: all Node tests pass.

Run: npm run build

Expected: Vite build completes.

- [ ] **Step 3: Build candidate images and deploy zero-traffic revisions**

Run: az acr build --registry acrdataforgedev --image dataforge-backend:relintel --file backend/Dockerfile .

Expected: ACR build succeeds.

Run: az acr build --registry acrdataforgedev --image dataforge-web:relintel --file web/Dockerfile .

Expected: ACR build succeeds.

Deploy candidate revisions copied from healthy convsep configuration. Set DF_SEPARATE_ANALYSIS_CONVERSATIONS=1. Do not assign production traffic. Point candidate web only at candidate backend.

- [ ] **Step 4: Capture signed-in candidate evidence and stop on failure**

Run: browser validation against candidate URL using an existing signed-in Easy Auth session.

Expected: all signed-in journey steps pass. If any page returns workspace_access_denied, retain candidate traffic at zero, attach its reason code and audit event to the record, and return to Tasks 1-2 without weakening authorization.

- [ ] **Step 5: Promote only after explicit user approval**

Run: az containerapp revision list --name ca-dataforge-backend --resource-group rg-dataforge-dev --query "[].{name:name,active:properties.active,traffic:properties.trafficWeight}" -o table

Expected: candidate exists with zero traffic before promotion.

After explicit approval, shift root traffic to validated backend and web revisions, rerun the signed-in journey against root, and record final revision traffic.

- [ ] **Step 6: Commit validation record after evidence exists**

~~~bash
git add docs/validation/production-reliability-conversation-intelligence.md README.md
git commit -m "docs: record conversation reliability validation"
~~~

## Self-Review

### Spec coverage

- Strict owner/member authorization, tenant mismatch, owner normalization, and no email fallback are in Tasks 1-2.
- Client-safe denial recovery and dashboard fallback protection are in Task 3.
- All five conversation modes, policy-after-classification, evidence-change behavior, and no unnecessary full analysis are in Task 4.
- Uniform Markdown, route/evidence/elapsed trace, automatic-analysis separation, and plan artifact linkage are in Task 5.
- Candidate-before-production, signed-in validation, and traffic promotion gates are in Task 6.

### Placeholder scan

Every new type, helper, response payload, test command, expected result, and release gate is defined above.

### Type consistency

WorkspaceAccessDecision is produced by workspace_access_decision and consumed by WorkspaceAuthorizationError and the HTTP envelope. ConversationRoute is attached as RoutingDecision.conversation_route, emitted over SSE, persisted through start_run, and rendered in the existing run timeline. source_run_id, run_id, conversation_id, origin, and persist_messages remain unchanged.

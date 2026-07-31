from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.governance import InMemoryActionRepository
from backend.finops.models import FinOpsRequestEvent
from backend.finops.query import FinOpsQueryService
from backend.finops.remediation import InMemoryRemediationDraftRepository
from backend.finops.repository import InMemoryFinOpsRepository


@pytest.fixture
def repository() -> InMemoryFinOpsRepository:
    value = InMemoryFinOpsRepository()
    value.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_aaaaaaaaaaaa",
                    "occurred_at": datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "agent_id": "coordinator",
                    "deployment": "gpt-5-mini",
                    "status": "succeeded",
                    "tokens": {"total": 10},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {
                        "amount": 0.001,
                        "currency": "USD",
                        "status": "estimated",
                    },
                    "evidence_state": "observed",
                }
            )
        ]
    )
    return value


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    repository: InMemoryFinOpsRepository,
) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.delenv("DF_FINOPS_ACTIONS_ENABLED", raising=False)
    monkeypatch.setattr(
        finops_router,
        "_REMEDIATION_REPOSITORY",
        InMemoryRemediationDraftRepository(),
        raising=False,
    )
    monkeypatch.setattr(
        finops_router,
        "_ACTION_REPOSITORY",
        InMemoryActionRepository(),
    )
    monkeypatch.setattr(
        finops_router,
        "_SQL_REMEDIATION_REPOSITORY",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: FinOpsQueryService(repository),
    )

    def roles(actor: dict[str, object]) -> dict[str, str]:
        actor_id = str(actor.get("actor_id") or "")
        if actor_id == "member-a":
            return {"ws-a": "member"}
        if actor_id == "outsider-a":
            return {"ws-b": "owner"}
        return {"ws-a": "owner", "ws-b": "owner"}

    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", roles)
    monkeypatch.setattr(
        finops_router,
        "_tenant_ref",
        lambda actor: str(actor.get("tenant_id") or ""),
    )
    monkeypatch.setattr(
        finops_router,
        "_actor_ref",
        lambda actor: str(actor.get("actor_id") or ""),
    )
    monkeypatch.setattr(
        finops_router,
        "current_remediation_base_version",
        lambda tenant_ref, workspace_id, action_kind: "cache-policy-v1",
        raising=False,
    )

    def current_opportunity(
        *,
        tenant_ref: str,
        workspace_id: str,
        source_opportunity_id: str,
    ) -> dict[str, object] | None:
        if (
            tenant_ref not in {"tenant-a", "tenant-b"}
            or workspace_id not in {"ws-a", "ws-b"}
            or source_opportunity_id != "opp-cache"
        ):
            return None
        return {
            "opportunity_id": "opp-cache",
            "anomaly_id": "anomaly-cache",
            "policy_type": "cache_hit_rate",
            "evidence_refs": ["req_aaaaaaaaaaaa"],
        }

    monkeypatch.setattr(
        finops_router,
        "_current_remediation_opportunity",
        current_opportunity,
        raising=False,
    )
    return TestClient(app)


@pytest.fixture
def owner_headers() -> dict[str, str]:
    return trusted_headers(actor_id="owner-a", tenant_id="tenant-a")


@pytest.fixture
def second_owner_headers() -> dict[str, str]:
    return trusted_headers(actor_id="owner-b", tenant_id="tenant-a")


@pytest.fixture
def member_headers() -> dict[str, str]:
    return trusted_headers(actor_id="member-a", tenant_id="tenant-a")


def _create(
    client: TestClient,
    headers: dict[str, str],
    *,
    workspace_id: str = "ws-a",
    base_version: str = "cache-policy-v1",
) -> dict[str, object]:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": workspace_id,
            "source_opportunity_id": "opp-cache",
            "base_version": base_version,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["draft"]


def test_create_remediation_accepts_only_opportunity_and_version(
    client: TestClient,
    owner_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )

    assert response.status_code == 201
    draft = response.json()["draft"]
    assert draft["status"] == "draft"
    assert draft["workspace_id"] == "ws-a"
    assert draft["source_opportunity_id"] == "opp-cache"
    assert "tenant_ref" not in draft
    assert "created_by" not in draft
    assert "reviewed_by" not in draft
    assert "owner-a" not in response.text


@pytest.mark.parametrize("field", ("script", "xml", "url", "resource_id"))
def test_create_remediation_rejects_arbitrary_change_payload(
    client: TestClient,
    owner_headers: dict[str, str],
    field: str,
) -> None:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
            field: "remove-all",
        },
        headers=owner_headers,
    )

    assert response.status_code == 422


def test_create_remediation_returns_409_for_stale_base_version(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "current_remediation_base_version",
        lambda tenant_ref, workspace_id, action_kind: "cache-policy-v2",
    )

    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "base version changed"


def test_create_remediation_reloads_current_authorized_server_opportunity(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def load(
        *,
        tenant_ref: str,
        workspace_id: str,
        source_opportunity_id: str,
    ) -> dict[str, object]:
        calls.append((tenant_ref, workspace_id, source_opportunity_id))
        return {
            "opportunity_id": source_opportunity_id,
            "policy_type": "cache_hit_rate",
            "evidence_refs": [],
        }

    monkeypatch.setattr(finops_router, "_current_remediation_opportunity", load)

    _create(client, owner_headers)

    assert calls == [("tenant-a", "ws-a", "opp-cache")]


def test_missing_current_opportunity_returns_404(
    client: TestClient,
    owner_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-missing",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "remediation opportunity not found"


def test_member_cannot_read_or_mutate_remediation_drafts(
    client: TestClient,
    owner_headers: dict[str, str],
    member_headers: dict[str, str],
) -> None:
    draft = _create(client, owner_headers)

    list_response = client.get(
        "/api/finops/remediation-drafts",
        params={"workspace_id": "ws-a"},
        headers=member_headers,
    )
    get_response = client.get(
        f"/api/finops/remediation-drafts/{draft['draft_id']}",
        headers=member_headers,
    )
    review_response = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/review",
        json={"base_revision": draft["revision"]},
        headers=member_headers,
    )

    assert list_response.status_code == 403
    assert get_response.status_code == 403
    assert review_response.status_code == 403


def test_missing_cross_tenant_and_out_of_scope_drafts_return_404(
    client: TestClient,
    owner_headers: dict[str, str],
) -> None:
    draft = _create(client, owner_headers)
    tenant_b_headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-b")
    outsider_headers = trusted_headers(actor_id="outsider-a", tenant_id="tenant-a")

    cross_tenant = client.get(
        f"/api/finops/remediation-drafts/{draft['draft_id']}",
        headers=tenant_b_headers,
    )
    out_of_scope = client.get(
        f"/api/finops/remediation-drafts/{draft['draft_id']}",
        headers=outsider_headers,
    )

    assert cross_tenant.status_code == 404
    assert out_of_scope.status_code == 404


def test_review_and_close_require_current_revision(
    client: TestClient,
    owner_headers: dict[str, str],
    second_owner_headers: dict[str, str],
) -> None:
    draft = _create(client, owner_headers)
    reviewed = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/review",
        json={"base_revision": draft["revision"]},
        headers=second_owner_headers,
    )
    stale_close = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/close",
        json={"base_revision": draft["revision"], "reason": "superseded"},
        headers=second_owner_headers,
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["draft"]["status"] == "reviewed"
    assert stale_close.status_code == 409
    assert stale_close.json()["detail"] == "remediation revision conflict"


def test_promote_creates_draft_action_but_does_not_submit(
    client: TestClient,
    owner_headers: dict[str, str],
    second_owner_headers: dict[str, str],
) -> None:
    draft = _create(client, owner_headers)
    reviewed = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/review",
        json={"base_revision": draft["revision"]},
        headers=second_owner_headers,
    ).json()["draft"]

    response = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/promote",
        json={"base_revision": reviewed["revision"]},
        headers=second_owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["draft"]["status"] == "promoted"
    action = response.json()["action"]
    assert action["status"] == "draft"
    assert "tenant_ref" not in action
    assert "proposed_by" not in action
    assert "approved_by" not in action
    assert "transitions" not in action
    assert response.json()["actions_enabled"] is False


def test_saved_draft_appears_only_in_authorized_workspace_risk_decision(
    client: TestClient,
    owner_headers: dict[str, str],
) -> None:
    draft = _create(client, owner_headers)

    authorized = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a"},
        headers=owner_headers,
    )
    other_workspace = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-b"},
        headers=owner_headers,
    )

    assert authorized.status_code == 200
    assert authorized.json()["drafts"] == [
        {
            "title": draft["title"],
            "summary": draft["summary"],
            "status": "draft",
        }
    ]
    assert other_workspace.status_code == 200
    assert other_workspace.json()["drafts"] == []

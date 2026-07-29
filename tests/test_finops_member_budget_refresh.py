from __future__ import annotations

from typing import Any

import pytest

from backend.finops import member_budget_refresh
from backend.finops.member_budget_evaluator import EvaluationSummary
from backend.finops.normalization import opaque_ref


def test_refresh_flag_off_has_no_factory_or_workspace_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "0")
    monkeypatch.setattr(
        member_budget_refresh,
        "_build_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("must remain disabled")),
    )

    assert member_budget_refresh.main() == 0


def test_server_scope_mapping_is_tenant_isolated_and_keeps_only_opaque_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        member_budget_refresh,
        "_list_workspaces",
        lambda: [{"workspace_id": "ws-a"}, {"workspace_id": "ws-b"}],
    )
    monkeypatch.setattr(
        member_budget_refresh,
        "_workspace_identities",
        lambda workspace_id: [
            {
                "tenant_id": "raw-tenant-a",
                "actor_id": f"raw-{workspace_id}-owner",
                "name": f"{workspace_id} Owner",
                "email": f"{workspace_id}@example.test",
                "role": "owner",
                "status": "active",
            },
            {
                "tenant_id": "raw-tenant-a",
                "actor_id": f"raw-{workspace_id}-member",
                "name": f"{workspace_id} Member",
                "email": "",
                "role": "viewer",
                "status": "active",
            },
        ],
    )

    directory = member_budget_refresh._server_directory(secret="test-secret")
    tenant_ref = opaque_ref("tenant", "raw-tenant-a", secret="test-secret")

    assert directory.scopes == {tenant_ref: ("ws-a", "ws-b")}
    assert set(directory.members) == {tenant_ref}
    assert set(directory.admins) == {tenant_ref}
    assert "raw-tenant-a" not in repr(directory)
    assert "raw-ws-a-owner" not in repr(directory)


@pytest.mark.parametrize(
    "identities",
    (
        [],
        [
            {
                "tenant_id": "tenant-a",
                "actor_id": "actor-a",
                "role": "owner",
                "status": "active",
            },
            {
                "tenant_id": "tenant-b",
                "actor_id": "actor-b",
                "role": "owner",
                "status": "active",
            },
        ],
    ),
)
def test_server_scope_mapping_fails_closed_for_missing_or_ambiguous_tenant(
    monkeypatch: pytest.MonkeyPatch, identities: list[dict[str, str]]
) -> None:
    monkeypatch.setattr(
        member_budget_refresh, "_list_workspaces", lambda: [{"workspace_id": "ws-a"}]
    )
    monkeypatch.setattr(
        member_budget_refresh, "_workspace_identities", lambda _workspace: identities
    )

    with pytest.raises(RuntimeError, match="workspace tenant"):
        member_budget_refresh._server_directory(secret="test-secret")


def test_run_sweep_isolates_email_results_but_returns_nonzero_for_infrastructure_exception() -> None:
    calls: list[str] = []

    class Evaluator:
        def evaluate_tenant(self, tenant_ref: str, **_kwargs: Any) -> EvaluationSummary:
            calls.append(tenant_ref)
            if tenant_ref == "tenant-db-failure":
                raise RuntimeError("database unavailable")
            return EvaluationSummary(failed=1 if tenant_ref == "tenant-email-failure" else 0)

    result = member_budget_refresh.run_sweep(
        Evaluator(),
        {
            "tenant-email-failure": ("ws-a",),
            "tenant-db-failure": ("ws-b",),
            "tenant-ok": ("ws-c",),
        },
    )

    assert calls == ["tenant-email-failure", "tenant-db-failure", "tenant-ok"]
    assert result == 1


def test_refresh_main_builds_durable_runtime_and_bounds_enabled_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    monkeypatch.setenv(
        "DF_FINOPS_PORTAL_URL",
        "https://dataforge.example.test/operations/member-budgets",
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    class Repository:
        def list_enabled_tenants(self) -> tuple[str, ...]:
            return ("tenant-a",)

    class Evaluator:
        def evaluate_tenant(
            self, tenant_ref: str, *, workspace_ids: tuple[str, ...], **_kwargs: Any
        ) -> EvaluationSummary:
            calls.append((tenant_ref, workspace_ids))
            return EvaluationSummary()

    directory = member_budget_refresh.ServerDirectory(
        scopes={"tenant-a": ("ws-a",)},
        members={"tenant-a": {"member-a"}},
        admins={"tenant-a": {"admin-a": "admin@example.test"}},
        names={"tenant-a": {"member-a": "Member A"}},
    )
    monkeypatch.setattr(
        member_budget_refresh,
        "_build_runtime",
        lambda: (Repository(), Evaluator(), directory),
    )

    assert member_budget_refresh.main() == 0
    assert calls == [("tenant-a", ("ws-a",))]


def test_refresh_main_returns_one_for_scope_or_database_enumeration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    monkeypatch.setenv(
        "DF_FINOPS_PORTAL_URL",
        "https://dataforge.example.test/operations/member-budgets",
    )
    monkeypatch.setattr(
        member_budget_refresh,
        "_build_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("raw database secret")),
    )

    assert member_budget_refresh.main() == 1

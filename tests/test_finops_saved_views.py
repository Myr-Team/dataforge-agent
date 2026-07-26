from __future__ import annotations

from backend.finops.saved_views import (
    FinOpsSavedViewService,
    InMemorySavedViewRepository,
    SavedViewCreate,
    csv_cell,
    export_breakdown_csv,
)


def test_saved_view_keeps_only_safe_filters_and_is_tenant_scoped() -> None:
    service = FinOpsSavedViewService(InMemorySavedViewRepository())
    created = service.create(
        tenant_ref="tenant-a",
        actor_ref="actor-a",
        value=SavedViewCreate(
            name="财务月度",
            audience="finance",
            tab="cost",
            filters={
                "department_id": "finance",
                "workspace_id": "ws-a",
                "model": "gpt-5",
            },
        ),
    )

    assert created.filters == {
        "department_id": "finance",
        "workspace_id": "ws-a",
        "model": "gpt-5",
    }
    assert service.list(tenant_ref="tenant-b", authorized_workspace_ids=("ws-a",)) == []
    assert service.list(tenant_ref="tenant-a", authorized_workspace_ids=("ws-b",)) == []
    assert [item.view_id for item in service.list(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
    )] == [created.view_id]


def test_export_has_utf8_bom_neutralizes_formulas_and_omits_private_ids() -> None:
    payload = export_breakdown_csv([
        {
            "key": "=HYPERLINK(\"https://bad\")",
            "requests": 2,
            "tokens": 12,
            "estimated_cost": 0.01,
            "error_rate_pct": 0,
            "p95_latency_ms": 230,
            "actor_ref": "actor-private",
            "provider_response_id": "provider-private",
        }
    ])

    assert payload.startswith(b"\xef\xbb\xbf")
    text = payload.decode("utf-8-sig")
    assert "'=HYPERLINK" in text
    assert "actor-private" not in text
    assert "provider-private" not in text
    assert csv_cell("@cmd") == "'@cmd"

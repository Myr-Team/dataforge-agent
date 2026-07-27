from __future__ import annotations

from backend.finops.ingestion import ingest_completed_run
from backend.finops.evidence_repository import InMemoryEvidenceAliasRepository
from backend.finops.management import FinOpsManagementService, InMemoryManagementRepository
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.sql_pricing import (
    DeploymentPriceMapping,
    InMemoryPriceMappingRepository,
)


def _run() -> dict[str, object]:
    return {
        "run_id": "run-a",
        "workspace_id": "ws-a",
        "status": "completed",
        "completed_at": "2026-07-24T02:00:00Z",
        "actor": {
            "actor_id": "raw-member-id",
            "tenant_id": "raw-tenant-id",
            "email": "must-not-persist@example.com",
        },
        "message": "private prompt",
        "models": [
            {
                "agent": "df-coordinator",
                "deployment": "gpt-5-mini",
                "usage": {"prompt": 10, "completion": 2, "total": 12},
                "response_id": "raw-provider-response",
            }
        ],
    }


def test_completed_run_ingestion_is_disabled_without_sql_flag(monkeypatch) -> None:
    repository = InMemoryFinOpsRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    result = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    assert result == {"status": "disabled", "events": 0}


def test_completed_run_ingestion_writes_only_normalized_events(monkeypatch) -> None:
    repository = InMemoryFinOpsRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")
    result = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    rows = repository.list_events(
        tenant_ref=result["tenant_ref"],
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    assert result["status"] == "ingested"
    assert len(rows) == 1
    serialized = rows[0].model_dump_json()
    assert "raw-member-id" not in serialized
    assert "must-not-persist@example.com" not in serialized
    assert "private prompt" not in serialized
    assert "raw-provider-response" not in serialized


def test_completed_run_ingestion_associates_stable_request_and_run_aliases(
    monkeypatch,
) -> None:
    repository = InMemoryFinOpsRepository()
    aliases = InMemoryEvidenceAliasRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")

    result = ingest_completed_run(
        _run(),
        repository=repository,
        alias_repository=aliases,
        workspace_name_resolver=lambda _workspace_id: "Commerce",
        hmac_secret="secret",
    )
    [event] = repository.list_events(
        tenant_ref=result["tenant_ref"],
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    request_alias = aliases.get(
        tenant_ref=result["tenant_ref"],
        workspace_id="ws-a",
        object_kind="request",
        object_ref=event.request_ref,
    )
    run_alias = aliases.get(
        tenant_ref=result["tenant_ref"],
        workspace_id="ws-a",
        object_kind="run",
        object_ref="run-a",
    )

    assert request_alias is not None
    assert request_alias.display_name == "Commerce · 分析运行 · 7月24日 10:00"
    assert run_alias is not None
    assert run_alias.display_name == request_alias.display_name

    ingest_completed_run(
        _run(),
        repository=repository,
        alias_repository=aliases,
        workspace_name_resolver=lambda _workspace_id: "Commerce Renamed",
        hmac_secret="secret",
    )
    stable_alias = aliases.get(
        tenant_ref=result["tenant_ref"],
        workspace_id="ws-a",
        object_kind="request",
        object_ref=event.request_ref,
    )
    assert stable_alias is not None
    assert stable_alias.workspace_name_snapshot == "Commerce"


def test_completed_run_ingestion_assigns_department_but_never_uses_manual_price_card(
    monkeypatch,
) -> None:
    """A manual (Owner-authored) price card must not price requests.

    The remediation forbids falling back to the manual price list when no
    official-price mapping covers the deployment. The request stays unpriced
    even when an active manual price card exists, while department attribution
    remains intact.
    """
    repository = InMemoryFinOpsRepository()
    management = FinOpsManagementService(InMemoryManagementRepository())
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")

    # The tenant ref is derived inside ingestion. Seed the same opaque scope by
    # first performing a harmless ingest and then configuring its management data.
    first = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    tenant_ref = str(first["tenant_ref"])
    management.create_department(
        tenant_ref=tenant_ref,
        department_id="engineering",
        display_name="Engineering",
        actor_ref="actor-owner",
    )
    management.assign_workspace(
        tenant_ref=tenant_ref,
        workspace_id="ws-a",
        department_id="engineering",
        actor_ref="actor-owner",
    )
    revision = management.create_price_card(
        tenant_ref=tenant_ref,
        actor_ref="actor-author",
        items=[
            {
                "deployment": "gpt-5-mini",
                "input_per_million": 2,
                "output_per_million": 8,
            }
        ],
    )
    management.review_price_card(
        tenant_ref=tenant_ref,
        revision_id=revision.revision_id,
        actor_ref="actor-reviewer",
    )
    management.activate_price_card(
        tenant_ref=tenant_ref,
        revision_id=revision.revision_id,
        actor_ref="actor-reviewer",
        actions_enabled=True,
    )

    ingest_completed_run(
        _run(),
        repository=repository,
        management_service=management,
        hmac_secret="secret",
    )
    [event] = repository.list_events(
        tenant_ref=tenant_ref,
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    assert event.department_id == "engineering"
    assert event.estimated_cost.amount is None
    assert event.estimated_cost.status == "unavailable"
    assert event.estimated_cost.price_card_revision is None


def test_completed_run_ingestion_stays_unpriced_without_official_mapping(
    monkeypatch,
) -> None:
    repository = InMemoryFinOpsRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")
    result = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    [event] = repository.list_events(
        tenant_ref=str(result["tenant_ref"]),
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    assert event.tokens.total == 12
    assert event.estimated_cost.amount is None
    assert event.estimated_cost.status == "unavailable"


def test_completed_run_ingestion_uses_official_deployment_mapping(monkeypatch) -> None:
    repository = InMemoryFinOpsRepository()
    mappings = InMemoryPriceMappingRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")
    first = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    tenant_ref = str(first["tenant_ref"])
    mappings.upsert(
        DeploymentPriceMapping(
            tenant_ref=tenant_ref,
            deployment="gpt-5-mini",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-owner",
        ),
        base_revision=0,
    )

    ingest_completed_run(
        _run(),
        repository=repository,
        price_mapping_repository=mappings,
        hmac_secret="secret",
    )
    [event] = repository.list_events(
        tenant_ref=tenant_ref,
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    from backend.finops.official_pricing import load_official_price_catalog

    assert event.estimated_cost.amount == 0.0000325
    price = load_official_price_catalog().get(
        "azure-openai:gpt-5.1:global-standard:global"
    )
    assert price is not None
    assert event.estimated_cost.price_card_revision == price.revision
    assert (
        event.estimated_cost.official_price_key
        == "azure-openai:gpt-5.1:global-standard:global"
    )
    assert event.estimated_cost.mapping_revision == 1


def test_duplicate_ingestion_preserves_recorded_price_revision(monkeypatch) -> None:
    """Re-ingesting a run must not reprice it under a newer mapping revision."""
    repository = InMemoryFinOpsRepository()
    mappings = InMemoryPriceMappingRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")
    first = ingest_completed_run(_run(), repository=repository, hmac_secret="secret")
    tenant_ref = str(first["tenant_ref"])
    mappings.upsert(
        DeploymentPriceMapping(
            tenant_ref=tenant_ref,
            deployment="gpt-5-mini",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-owner",
        ),
        base_revision=0,
    )
    ingest_completed_run(
        _run(),
        repository=repository,
        price_mapping_repository=mappings,
        hmac_secret="secret",
    )

    # The mapping is corrected to a new revision after the request was recorded.
    mappings.upsert(
        DeploymentPriceMapping(
            tenant_ref=tenant_ref,
            deployment="gpt-5-mini",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=2,
            updated_by_ref="actor-owner",
        ),
        base_revision=1,
    )
    ingest_completed_run(
        _run(),
        repository=repository,
        price_mapping_repository=mappings,
        hmac_secret="secret",
    )
    [event] = repository.list_events(
        tenant_ref=tenant_ref,
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    assert event.estimated_cost.amount == 0.0000325
    assert event.estimated_cost.mapping_revision == 1

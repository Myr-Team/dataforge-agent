from __future__ import annotations

import os
from typing import Any, Callable, Mapping

try:
    from ..lineage_sql import build_lineage_sql_connection_factory
    from ..workspace_store import get_workspace_detail
except ImportError:
    from lineage_sql import build_lineage_sql_connection_factory
    from workspace_store import get_workspace_detail

from .evidence import build_evidence_alias, operation_code_for_event
from .evidence_repository import SqlEvidenceAliasRepository
from .normalization import normalize_run_event, opaque_ref
from .management import FinOpsManagementService
from .official_pricing import estimate_official_cost
from .sql_pricing import SqlPriceMappingRepository
from .sql_management import SqlFinOpsManagementRepository
from .sql_repository import SqlFinOpsRepository


def ingest_completed_run(
    run: Mapping[str, Any],
    *,
    repository: Any | None = None,
    management_service: FinOpsManagementService | None = None,
    price_mapping_repository: Any | None = None,
    alias_repository: Any | None = None,
    workspace_name_resolver: Callable[[str], str] | None = None,
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    if not _enabled("DF_FINOPS_SQL_ENABLED"):
        return {"status": "disabled", "events": 0}
    secret = str(hmac_secret or os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
    tenant_id = str(
        actor.get("tenant_id")
        or os.environ.get("DF_WORKSPACE_OWNER_TENANT_ID")
        or ""
    ).strip()
    if not secret or not tenant_id:
        return {"status": "unavailable", "events": 0}
    tenant_ref = opaque_ref("tenant", tenant_id, secret=secret)
    if repository is None:
        factory = build_lineage_sql_connection_factory()
        target = SqlFinOpsRepository(connection_factory=factory)
        alias_target = alias_repository or SqlEvidenceAliasRepository(
            connection_factory=factory
        )
        manager = management_service or FinOpsManagementService(
            SqlFinOpsManagementRepository(connection_factory=factory)
        )
        price_mappings = price_mapping_repository or SqlPriceMappingRepository(
            connection_factory=factory
        )
    else:
        target = repository
        alias_target = alias_repository
        manager = management_service
        price_mappings = price_mapping_repository
    workspace_id = str(run.get("workspace_id") or "").strip()
    department_id = (
        manager.workspace_department(tenant_ref, workspace_id)
        if manager is not None and workspace_id
        else None
    )
    models = run.get("models") if isinstance(run.get("models"), list) else []
    events = []
    for index in range(len(models)):
        try:
            event = normalize_run_event(
                run,
                model_index=index,
                tenant_id=tenant_ref,
                hmac_secret=secret,
                department_id=department_id,
            )
            if event.estimated_cost.amount is None and event.tokens.observed:
                deployment = event.deployment or event.model
                mapping = (
                    price_mappings.get(tenant_ref, deployment)
                    if price_mappings is not None and deployment
                    else None
                )
                if mapping is not None:
                    estimate = estimate_official_cost(
                        mapping.official_price_key,
                        mapping.mapping_revision,
                        event.tokens,
                    )
                    event = event.model_copy(
                        update={"estimated_cost": estimate}
                    )
            events.append(event)
        except (TypeError, ValueError):
            continue
    if events:
        target.upsert_events(events)
        if alias_target is not None:
            resolver = workspace_name_resolver or _workspace_name
            workspace_name = resolver(workspace_id)
            for event in events:
                operation_code = operation_code_for_event(event)
                alias_target.get_or_create(
                    build_evidence_alias(
                        tenant_ref=event.tenant_ref,
                        workspace_id=event.workspace_id,
                        workspace_name=workspace_name,
                        object_kind="request",
                        object_ref=event.request_ref,
                        operation_code=operation_code,
                        occurred_at=event.occurred_at,
                    )
                )
                if event.run_id:
                    alias_target.get_or_create(
                        build_evidence_alias(
                            tenant_ref=event.tenant_ref,
                            workspace_id=event.workspace_id,
                            workspace_name=workspace_name,
                            object_kind="run",
                            object_ref=event.run_id,
                            operation_code=operation_code,
                            occurred_at=event.occurred_at,
                        )
                    )
    return {
        "status": "ingested",
        "events": len(events),
        "tenant_ref": tenant_ref,
    }


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "0").strip().lower() in {"1", "true", "yes", "on"}


def _workspace_name(workspace_id: str) -> str:
    try:
        detail = get_workspace_detail(workspace_id)
    except (FileNotFoundError, ValueError):
        return ""
    return str(detail.get("name") or "").strip()

from __future__ import annotations

import os
import hashlib
import json
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Literal, Mapping
from urllib.parse import quote, urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from .. import cache_store
    from ..identity import actor_from_request, is_trusted_tenant_identity
    from ..foundry_client import run_agent
    from ..lineage_sql import build_lineage_sql_connection_factory
    from ..run_store import get_run, list_runs
    from ..workspace_authz import active_workspace_role
    from ..workspace_store import list_workspaces
except ImportError:
    import cache_store
    from identity import actor_from_request, is_trusted_tenant_identity
    from foundry_client import run_agent
    from lineage_sql import build_lineage_sql_connection_factory
    from run_store import get_run, list_runs
    from workspace_authz import active_workspace_role
    from workspace_store import list_workspaces

from .normalization import opaque_ref
from .evidence_repository import (
    InMemoryEvidenceAliasRepository,
    SqlEvidenceAliasRepository,
)
from .anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from .agent_inputs import build_finops_agent_input, build_roi_agent_input
from .analysis_agents import FinOpsAnalysisAgent
from .assistant import AssistantRequest, FinOpsAssistantService
from .anomaly_store import (
    AnomalyConflict,
    AnomalyNotFound,
    FinOpsAnomalyService,
    InMemoryAnomalyRepository,
)
from .governance import (
    ActionConflict,
    ActionNotFound,
    ActionPermissionDenied,
    FinOpsActionService,
    InMemoryActionRepository,
)
from .dataforge_clients import (
    DataForgeCachePolicyClient,
    DataForgeModelRouteClient,
    DataForgeWorkspaceConfigStore,
)
from .azure_apim import build_azure_apim_policy_client
from .executors import (
    ApimPolicyExecutor,
    PriceCardActivationExecutor,
    VersionedConfigExecutor,
)
from .management import FinOpsManagementService, InMemoryManagementRepository
from .price_card_client import ManagementPriceCardClient, price_card_version
from .query import FinOpsQuery, FinOpsQueryService
from .query_cache import CachedFinOpsQueryService
from .request_detail import FinOpsRequestDetailService, build_foundry_trace_link
from .insight_repository import InMemoryInsightRepository, SqlInsightRepository
from .insight_service import FinOpsInsightService
from .repository import RunStoreFinOpsRepository
from .sql_repository import SqlFinOpsRepository
from .sql_management import SqlFinOpsManagementRepository
from .sql_governance import SqlFinOpsActionRepository
from .sql_anomalies import SqlFinOpsAnomalyRepository
from .sql_repository import FinOpsPersistenceError


router = APIRouter(prefix="/api/finops", tags=["finops"])
_WORKSPACE_CONFIG_STORE = DataForgeWorkspaceConfigStore()
_DATAFORGE_ACTION_EXECUTORS = {
    "model_route": VersionedConfigExecutor(
        DataForgeModelRouteClient(store=_WORKSPACE_CONFIG_STORE),
        kind="model_route",
    ),
    "cache_policy": VersionedConfigExecutor(
        DataForgeCachePolicyClient(store=_WORKSPACE_CONFIG_STORE),
        kind="cache_policy",
    ),
    "price_card_activation": PriceCardActivationExecutor(
        ManagementPriceCardClient(lambda: get_finops_management_service())
    ),
}
_ACTION_REPOSITORY = InMemoryActionRepository()
_MANAGEMENT_REPOSITORY = InMemoryManagementRepository()
_MANAGEMENT_SERVICE = FinOpsManagementService(_MANAGEMENT_REPOSITORY)
_ANOMALY_REPOSITORY = InMemoryAnomalyRepository()
_ANOMALY_SERVICE = FinOpsAnomalyService(_ANOMALY_REPOSITORY)
_SQL_REPOSITORY: SqlFinOpsRepository | None = None
_SQL_MANAGEMENT_SERVICE: FinOpsManagementService | None = None
_SQL_ACTION_REPOSITORY: SqlFinOpsActionRepository | None = None
_SQL_ANOMALY_SERVICE: FinOpsAnomalyService | None = None
_EVIDENCE_REPOSITORY = InMemoryEvidenceAliasRepository()
_SQL_EVIDENCE_REPOSITORY: SqlEvidenceAliasRepository | None = None
_INSIGHT_REPOSITORY = InMemoryInsightRepository()
_SQL_INSIGHT_REPOSITORY: SqlInsightRepository | None = None


class InsightAnalyzeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    agent_kind: Literal["finops", "roi"]
    workspace_id: str = Field(min_length=1, max_length=160)
    from_value: str = Field(alias="from", min_length=1, max_length=64)
    to_value: str = Field(alias="to", min_length=1, max_length=64)


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "0").strip().lower() in {"1", "true", "yes", "on"}


def get_finops_query_service() -> Any:
    global _SQL_REPOSITORY
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("FinOps HMAC is unavailable")
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_REPOSITORY is None:
            _SQL_REPOSITORY = SqlFinOpsRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        delegate = FinOpsQueryService(_SQL_REPOSITORY)
    else:
        delegate = FinOpsQueryService(
            RunStoreFinOpsRepository(
                run_loader=list_runs,
                hmac_secret=secret,
                department_resolver=lambda tenant_ref, workspace_id: (
                    get_finops_management_service().workspace_department(
                        tenant_ref,
                        workspace_id,
                    )
                ),
            )
        )
    try:
        ttl_seconds = int(os.environ.get("DF_FINOPS_QUERY_CACHE_TTL_SECONDS", "60"))
    except ValueError:
        ttl_seconds = 60
    return CachedFinOpsQueryService(
        delegate,
        cache=cache_store,
        ttl_seconds=ttl_seconds,
    )


def get_finops_action_service() -> FinOpsActionService:
    global _SQL_ACTION_REPOSITORY
    executors = dict(_DATAFORGE_ACTION_EXECUTORS)
    apim_client = build_azure_apim_policy_client()
    if apim_client is not None:
        executors["apim_token_limit"] = ApimPolicyExecutor(apim_client)
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_ACTION_REPOSITORY is None:
            _SQL_ACTION_REPOSITORY = SqlFinOpsActionRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        repository = _SQL_ACTION_REPOSITORY
    else:
        repository = _ACTION_REPOSITORY
    return FinOpsActionService(repository=repository, executors=executors)


def get_finops_management_service() -> FinOpsManagementService:
    global _SQL_MANAGEMENT_SERVICE
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_MANAGEMENT_SERVICE is None:
            _SQL_MANAGEMENT_SERVICE = FinOpsManagementService(
                SqlFinOpsManagementRepository(
                    connection_factory=build_lineage_sql_connection_factory()
                )
            )
        return _SQL_MANAGEMENT_SERVICE
    return _MANAGEMENT_SERVICE


def get_finops_anomaly_service() -> FinOpsAnomalyService:
    global _SQL_ANOMALY_SERVICE
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_ANOMALY_SERVICE is None:
            _SQL_ANOMALY_SERVICE = FinOpsAnomalyService(
                SqlFinOpsAnomalyRepository(
                    connection_factory=build_lineage_sql_connection_factory()
                )
            )
        return _SQL_ANOMALY_SERVICE
    return _ANOMALY_SERVICE


def get_finops_evidence_alias_repository() -> Any:
    global _SQL_EVIDENCE_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_EVIDENCE_REPOSITORY is None:
            _SQL_EVIDENCE_REPOSITORY = SqlEvidenceAliasRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return _SQL_EVIDENCE_REPOSITORY
    return _EVIDENCE_REPOSITORY


def get_finops_insight_service() -> FinOpsInsightService:
    global _SQL_INSIGHT_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_INSIGHT_REPOSITORY is None:
            _SQL_INSIGHT_REPOSITORY = SqlInsightRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        repository = _SQL_INSIGHT_REPOSITORY
    else:
        repository = _INSIGHT_REPOSITORY
    return FinOpsInsightService(
        repository=repository,
        runner=FinOpsAnalysisAgent(
            repository=repository,
            model_runner=run_agent,
        ),
    )


def get_finops_assistant_service() -> FinOpsAssistantService:
    return FinOpsAssistantService(model_runner=run_agent)


def _workspace_name(workspace_id: str) -> str:
    for item in list_workspaces():
        if (
            isinstance(item, Mapping)
            and str(item.get("workspace_id") or "").strip() == workspace_id
        ):
            return str(item.get("name") or "").strip()
    return ""


def _anomaly_evaluation_input(
    events: list[Any],
    *,
    tenant_ref: str,
) -> AnomalyEvaluationInput:
    policies: dict[str, dict[str, Any]] = {}
    for item in get_finops_management_service().list_policies(tenant_ref=tenant_ref):
        if item.status == "enabled":
            policies.setdefault(item.policy_type, item.configuration)
    error = policies.get("error_rate") or {}
    latency = policies.get("p95_latency") or {}
    budget = policies.get("daily_cost_budget") or {}
    coverage = policies.get("apim_coverage") or {}
    cache = policies.get("cache_hit_rate") or {}
    token = policies.get("token_spike") or {}
    unpriced = policies.get("unpriced_requests") or {}
    return AnomalyEvaluationInput(
        events=events,
        daily_budget_usd=budget.get("daily_budget_usd"),
        budget_warning_pct=budget.get("warning_pct", 80),
        budget_critical_pct=budget.get("critical_pct", 100),
        trailing_token_median=_trailing_token_median(
            events,
            lookback_days=int(token.get("lookback_days", 7)),
        ),
        token_spike_multiplier=token.get("multiplier", 2),
        error_rate_threshold_pct=error.get("threshold_pct", 5),
        error_rate_minimum_requests=error.get("minimum_requests", 20),
        error_rate_window_minutes=error.get("window_minutes", 15),
        p95_latency_threshold_ms=latency.get("threshold_ms", 2000),
        p95_latency_minimum_requests=latency.get("minimum_requests", 20),
        p95_latency_window_minutes=latency.get("window_minutes", 15),
        apim_coverage_threshold_pct=coverage.get("minimum_pct", 95),
        unpriced_threshold_pct=unpriced.get("threshold_pct", 5),
        cache_hit_rate_threshold_pct=cache.get("minimum_hit_rate_pct", 20),
        cache_minimum_requests=cache.get("minimum_requests", 20),
    )


def _trailing_token_median(events: list[Any], *, lookback_days: int) -> float | None:
    if not events:
        return None
    latest = max(event.occurred_at for event in events)
    totals: dict[str, int] = {}
    for event in events:
        age_days = (latest.date() - event.occurred_at.date()).days
        if (
            1 <= age_days <= max(1, min(lookback_days, 30))
            and event.occurred_at.hour == latest.hour
            and event.tokens.total is not None
        ):
            key = event.occurred_at.date().isoformat()
            totals[key] = totals.get(key, 0) + int(event.tokens.total)
    return float(median(totals.values())) if totals else None


def _tenant_ref(actor: Mapping[str, Any]) -> str:
    tenant_id = str(actor.get("tenant_id") or "").strip()
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not tenant_id or not secret:
        raise RuntimeError("FinOps tenant scope is unavailable")
    return opaque_ref("tenant", tenant_id, secret=secret)


def _actor_ref(actor: Mapping[str, Any]) -> str:
    actor_id = str(actor.get("actor_id") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not actor_id or not tenant_id or not secret:
        raise RuntimeError("FinOps actor scope is unavailable")
    return opaque_ref("actor", tenant_id, actor_id, secret=secret)


def _authorized_workspace_roles(actor: Mapping[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for item in list_workspaces():
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "").strip()
        if not workspace_id:
            continue
        try:
            role = active_workspace_role(workspace_id, actor)
        except FileNotFoundError:
            continue
        if role:
            roles[workspace_id] = role
    return roles


def _context(
    request: Request,
    *,
    from_value: str | None,
    to_value: str | None,
    department_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
    actor_ref: str | None,
    model: str | None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[FinOpsQueryService, FinOpsQuery, dict[str, str]]:
    if not _enabled("DF_FINOPS_READ_ENABLED"):
        raise HTTPException(status_code=404, detail="FinOps read capability is disabled")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=401, detail="trusted tenant identity is required")
    roles = _authorized_workspace_roles(actor)
    if workspace_id and workspace_id not in roles:
        raise HTTPException(status_code=403, detail="workspace access denied for finops.read")
    selected_ids = (workspace_id,) if workspace_id else tuple(sorted(roles))
    if not selected_ids:
        raise HTTPException(status_code=403, detail="workspace access denied for finops.read")
    try:
        start, end = _window(from_value, to_value)
        query = FinOpsQuery(
            tenant_ref=_tenant_ref(actor),
            authorized_workspace_ids=selected_ids,
            from_value=start,
            to_value=end,
            department_id=department_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_ref=actor_ref,
            model=model,
            cursor=cursor,
            limit=limit,
        )
        service = get_finops_query_service()
    except (RuntimeError, ValueError) as exc:
        if "HMAC" in str(exc) or "scope is unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="FinOps evidence service is unavailable") from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return service, query, roles


def _window(from_value: str | None, to_value: str | None) -> tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _parse_time(to_value) if to_value else now
    start = _parse_time(from_value) if from_value else end - timedelta(days=30)
    if start > end or end - start > timedelta(days=90):
        raise ValueError("FinOps query window must be ordered and no longer than 90 days")
    return _iso(start), _iso(end)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 query window") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _common(
    request: Request,
    from_value: str | None,
    to_value: str | None,
    department_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
    actor_ref: str | None,
    model: str | None,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[FinOpsQueryService, FinOpsQuery, dict[str, str]]:
    return _context(
        request,
        from_value=from_value,
        to_value=to_value,
        department_id=department_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_ref=actor_ref,
        model=model,
        cursor=cursor,
        limit=limit,
    )


@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    workspace_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(
        request,
        from_value,
        to_value,
        None,
        workspace_id,
        None,
        None,
        None,
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.summary.read",
        )
    payload = service.bootstrap(query)
    payload["overview"]["metrics"]["budget"] = _bootstrap_budget(
        query,
        payload["overview"]["metrics"],
    )
    payload["anomalies"] = {
        "items": _bootstrap_anomaly_summaries(query),
        "count": 0,
    }
    payload["anomalies"]["count"] = len(payload["anomalies"]["items"])
    payload["insights"] = _bootstrap_insights(query)
    return payload


def _bootstrap_insights(query: FinOpsQuery) -> dict[str, Any]:
    service = get_finops_insight_service()
    return {
        kind: _public_insight(
            service.latest(
                tenant_ref=query.tenant_ref,
                authorized_workspace_ids=(
                    (query.workspace_id,)
                    if query.workspace_id
                    else query.authorized_workspace_ids
                ),
                agent_kind=kind,
            ),
            include_evidence_refs=False,
        )
        for kind in ("finops", "roi")
    }


def _bootstrap_budget(
    query: FinOpsQuery,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    budget_policy = next(
        (
            item
            for item in get_finops_management_service().list_policies(
                tenant_ref=query.tenant_ref
            )
            if item.status == "enabled" and item.policy_type == "daily_cost_budget"
        ),
        None,
    )
    cost = metrics.get("estimated_cost")
    used_amount = cost.get("amount") if isinstance(cost, Mapping) else None
    if budget_policy is None:
        return {
            "amount": None,
            "used_amount": used_amount,
            "usage_pct": None,
            "status": "unavailable",
            "source": None,
        }
    start = _parse_time(query.from_value)
    end = _parse_time(query.to_value)
    seconds = max(0.0, (end - start).total_seconds())
    days = max(1, int((seconds + 86_399) // 86_400))
    daily_budget = float(budget_policy.configuration["daily_budget_usd"])
    amount = round(daily_budget * days, 8)
    usage_pct = (
        round(float(used_amount) / amount * 100, 4)
        if used_amount is not None and amount > 0
        else None
    )
    cost_status = str(cost.get("status") or "unavailable") if isinstance(cost, Mapping) else "unavailable"
    return {
        "amount": amount,
        "used_amount": used_amount,
        "usage_pct": usage_pct,
        "status": cost_status,
        "source": "daily_cost_budget",
    }


def _bootstrap_anomaly_summaries(query: FinOpsQuery) -> list[dict[str, Any]]:
    titles = {
        "error_rate": "失败率需要关注",
        "p95_latency": "响应时间偏高",
        "daily_cost_budget": "预算使用需要关注",
        "token_spike": "Token 使用异常上升",
        "apim_coverage": "网关治理覆盖不足",
        "unpriced_requests": "部分调用尚未计价",
        "cache_hit_rate": "缓存命中率偏低",
    }
    selected_workspace_ids = (
        (query.workspace_id,) if query.workspace_id else query.authorized_workspace_ids
    )
    managed = get_finops_anomaly_service().list(
        tenant_ref=query.tenant_ref,
        workspace_ids=selected_workspace_ids,
    )
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    open_items = [item for item in managed if item.status == "open"]
    open_items.sort(
        key=lambda item: (
            severity_order.get(item.severity, 3),
            item.updated_at,
        )
    )
    return [
        {
            "policy_type": item.policy_type,
            "severity": item.severity,
            "status": item.status,
            "title": titles.get(item.policy_type, "运营风险需要关注"),
            "observed_value": item.observed_value,
            "threshold_value": item.threshold_value,
            "sample_count": item.sample_count,
            "observed_at": item.updated_at,
            "evidence_state": "observed",
        }
        for item in open_items[:3]
    ]


@router.post("/assistant/query")
async def assistant_query(
    body: AssistantRequest,
    request: Request,
) -> dict[str, Any]:
    context = body.metric_context
    filters = context.filters
    query_service, query, roles = _common(
        request,
        context.window.from_value,
        context.window.to_value,
        filters.department_id,
        filters.workspace_id,
        filters.agent_id,
        filters.actor_ref,
        filters.model,
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.summary.read",
        )
    evidence_payload = build_finops_agent_input(query, query_service)
    response = get_finops_assistant_service().answer(
        request=body,
        evidence_payload=evidence_payload,
    )
    return response.model_dump(mode="json")


@router.get("/filters")
async def filters(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    return service.filters(query)


@router.get("/overview")
async def overview(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    return service.overview(query)


@router.get("/breakdowns")
async def breakdowns(
    request: Request,
    group_by: Literal["department", "workspace", "actor", "agent", "model"] = Query(...),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    if group_by == "actor" and not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="person-level FinOps breakdown requires admin or owner")
    return service.breakdowns(query, group_by)


@router.get("/agents")
async def agents(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    return service.agents(query)


@router.get("/trends")
async def trends(
    request: Request,
    bucket: Literal["hour", "day"] = Query(default="day"),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    return service.trends(query, bucket)


@router.get("/requests")
async def requests_list(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    service, query, _ = _common(
        request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model, cursor=cursor, limit=limit
    )
    return service.requests(query)


@router.get("/requests/{request_ref}")
async def request_detail(
    request_ref: str,
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.request_detail.read",
        )
    detail_service = FinOpsRequestDetailService(
        query_service=service,
        alias_repository=get_finops_evidence_alias_repository(),
        run_loader=get_run,
        workspace_name_resolver=_workspace_name,
    )
    payload = detail_service.build(query, request_ref, can_trace=True)
    if payload is None:
        raise HTTPException(status_code=404, detail="FinOps request not found")
    link = _azure_monitor_link(payload.get("technical_refs"), query)
    if link:
        payload.setdefault("links", {})["azure_monitor"] = link
    trace_refs = payload.get("technical_refs")
    trace_id = (
        str(trace_refs.get("trace_id") or "").strip()
        if isinstance(trace_refs, Mapping)
        else ""
    )
    trace_link = build_foundry_trace_link(
        str(os.environ.get("DF_FINOPS_FOUNDRY_TRACE_LINK_TEMPLATE") or ""),
        trace_id,
    )
    if trace_link:
        payload.setdefault("links", {})["foundry_trace"] = trace_link
    return payload


def _azure_monitor_link(value: Any, query: FinOpsQuery) -> str | None:
    event = value if isinstance(value, dict) else {}
    correlation_id = str(event.get("apim_correlation_id") or "").strip().lower()
    template = str(os.environ.get("DF_FINOPS_AZURE_MONITOR_LINK_TEMPLATE") or "").strip()
    if not correlation_id or "{correlation_id}" not in template:
        return None
    parsed = urlparse(template)
    if parsed.scheme != "https" or parsed.hostname not in {"portal.azure.com", "portal.azure.cn"}:
        return None
    replacements = {
        "{correlation_id}": quote(correlation_id, safe=""),
        "{from}": quote(query.from_value, safe=""),
        "{to}": quote(query.to_value, safe=""),
    }
    result = template
    for key, replacement in replacements.items():
        result = result.replace(key, replacement)
    return result if "{" not in result and "}" not in result else None


@router.get("/insights")
async def insights(
    request: Request,
    agent_kind: Literal["finops", "roi"] | None = Query(default=None),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    query_service, query, roles = _common(
        request,
        from_value,
        to_value,
        department_id,
        workspace_id,
        agent_id,
        actor_ref,
        model,
        cursor=cursor,
        limit=limit,
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.summary.read",
        )
    page = get_finops_insight_service().list(
        tenant_ref=query.tenant_ref,
        authorized_workspace_ids=(
            (query.workspace_id,)
            if query.workspace_id
            else query.authorized_workspace_ids
        ),
        agent_kind=agent_kind,
        cursor=cursor,
        limit=limit,
    )
    payload = query_service.requests(query)
    payload.pop("items", None)
    payload.update(
        {
            "items": [
                _public_insight(item, include_evidence_refs=True)
                for item in page.items
            ],
            "count": page.count,
            "next_cursor": page.next_cursor,
        }
    )
    return payload


@router.post("/insights/analyze", status_code=202)
async def analyze_insight(
    body: InsightAnalyzeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    query_service, query, roles = _common(
        request,
        body.from_value,
        body.to_value,
        None,
        body.workspace_id,
        None,
        None,
        None,
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        permission = (
            "finops.roi.read"
            if body.agent_kind == "roi"
            else "finops.cost.read"
        )
        raise HTTPException(
            status_code=403,
            detail=f"workspace access denied for {permission}",
        )
    input_payload = _manual_insight_input(
        agent_kind=body.agent_kind,
        query=query,
        query_service=query_service,
    )
    source_revision = hashlib.sha256(
        json.dumps(
            input_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    service = get_finops_insight_service()
    selected_workspace_ids = (
        (query.workspace_id,)
        if query.workspace_id
        else query.authorized_workspace_ids
    )
    fingerprint = service.fingerprint(
        agent_kind=body.agent_kind,
        tenant_ref=query.tenant_ref,
        workspace_ids=selected_workspace_ids,
        trigger_type="manual",
        trigger_ref=f"manual:{body.workspace_id}:{body.agent_kind}",
        source_revision=source_revision,
    )
    existing = service.by_fingerprint(
        agent_kind=body.agent_kind,
        tenant_ref=query.tenant_ref,
        trigger_fingerprint=fingerprint,
    )
    if existing is not None:
        return {
            "status": "existing",
            "agent_kind": body.agent_kind,
            "trigger_fingerprint": fingerprint,
        }
    background_tasks.add_task(
        service.analyze,
        agent_kind=body.agent_kind,
        tenant_ref=query.tenant_ref,
        workspace_ids=selected_workspace_ids,
        window={"from": query.from_value, "to": query.to_value},
        trigger_type="manual",
        trigger_ref=f"manual:{body.workspace_id}:{body.agent_kind}",
        source_revision=source_revision,
        input_payload=input_payload,
    )
    return {
        "status": "scheduled",
        "agent_kind": body.agent_kind,
        "trigger_fingerprint": fingerprint,
    }


def _manual_insight_input(
    *,
    agent_kind: Literal["finops", "roi"],
    query: FinOpsQuery,
    query_service: Any,
) -> dict[str, Any]:
    if agent_kind == "finops":
        selected_workspace_ids = (
            (query.workspace_id,)
            if query.workspace_id
            else query.authorized_workspace_ids
        )
        anomalies = get_finops_anomaly_service().list(
            tenant_ref=query.tenant_ref,
            workspace_ids=selected_workspace_ids,
        )
        active_price_card = next(
            (
                item
                for item in get_finops_management_service().list_price_cards(
                    tenant_ref=query.tenant_ref
                )
                if item.status == "active"
            ),
            None,
        )
        return build_finops_agent_input(
            query,
            query_service,
            anomalies=[
                item.model_dump(mode="json", exclude={"tenant_ref"})
                for item in anomalies
            ],
            price_card_revision=(
                active_price_card.revision_id if active_price_card else None
            ),
        )
    if not query.workspace_id:
        return {
            "status": "insufficient_data",
            "agent_kind": "roi",
            "workspace_id": "",
            "window": {"from": query.from_value, "to": query.to_value},
            "evidence_refs": [],
            "evidence_gaps": ["ROI 分析需要选择单个工作区"],
        }
    try:
        try:
            from ..control_plane import workspace_roi_snapshot
            from ..outcome_store import list_outcome_events
        except ImportError:
            from control_plane import workspace_roi_snapshot
            from outcome_store import list_outcome_events
        snapshot = workspace_roi_snapshot(
            query.workspace_id,
            query.from_value,
            query.to_value,
        )
        outcomes = list_outcome_events(query.workspace_id)
    except Exception:
        return {
            "status": "insufficient_data",
            "agent_kind": "roi",
            "workspace_id": query.workspace_id,
            "window": {"from": query.from_value, "to": query.to_value},
            "evidence_refs": [],
            "evidence_gaps": ["已验证结果事件不足"],
        }
    return build_roi_agent_input(
        query.workspace_id,
        {"from": query.from_value, "to": query.to_value},
        snapshot,
        outcomes,
    )


def _public_insight(
    value: Any,
    *,
    include_evidence_refs: bool,
) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = value.model_dump(
        mode="json",
        by_alias=True,
        exclude={"tenant_ref", "trigger_fingerprint"},
    )
    if not include_evidence_refs:
        payload["evidence_count"] = len(payload.get("evidence_refs") or [])
        payload.pop("evidence_refs", None)
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict):
                finding["evidence_count"] = len(finding.get("evidence_refs") or [])
                finding.pop("evidence_refs", None)
    return payload


def _empty_collection(
    request: Request,
    from_value: str | None,
    to_value: str | None,
    department_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
    actor_ref: str | None,
    model: str | None,
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    payload = service.requests(query)
    payload.pop("items", None)
    payload.update({"items": [], "count": 0, "next_cursor": None})
    return payload


@router.get("/anomalies")
async def anomalies(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    events = service.events(query)
    findings = evaluate_default_anomalies(
        _anomaly_evaluation_input(events, tenant_ref=query.tenant_ref)
    )
    selected_workspace_ids = (
        (query.workspace_id,) if query.workspace_id else query.authorized_workspace_ids
    )
    managed = get_finops_anomaly_service().reconcile(
        tenant_ref=query.tenant_ref,
        findings=findings,
        scope_workspace_ids=selected_workspace_ids,
    )
    selected = set(selected_workspace_ids)
    managed = [
        item
        for item in managed
        if set(item.workspace_ids).issubset(selected)
    ]
    payload = service.requests(query)
    payload.pop("items", None)
    payload.update(
        {
            "items": [item.model_dump(mode="json", exclude={"tenant_ref"}) for item in managed],
            "count": len(managed),
            "next_cursor": None,
        }
    )
    return payload


@router.get("/recommendations")
async def recommendations(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    events = service.events(query)
    findings = evaluate_default_anomalies(
        _anomaly_evaluation_input(events, tenant_ref=query.tenant_ref)
    )
    items = [
        {
            "recommendation_id": f"rec_{item.anomaly_id.removeprefix('anomaly_')}",
            "source_anomaly_id": item.anomaly_id,
            "severity": item.severity,
            "policy_type": item.policy_type,
            "recommendation": item.recommendation,
            "execution_mode": "approval_required",
        }
        for item in findings
    ]
    payload = service.requests(query)
    payload.pop("items", None)
    payload.update(
        {
            "items": items,
            "count": len(items),
            "next_cursor": None,
            "actions_enabled": _enabled("DF_FINOPS_ACTIONS_ENABLED"),
        }
    )
    return payload


@router.get("/actions")
async def actions(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    items = [
        action.model_dump(mode="json")
        for action in get_finops_action_service().list(tenant_ref=query.tenant_ref)
        if (
            not action.payload.get("workspace_id")
            or action.payload.get("workspace_id") in roles
        )
        and (not workspace_id or action.payload.get("workspace_id") == workspace_id)
    ]
    payload = service.requests(query)
    payload.pop("items", None)
    payload.update(
        {
            "items": items,
            "count": len(items),
            "next_cursor": None,
            "actions_enabled": _enabled("DF_FINOPS_ACTIONS_ENABLED"),
        }
    )
    return payload


def _write_context(request: Request, *, workspace_id: str | None = None) -> tuple[str, str, dict[str, str]]:
    if not _enabled("DF_FINOPS_READ_ENABLED"):
        raise HTTPException(status_code=404, detail="FinOps capability is disabled")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=401, detail="trusted tenant identity is required")
    roles = _authorized_workspace_roles(actor)
    if workspace_id and workspace_id not in roles:
        raise HTTPException(status_code=403, detail="workspace access denied for finops.write")
    relevant_roles = [roles[workspace_id]] if workspace_id else list(roles.values())
    if not relevant_roles or not any(role in {"owner", "admin"} for role in relevant_roles):
        raise HTTPException(status_code=403, detail="FinOps management requires admin or owner")
    try:
        return _tenant_ref(actor), _actor_ref(actor), roles
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="FinOps evidence service is unavailable") from exc


def _require_admin_scope(
    roles: Mapping[str, str],
    workspace_ids: list[str] | tuple[str, ...],
) -> None:
    targets = {str(value).strip() for value in workspace_ids if str(value).strip()}
    if any(roles.get(workspace_id) not in {"owner", "admin"} for workspace_id in targets):
        raise HTTPException(status_code=403, detail="workspace access denied for finops.write")


def _action_response(action: Any) -> dict[str, Any]:
    return {
        "action": action.model_dump(mode="json"),
        "actions_enabled": _enabled("DF_FINOPS_ACTIONS_ENABLED"),
    }


def _action_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ActionNotFound):
        return HTTPException(status_code=404, detail="FinOps action not found")
    if isinstance(exc, ActionPermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ActionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="FinOps action failed")


def _action_write_context(
    request: Request,
    action_id: str,
) -> tuple[str, str, dict[str, str], Any]:
    tenant_ref, actor_ref, roles = _write_context(request)
    try:
        action = get_finops_action_service().get(
            tenant_ref=tenant_ref,
            action_id=action_id,
        )
    except Exception as exc:
        raise _action_error(exc) from exc
    workspace_id = str(action.payload.get("workspace_id") or "").strip()
    _require_admin_scope(roles, [workspace_id] if workspace_id else [])
    return tenant_ref, actor_ref, roles, action


@router.post("/actions", status_code=201)
async def create_action(body: dict[str, Any], request: Request) -> dict[str, Any]:
    raw = body if isinstance(body, dict) else {}
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    workspace_id = str(payload.get("workspace_id") or "").strip() or None
    tenant_ref, actor_ref, _ = _write_context(request, workspace_id=workspace_id)
    try:
        action = get_finops_action_service().create(
            tenant_ref=tenant_ref,
            action_type=str(raw.get("action_type") or ""),
            payload=payload,
            actor_ref=actor_ref,
            actor_kind="human",
        )
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


@router.post("/actions/{action_id}/submit")
async def submit_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().submit(action_id, tenant_ref=tenant_ref, actor_ref=actor_ref)
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().approve(action_id, tenant_ref=tenant_ref, actor_ref=actor_ref)
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


@router.post("/actions/{action_id}/execute")
async def execute_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().execute(
            action_id,
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            actions_enabled=_enabled("DF_FINOPS_ACTIONS_ENABLED"),
        )
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


@router.post("/actions/{action_id}/verify")
async def verify_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().verify(action_id, tenant_ref=tenant_ref, actor_ref=actor_ref)
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


@router.post("/actions/{action_id}/rollback")
async def rollback_action(action_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, action_record = _action_write_context(request, action_id)
    reason = str((body if isinstance(body, dict) else {}).get("reason") or "").strip()
    workspace_id = str(action_record.payload.get("workspace_id") or "").strip()
    owner = (
        roles.get(workspace_id) == "owner"
        if workspace_id
        else any(role == "owner" for role in roles.values())
    )
    try:
        action = get_finops_action_service().rollback(
            action_id,
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            reason=reason,
            owner=owner,
        )
    except Exception as exc:
        raise _action_error(exc) from exc
    return _action_response(action)


def _management_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="FinOps management record not found")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, FinOpsPersistenceError):
        return HTTPException(status_code=503, detail="FinOps persistence service is unavailable")
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="FinOps management operation failed")


def _price_card_response(revision: Any) -> dict[str, Any]:
    payload = revision.model_dump(mode="json")
    payload["base_version"] = price_card_version(payload)
    return payload


def _anomaly_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, AnomalyNotFound):
        return HTTPException(status_code=404, detail="FinOps anomaly not found")
    if isinstance(exc, AnomalyConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FinOpsPersistenceError):
        return HTTPException(status_code=503, detail="FinOps persistence service is unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="FinOps anomaly operation failed")


@router.post("/anomalies/{anomaly_id}/acknowledge")
async def acknowledge_anomaly(anomaly_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _write_context(request)
    try:
        service = get_finops_anomaly_service()
        current = service.get(tenant_ref=tenant_ref, anomaly_id=anomaly_id)
        _require_admin_scope(roles, current.workspace_ids)
        anomaly = service.acknowledge(
            tenant_ref=tenant_ref,
            anomaly_id=anomaly_id,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _anomaly_error(exc) from exc
    return {"anomaly": anomaly.model_dump(mode="json", exclude={"tenant_ref"})}


@router.post("/anomalies/{anomaly_id}/suppress")
async def suppress_anomaly(anomaly_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    try:
        service = get_finops_anomaly_service()
        current = service.get(tenant_ref=tenant_ref, anomaly_id=anomaly_id)
        _require_admin_scope(roles, current.workspace_ids)
        anomaly = service.suppress(
            tenant_ref=tenant_ref,
            anomaly_id=anomaly_id,
            actor_ref=actor_ref,
            reason=str(raw.get("reason") or ""),
            until=str(raw.get("until") or "").strip() or None,
        )
    except Exception as exc:
        raise _anomaly_error(exc) from exc
    return {"anomaly": anomaly.model_dump(mode="json", exclude={"tenant_ref"})}


@router.get("/departments")
async def list_departments(request: Request) -> dict[str, Any]:
    tenant_ref, _, _ = _write_context(request)
    items = get_finops_management_service().list_departments(tenant_ref=tenant_ref)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.post("/departments", status_code=201)
async def create_department(body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    try:
        department = get_finops_management_service().create_department(
            tenant_ref=tenant_ref,
            department_id=str(raw.get("department_id") or ""),
            display_name=str(raw.get("display_name") or ""),
            cost_center=str(raw.get("cost_center") or "").strip() or None,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"department": department.model_dump(mode="json")}


@router.patch("/departments/{department_id}")
async def update_department(department_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    try:
        department = get_finops_management_service().update_department(
            tenant_ref=tenant_ref,
            department_id=department_id,
            actor_ref=actor_ref,
            display_name=raw.get("display_name"),
            cost_center=raw.get("cost_center"),
            status=raw.get("status"),
            base_version=raw.get("base_version"),
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"department": department.model_dump(mode="json")}


@router.delete("/departments/{department_id}")
async def archive_department(department_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    service = get_finops_management_service()
    current = next((item for item in service.list_departments(tenant_ref=tenant_ref) if item.department_id == department_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="FinOps department not found")
    try:
        department = service.update_department(
            tenant_ref=tenant_ref,
            department_id=department_id,
            actor_ref=actor_ref,
            status="archived",
            base_version=current.version,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"department": department.model_dump(mode="json")}


@router.put("/workspace-assignments/{workspace_id}")
async def assign_workspace_department(workspace_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request, workspace_id=workspace_id)
    raw = body if isinstance(body, dict) else {}
    department_id = str(raw.get("department_id") or "").strip() or None
    try:
        assignment = get_finops_management_service().assign_workspace(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            department_id=department_id,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"assignment": assignment}


@router.get("/price-cards")
async def list_price_cards(request: Request) -> dict[str, Any]:
    tenant_ref, _, _ = _write_context(request)
    items = get_finops_management_service().list_price_cards(tenant_ref=tenant_ref)
    return {"items": [_price_card_response(item) for item in items], "count": len(items)}


@router.post("/price-cards", status_code=201)
async def create_price_card(body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    try:
        revision = get_finops_management_service().create_price_card(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            items=items,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"price_card": _price_card_response(revision)}


@router.post("/price-cards/{revision_id}/review")
async def review_price_card(revision_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    try:
        revision = get_finops_management_service().review_price_card(
            tenant_ref=tenant_ref,
            revision_id=revision_id,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"price_card": _price_card_response(revision)}


@router.post("/price-cards/{revision_id}/activate")
async def activate_price_card(revision_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    try:
        revision = get_finops_management_service().activate_price_card(
            tenant_ref=tenant_ref,
            revision_id=revision_id,
            actor_ref=actor_ref,
            actions_enabled=_enabled("DF_FINOPS_ACTIONS_ENABLED"),
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"price_card": _price_card_response(revision)}


@router.get("/policies")
async def list_policies(request: Request) -> dict[str, Any]:
    tenant_ref, _, _ = _write_context(request)
    items = get_finops_management_service().list_policies(tenant_ref=tenant_ref)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.post("/policies", status_code=201)
async def create_policy(body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    configuration = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}
    try:
        policy = get_finops_management_service().create_policy(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            policy_type=str(raw.get("policy_type") or ""),
            configuration=configuration,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"policy": policy.model_dump(mode="json")}


@router.patch("/policies/{policy_id}")
async def update_policy(policy_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    raw = body if isinstance(body, dict) else {}
    configuration = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}
    try:
        policy = get_finops_management_service().update_policy(
            tenant_ref=tenant_ref,
            policy_id=policy_id,
            actor_ref=actor_ref,
            configuration=configuration,
            status=str(raw.get("status") or "enabled"),
            base_version=int(raw.get("base_version") or 0),
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"policy": policy.model_dump(mode="json")}


@router.delete("/policies/{policy_id}")
async def disable_policy(policy_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(request)
    service = get_finops_management_service()
    current = next(
        (
            item
            for item in service.list_policies(tenant_ref=tenant_ref)
            if item.policy_id == policy_id
        ),
        None,
    )
    if current is None:
        raise HTTPException(status_code=404, detail="FinOps policy not found")
    try:
        policy = service.update_policy(
            tenant_ref=tenant_ref,
            policy_id=policy_id,
            actor_ref=actor_ref,
            configuration=current.configuration,
            status="disabled",
            base_version=current.version,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"policy": policy.model_dump(mode="json")}

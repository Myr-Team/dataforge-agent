from __future__ import annotations

import os
import hashlib
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterator, Literal, Mapping
from urllib.parse import quote, urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from .. import cache_store
    from ..identity import actor_from_request, is_trusted_tenant_identity
    from ..foundry_client import run_agent
    from ..lineage_sql import build_lineage_sql_connection_factory
    from ..run_store import get_run, list_runs
    from ..workspace_authz import active_workspace_role
    from ..workspace_store import list_workspaces, load_workspace_model_configuration
    from ..control_plane import workspace_cost_value_snapshot, workspace_roi_snapshot
    from ..model_policy import (
        SelectedTextRoute,
        model_route_scope,
        select_text_route_record,
        workspace_model_policy_scope,
    )
except ImportError:
    import cache_store
    from identity import actor_from_request, is_trusted_tenant_identity
    from foundry_client import run_agent
    from lineage_sql import build_lineage_sql_connection_factory
    from run_store import get_run, list_runs
    from workspace_authz import active_workspace_role
    from workspace_store import list_workspaces, load_workspace_model_configuration
    from control_plane import workspace_cost_value_snapshot, workspace_roi_snapshot
    from model_policy import (
        SelectedTextRoute,
        model_route_scope,
        select_text_route_record,
        workspace_model_policy_scope,
    )

from .normalization import canonical_actor_ref, canonical_tenant_ref
from .evidence import build_evidence_alias, operation_code_for_event
from .evidence_selection import (
    EvidenceSet,
    select_metric_evidence,
    select_policy_evidence,
)
from .evidence_repository import (
    InMemoryEvidenceAliasRepository,
    SqlEvidenceAliasRepository,
)
from .anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from .agent_inputs import (
    build_finops_agent_input,
    build_finops_assistant_input,
    build_roi_agent_input,
)
from .analysis_agents import FinOpsAnalysisAgent, analysis_agent_id
from .assistant import AssistantRequest, AssistantTurn, FinOpsAssistantService
from .assistant_bootstrap import AssistantBootstrapCache
from .assistant_store import (
    AssistantConversationExpired,
    AssistantMessage,
    AssistantScope,
    InMemoryAssistantConversationStore,
)
from .sql_assistant import SqlAssistantConversationStore
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
from .remediation import (
    FinOpsRemediationService,
    InMemoryRemediationDraftRepository,
    REMEDIATION_TEMPLATE_VERSION,
    RemediationConflict,
    RemediationNotFound,
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
from .official_pricing import (
    load_official_price_catalog,
    official_price_supports_call_classes,
)
from .sql_pricing import (
    DeploymentPriceMapping,
    InMemoryPriceMappingRepository,
    PriceMappingConflict,
    SqlPriceMappingRepository,
)
from .gateway_unmatched import SqlGatewayUnmatchedRepository
from .query import FinOpsQuery, FinOpsQueryService
from .models import FinOpsRequestEvent
from .cache_namespace import FinOpsCacheNamespace
from .query_cache import CachedFinOpsQueryService, FinOpsCacheBusy
from .request_detail import FinOpsRequestDetailService, build_foundry_trace_link
from .planning import (
    BudgetDefinition,
    FinOpsPlanningService,
    InMemoryPlanningRepository,
)
from .saved_views import (
    FinOpsSavedViewService,
    InMemorySavedViewRepository,
    SavedViewCreate,
    export_breakdown_csv,
)
from .sql_planning import SqlFinOpsPlanningRepository
from .roi_economics import build_roi_economics
from .opportunities import build_opportunity_queue
from .decision_service import build_risk_decision, build_roi_decision
from .demo_workspace_seed import demo_operations_model_policy
from .insight_repository import InMemoryInsightRepository, SqlInsightRepository
from .insight_service import FinOpsInsightService
from .repository import RunStoreFinOpsRepository
from .sql_repository import SqlFinOpsRepository
from .sql_management import SqlFinOpsManagementRepository
from .sql_governance import SqlFinOpsActionRepository
from .sql_remediation import SqlRemediationDraftRepository
from .sql_anomalies import SqlFinOpsAnomalyRepository
from .sql_repository import FinOpsPersistenceError
from .sql_rollups import SqlFinOpsRollupRepository
from .member_budget_repository import InMemoryMemberBudgetRepository
from .sql_member_budgets import SqlMemberBudgetRepository
from .risk_scans import (
    FinOpsRiskScan,
    InMemoryRiskScanRepository,
    RiskScanFinding,
    RiskScanScope,
    RiskScanService,
)
from .sql_risk_scans import SqlRiskScanRepository


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
_REMEDIATION_REPOSITORY = InMemoryRemediationDraftRepository()
_MANAGEMENT_REPOSITORY = InMemoryManagementRepository()
_MANAGEMENT_SERVICE = FinOpsManagementService(_MANAGEMENT_REPOSITORY)
_ANOMALY_REPOSITORY = InMemoryAnomalyRepository()
_ANOMALY_SERVICE = FinOpsAnomalyService(_ANOMALY_REPOSITORY)
_SQL_REPOSITORY: SqlFinOpsRepository | None = None
_SQL_MANAGEMENT_SERVICE: FinOpsManagementService | None = None
_SQL_ACTION_REPOSITORY: SqlFinOpsActionRepository | None = None
_SQL_REMEDIATION_REPOSITORY: SqlRemediationDraftRepository | None = None
_SQL_ANOMALY_SERVICE: FinOpsAnomalyService | None = None
_EVIDENCE_REPOSITORY = InMemoryEvidenceAliasRepository()
_SQL_EVIDENCE_REPOSITORY: SqlEvidenceAliasRepository | None = None
_INSIGHT_REPOSITORY = InMemoryInsightRepository()
_SQL_INSIGHT_REPOSITORY: SqlInsightRepository | None = None
_PLANNING_REPOSITORY = InMemoryPlanningRepository()
_SAVED_VIEW_REPOSITORY = InMemorySavedViewRepository()
_SQL_PLANNING_REPOSITORY: SqlFinOpsPlanningRepository | None = None
_PRICE_MAPPING_REPOSITORY = InMemoryPriceMappingRepository()
_SQL_PRICE_MAPPING_REPOSITORY: SqlPriceMappingRepository | None = None
_SQL_GATEWAY_UNMATCHED_REPOSITORY: SqlGatewayUnmatchedRepository | None = None
_SQL_ROLLUP_REPOSITORY: SqlFinOpsRollupRepository | None = None
_MEMBER_BUDGET_REPOSITORY = InMemoryMemberBudgetRepository()
_SQL_MEMBER_BUDGET_REPOSITORY: SqlMemberBudgetRepository | None = None
_ASSISTANT_STORE = InMemoryAssistantConversationStore()
_SQL_ASSISTANT_STORE: SqlAssistantConversationStore | None = None
_ASSISTANT_BOOTSTRAP_CACHE = AssistantBootstrapCache()
_RISK_SCAN_REPOSITORY = InMemoryRiskScanRepository()
_SQL_RISK_SCAN_REPOSITORY: SqlRiskScanRepository | None = None


class InsightAnalyzeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    agent_kind: Literal["finops", "roi"]
    workspace_id: str = Field(min_length=1, max_length=160)
    from_value: str = Field(alias="from", min_length=1, max_length=64)
    to_value: str = Field(alias="to", min_length=1, max_length=64)


class PriceMappingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    official_price_key: str = Field(min_length=3, max_length=240)
    base_revision: int = Field(ge=0)


class AssistantConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=160)
    title: str = Field(default="新会话", min_length=1, max_length=120)


class RemediationDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=160)
    source_opportunity_id: str = Field(min_length=1, max_length=128)
    base_version: str = Field(min_length=1, max_length=128)


class RemediationTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=512)


class RiskScanCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    workspace_id: str = Field(min_length=1, max_length=160)
    from_value: str | None = Field(default=None, alias="from", max_length=64)
    to_value: str | None = Field(default=None, alias="to", max_length=64)
    department_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    actor_ref: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=160)


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "0").strip().lower() in {"1", "true", "yes", "on"}


def get_finops_query_service() -> Any:
    global _SQL_REPOSITORY, _SQL_ROLLUP_REPOSITORY
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("FinOps HMAC is unavailable")
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        global _SQL_GATEWAY_UNMATCHED_REPOSITORY
        if _SQL_REPOSITORY is None:
            _SQL_REPOSITORY = SqlFinOpsRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        if _SQL_GATEWAY_UNMATCHED_REPOSITORY is None:
            _SQL_GATEWAY_UNMATCHED_REPOSITORY = SqlGatewayUnmatchedRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        delegate = FinOpsQueryService(
            _SQL_REPOSITORY,
            gateway_unmatched_repository=_SQL_GATEWAY_UNMATCHED_REPOSITORY,
            rollup_repository=get_finops_rollup_repository(),
        )
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
    return CachedFinOpsQueryService(
        delegate,
        cache=cache_store,
        namespace=get_finops_cache_namespace(),
    )


def get_finops_cache_namespace() -> FinOpsCacheNamespace:
    return FinOpsCacheNamespace(cache_store)


def _bump_finops_domains(
    tenant_ref: str,
    workspace_ids: tuple[str, ...] | list[str],
    domains: tuple[str, ...],
) -> None:
    """Best-effort targeted invalidation after a successful durable write."""
    namespace = get_finops_cache_namespace()
    for workspace_id in sorted({str(item or "").strip() for item in workspace_ids}):
        if not workspace_id:
            continue
        try:
            namespace.bump(tenant_ref, workspace_id, domains)
        except Exception:
            # Redis is an acceleration layer; a durable write must remain
            # successful when its namespace revision cannot be advanced.
            continue


def get_finops_rollup_repository() -> SqlFinOpsRollupRepository:
    """Return the SQL rollup reader only when SQL FinOps is enabled."""
    global _SQL_ROLLUP_REPOSITORY
    if not _enabled("DF_FINOPS_SQL_ENABLED"):
        raise RuntimeError("FinOps SQL rollups are disabled")
    if _SQL_ROLLUP_REPOSITORY is None:
        _SQL_ROLLUP_REPOSITORY = SqlFinOpsRollupRepository(
            connection_factory=build_lineage_sql_connection_factory()
        )
    return _SQL_ROLLUP_REPOSITORY


def get_finops_member_budget_repository() -> Any:
    global _SQL_MEMBER_BUDGET_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_MEMBER_BUDGET_REPOSITORY is None:
            _SQL_MEMBER_BUDGET_REPOSITORY = SqlMemberBudgetRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return _SQL_MEMBER_BUDGET_REPOSITORY
    return _MEMBER_BUDGET_REPOSITORY


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


def current_remediation_base_version(
    tenant_ref: str,
    workspace_id: str,
    action_kind: str,
) -> str:
    del tenant_ref
    if action_kind != "cache_policy":
        raise ValueError("typed remediation version resolver is unavailable")
    return DataForgeCachePolicyClient(
        store=_WORKSPACE_CONFIG_STORE
    ).current_version(workspace_id)


def get_finops_remediation_service() -> FinOpsRemediationService:
    global _SQL_REMEDIATION_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_REMEDIATION_REPOSITORY is None:
            _SQL_REMEDIATION_REPOSITORY = SqlRemediationDraftRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        repository = _SQL_REMEDIATION_REPOSITORY
    else:
        repository = _REMEDIATION_REPOSITORY
    return FinOpsRemediationService(
        repository=repository,
        action_service=get_finops_action_service(),
        version_resolver=current_remediation_base_version,
    )


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


def get_finops_price_mapping_repository() -> Any:
    global _SQL_PRICE_MAPPING_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_PRICE_MAPPING_REPOSITORY is None:
            _SQL_PRICE_MAPPING_REPOSITORY = SqlPriceMappingRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return _SQL_PRICE_MAPPING_REPOSITORY
    return _PRICE_MAPPING_REPOSITORY


def get_finops_assistant_store() -> Any:
    global _SQL_ASSISTANT_STORE
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_ASSISTANT_STORE is None:
            _SQL_ASSISTANT_STORE = SqlAssistantConversationStore(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return _SQL_ASSISTANT_STORE
    return _ASSISTANT_STORE


def get_finops_assistant_bootstrap_cache() -> AssistantBootstrapCache:
    return _ASSISTANT_BOOTSTRAP_CACHE


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


def get_finops_risk_scan_service() -> RiskScanService:
    global _SQL_RISK_SCAN_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_RISK_SCAN_REPOSITORY is None:
            _SQL_RISK_SCAN_REPOSITORY = SqlRiskScanRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        repository = _SQL_RISK_SCAN_REPOSITORY
    else:
        repository = _RISK_SCAN_REPOSITORY
    return RiskScanService(repository)


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


@contextmanager
def _finops_model_route_scope(
    *,
    workspace_id: str,
    agent_id: str | None,
    execution_kind: str = "full_analysis",
) -> Iterator[SelectedTextRoute]:
    configuration = load_workspace_model_configuration(workspace_id)
    policy = (
        configuration.get("policy")
        if isinstance(configuration, Mapping)
        and isinstance(configuration.get("policy"), Mapping)
        else None
    )
    policy_persisted = (
        configuration.get("policy_persisted") is True
        if isinstance(configuration, Mapping)
        and "policy_persisted" in configuration
        else bool(policy)
    )
    demo_workspace_id = str(
        os.environ.get("DF_FINOPS_DEMO_WORKSPACE_ID") or "demo-corpus"
    ).strip()
    if (
        not policy_persisted
        and demo_workspace_id
        and workspace_id == demo_workspace_id
    ):
        # Read-only default for the allowlisted demo workspace. It is never
        # persisted here, so Owner saves/removals still go through the audited
        # model-routing API; an explicitly persisted empty policy wins.
        policy = demo_operations_model_policy()
    price_card = (
        configuration.get("price_card")
        if isinstance(configuration, Mapping)
        and isinstance(configuration.get("price_card"), Mapping)
        else None
    )
    with workspace_model_policy_scope(policy=policy, price_card=price_card):
        selected = select_text_route_record(
            execution_kind,
            agent_id=agent_id,
        )
        with model_route_scope(route=selected, price_card=price_card):
            yield selected


def _analyze_insight_with_workspace_route(
    service: Any,
    *,
    workspace_id: str,
    agent_kind: Literal["finops", "roi"],
    **kwargs: Any,
) -> Any:
    with _finops_model_route_scope(
        workspace_id=workspace_id,
        agent_id=analysis_agent_id(agent_kind),
    ):
        return service.analyze(agent_kind=agent_kind, **kwargs)


def get_finops_planning_service() -> FinOpsPlanningService:
    global _SQL_PLANNING_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_PLANNING_REPOSITORY is None:
            _SQL_PLANNING_REPOSITORY = SqlFinOpsPlanningRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return FinOpsPlanningService(_SQL_PLANNING_REPOSITORY)
    return FinOpsPlanningService(_PLANNING_REPOSITORY)


def get_finops_saved_view_service() -> FinOpsSavedViewService:
    global _SQL_PLANNING_REPOSITORY
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        if _SQL_PLANNING_REPOSITORY is None:
            _SQL_PLANNING_REPOSITORY = SqlFinOpsPlanningRepository(
                connection_factory=build_lineage_sql_connection_factory()
            )
        return FinOpsSavedViewService(_SQL_PLANNING_REPOSITORY)
    return FinOpsSavedViewService(_SAVED_VIEW_REPOSITORY)


_WORKSPACE_NAME_CACHE_SECONDS = 300.0
_WORKSPACE_NAME_CACHE: dict[str, tuple[float, str]] = {}


def _workspace_name(workspace_id: str) -> str:
    now = time.monotonic()
    cached = _WORKSPACE_NAME_CACHE.get(workspace_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    name = ""
    for item in list_workspaces():
        if (
            isinstance(item, Mapping)
            and str(item.get("workspace_id") or "").strip() == workspace_id
        ):
            name = str(item.get("name") or "").strip()
            break
    _WORKSPACE_NAME_CACHE[workspace_id] = (
        now + _WORKSPACE_NAME_CACHE_SECONDS,
        name,
    )
    return name


def _assistant_evidence_name(item: Mapping[str, Any]) -> str:
    event = FinOpsRequestEvent.model_validate(item)
    alias = get_finops_evidence_alias_repository().get_or_create(
        build_evidence_alias(
            tenant_ref=event.tenant_ref,
            workspace_id=event.workspace_id,
            workspace_name=_workspace_name(event.workspace_id),
            object_kind="request",
            object_ref=event.request_ref,
            operation_code=operation_code_for_event(event),
            occurred_at=event.occurred_at,
        )
    )
    return alias.display_name


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
    return canonical_tenant_ref(tenant_id, secret=secret)


def _actor_ref(actor: Mapping[str, Any]) -> str:
    actor_id = str(actor.get("actor_id") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not actor_id or not tenant_id or not secret:
        raise RuntimeError("FinOps actor scope is unavailable")
    return canonical_actor_ref(tenant_id, actor_id, secret=secret)


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
            permission_scope=_permission_scope(roles, selected_ids),
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
    except FinOpsCacheBusy:
        raise
    except (RuntimeError, ValueError) as exc:
        if "HMAC" in str(exc) or "scope is unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="FinOps evidence service is unavailable") from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return service, query, roles


def _permission_scope(
    roles: Mapping[str, str],
    workspace_ids: tuple[str, ...],
) -> str:
    pairs = [
        [workspace_id, str(roles[workspace_id])]
        for workspace_id in sorted(workspace_ids)
    ]
    material = json.dumps(
        pairs,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


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


def _redact_unattributed_gateway_evidence(payload: dict[str, Any]) -> None:
    """Hide the Owner-only unattributed gateway evidence from lower roles.

    Members must never see the system-scoped aggregate, so both the labelled
    block and the piped ``unmatched_metric_records`` counter are removed.
    """
    trust = payload.get("trust") if isinstance(payload, dict) else None
    apim = trust.get("apim") if isinstance(trust, dict) else None
    if isinstance(apim, dict):
        apim.pop("gateway_unmatched", None)
        apim["unmatched_metric_records"] = None


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
            include_evidence_refs=True,
        )
        for kind in ("finops", "roi")
    }


def _bootstrap_budget(
    query: FinOpsQuery,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    cost = metrics.get("estimated_cost")
    used_amount = cost.get("amount") if isinstance(cost, Mapping) else None
    cost_status = str(cost.get("status") or "unavailable") if isinstance(cost, Mapping) else "unavailable"
    if query.workspace_id:
        repository = get_finops_member_budget_repository()
        subjects = repository.list_budget_subjects(
            query.tenant_ref,
            query.workspace_id,
            include_disabled=False,
        )
        member_refs = {item.subject_ref for item in subjects}
        member_budgets = [
            item
            for item in repository.list_budgets(
                query.tenant_ref,
                include_disabled=False,
            )
            if item.member_ref in member_refs
        ]
        if member_budgets:
            amount = round(sum(float(item.amount_usd) for item in member_budgets), 8)
            month_start, month_end = _utc_month_window()
            summaries = repository.summarize_month(
                query.tenant_ref,
                month_start,
                month_end,
                (query.workspace_id,),
            )
            selected = [summaries.get(item.member_ref) for item in member_budgets]
            known = [
                item for item in selected
                if item is not None and item.estimated_spend_usd is not None
            ]
            used_amount = (
                round(sum(float(item.estimated_spend_usd) for item in known), 8)
                if known
                else None
            )
            priced_requests = sum(item.priced_requests for item in selected if item is not None)
            total_requests = sum(item.total_requests for item in selected if item is not None)
            coverage_pct = (
                round(priced_requests / total_requests * 100, 4)
                if total_requests
                else None
            )
            if not known:
                budget_data_status = "unavailable"
            elif (
                len(known) == len(member_budgets)
                and total_requests > 0
                and priced_requests == total_requests
            ):
                budget_data_status = "complete"
            else:
                budget_data_status = "partial"
            return {
                "amount": amount,
                "used_amount": used_amount,
                "usage_pct": (
                    round(float(used_amount) / amount * 100, 4)
                    if used_amount is not None and amount > 0
                    else None
                ),
                "status": "estimated" if used_amount is not None else "unavailable",
                "source": "workspace_member_budgets",
                "pricing_coverage_pct": coverage_pct,
                "data_status": budget_data_status,
                "priced_requests": priced_requests,
                "total_requests": total_requests,
                "period": {
                    "type": "calendar_month_utc",
                    "from": _iso(month_start),
                    "to": _iso(month_end),
                },
            }
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
    return {
        "amount": amount,
        "used_amount": used_amount,
        "usage_pct": usage_pct,
        "status": cost_status,
        "source": "daily_cost_budget",
    }


def _utc_month_window() -> tuple[datetime, datetime]:
    start = datetime.now(timezone.utc).replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


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


def _assistant_scope(request: Request, workspace_id: str) -> AssistantScope:
    tenant_ref, actor_ref, roles = _pricing_read_context(request)
    if workspace_id not in roles:
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.assistant.read",
        )
    return AssistantScope(
        tenant_ref=tenant_ref,
        actor_ref=actor_ref,
        workspace_id=workspace_id,
    )


@router.get("/assistant/bootstrap")
async def bootstrap_assistant_history(
    request: Request,
    workspace_id: str = Query(..., min_length=1, max_length=160),
) -> dict[str, Any]:
    scope = _assistant_scope(request, workspace_id)
    cached = get_finops_assistant_bootstrap_cache().load(
        scope,
        lambda: get_finops_assistant_store().bootstrap(scope, message_limit=40),
    )
    value = cached.value
    return {
        "conversation": (
            value.conversation.model_dump(mode="json")
            if value.conversation is not None
            else None
        ),
        "messages": [item.model_dump(mode="json") for item in value.messages],
        "loaded_at": value.loaded_at.isoformat(),
        "expires_at": (
            value.conversation.expires_at.isoformat()
            if value.conversation is not None
            else None
        ),
        "cache_status": cached.cache_status,
    }


@router.get("/assistant/conversations")
async def list_assistant_conversations(
    request: Request,
    workspace_id: str = Query(..., min_length=1, max_length=160),
) -> dict[str, Any]:
    scope = _assistant_scope(request, workspace_id)
    items = get_finops_assistant_store().list_conversations(scope)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
    }


@router.post("/assistant/conversations", status_code=201)
async def create_assistant_conversation(
    body: AssistantConversationCreate,
    request: Request,
) -> dict[str, Any]:
    scope = _assistant_scope(request, body.workspace_id)
    value = get_finops_assistant_store().create(scope, title=body.title)
    get_finops_assistant_bootstrap_cache().invalidate(scope)
    return {"conversation": value.model_dump(mode="json")}


@router.get("/assistant/conversations/{conversation_ref}/messages")
async def get_assistant_messages(
    conversation_ref: str,
    request: Request,
    workspace_id: str = Query(..., min_length=1, max_length=160),
) -> dict[str, Any]:
    scope = _assistant_scope(request, workspace_id)
    items = get_finops_assistant_store().get_messages(
        scope, conversation_ref
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
    }


@router.delete(
    "/assistant/conversations/{conversation_ref}",
    status_code=204,
)
async def clear_assistant_conversation(
    conversation_ref: str,
    request: Request,
    workspace_id: str = Query(..., min_length=1, max_length=160),
) -> Response:
    scope = _assistant_scope(request, workspace_id)
    try:
        get_finops_assistant_store().clear(scope, conversation_ref)
        get_finops_assistant_bootstrap_cache().invalidate(scope)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Operations AI conversation not found",
        ) from exc
    return Response(status_code=204)


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
    workspace_id = str(filters.workspace_id or query.workspace_id or "").strip()
    scope = _assistant_scope(request, workspace_id) if workspace_id else None
    store = get_finops_assistant_store() if scope is not None else None
    conversation_ref = body.conversation_ref
    if store is not None:
        if not conversation_ref:
            conversation_ref = store.create(
                scope,
                title=body.question[:120],
            ).conversation_ref
        persisted = store.get_messages(scope, conversation_ref)
        if body.conversation_ref and not persisted and not any(
            item.conversation_ref == conversation_ref
            for item in store.list_conversations(scope)
        ):
            raise HTTPException(
                status_code=404,
                detail="Operations AI conversation not found",
            )
        body = body.model_copy(
            update={
                "history": [
                    AssistantTurn(
                        role=item.role,
                        content=(
                            " ".join(str(item.content or "").split())[:600]
                            or "历史记录"
                        ),
                    )
                    for item in persisted[-6:]
                ]
            }
        )
        try:
            store.append(
                scope,
                conversation_ref,
                AssistantMessage(
                    role="user",
                    content=body.question,
                    metric_context_payload=body.metric_context.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                ),
            )
            get_finops_assistant_bootstrap_cache().invalidate(scope)
        except AssistantConversationExpired as exc:
            raise HTTPException(
                status_code=404,
                detail="Operations AI conversation expired",
            ) from exc
    events = query_service.events(query)
    selected = (
        select_policy_evidence(events, context.policy_type, limit=3)
        if context.policy_type
        else select_metric_evidence(
            events,
            _assistant_evidence_metric(context.metric_id),
            limit=3,
        )
    )
    requested_refs = set(context.evidence_refs)
    selected_refs = {
        item.request_ref
        for item in selected.items
        if not requested_refs or item.request_ref in requested_refs
    }
    selected_items = [event for event in events if event.request_ref in selected_refs]
    evidence_payload = build_finops_assistant_input(
        query,
        query_service,
        metric_context=context.model_dump(mode="json", by_alias=True),
        evidence_name_resolver=_assistant_evidence_name,
        evidence_items=selected_items,
        include_summary=body.mode == "deep",
    )
    with _finops_model_route_scope(
        workspace_id=workspace_id,
        agent_id=(analysis_agent_id("finops") if body.mode == "deep" else None),
        execution_kind=("full_analysis" if body.mode == "deep" else "direct_reply"),
    ):
        response = get_finops_assistant_service().answer(
            request=body,
            evidence_payload=evidence_payload,
        )
    if store is not None and conversation_ref:
        try:
            store.append(
                scope,
                conversation_ref,
                AssistantMessage(
                    role="assistant",
                    content=response.answer,
                    metric_context_payload={
                        "response_sections": (
                            response.sections.model_dump(mode="json")
                            if response.sections is not None
                            else None
                        ),
                        "evidence_refs": response.evidence_refs,
                        "evidence_labels": response.evidence_labels,
                        "evidence_state": response.evidence_state,
                        "suggested_questions": response.suggested_questions,
                    },
                ),
            )
            get_finops_assistant_bootstrap_cache().invalidate(scope)
        except AssistantConversationExpired:
            pass
    return {
        **response.model_dump(mode="json"),
        "conversation_ref": conversation_ref,
    }


def _assistant_evidence_metric(metric_id: str) -> str:
    metric = str(metric_id or "").strip().lower()
    if "cache" in metric:
        return "cache"
    if "token" in metric:
        return "tokens"
    if "latency" in metric or metric in {"p50", "p95"}:
        return "latency"
    if "cost" in metric or "budget" in metric or metric.startswith("roi_"):
        return "estimated_cost"
    if "success" in metric or "error" in metric:
        return "success_rate"
    return "requests"


@router.get("/budgets")
async def list_budgets(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    workspace_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    query_service, query, roles = _common(
        request, from_value, to_value, None, workspace_id, None, None, None
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="FinOps budgets require admin or owner")
    overview_payload = query_service.overview(query)
    metrics = overview_payload.get("metrics") or {}
    cost = metrics.get("estimated_cost") or {}
    service = get_finops_planning_service()
    selected = []
    for budget in service.list(tenant_ref=query.tenant_ref):
        if budget.scope_type == "workspace":
            if budget.scope_id not in query.authorized_workspace_ids:
                continue
            if workspace_id and budget.scope_id != workspace_id:
                continue
        progress = service.progress(
            budget,
            spent_amount=cost.get("amount"),
            priced_requests=int(cost.get("priced_requests") or 0),
            total_requests=int(metrics.get("requests") or 0),
        )
        payload = budget.model_dump(mode="json")
        payload["progress"] = progress.model_dump(mode="json")
        selected.append(payload)
    if not selected and query.workspace_id:
        member_budget = _bootstrap_budget(query, metrics)
        if member_budget.get("source") == "workspace_member_budgets":
            period = member_budget.get("period") or {}
            amount = float(member_budget["amount"])
            spent = member_budget.get("used_amount")
            usage_pct = member_budget.get("usage_pct")
            from_period = _parse_time(str(period["from"]))
            to_period = _parse_time(str(period["to"]))
            point = min(
                max(datetime.now(timezone.utc), from_period),
                to_period,
            )
            elapsed = (point - from_period).total_seconds()
            duration = (to_period - from_period).total_seconds()
            forecast = (
                round(float(spent) * duration / elapsed, 4)
                if spent is not None and elapsed > 0
                else None
            )
            threshold_state = (
                "unavailable"
                if usage_pct is None
                else "critical"
                if float(usage_pct) >= 100
                else "warning"
                if float(usage_pct) >= 80
                else "normal"
            )
            selected.append(
                {
                    "budget_id": "workspace_member_budgets",
                    "name": "工作区成员月度预算",
                    "scope_type": "workspace",
                    "scope_id": query.workspace_id,
                    "period_start": period["from"],
                    "period_end": period["to"],
                    "amount": amount,
                    "currency": "USD",
                    "warning_pct": 80,
                    "critical_pct": 100,
                    "version": 1,
                    "created_by": "system_projection",
                    "updated_at": _iso(point),
                    "progress": {
                        "budget_id": "workspace_member_budgets",
                        "amount": amount,
                        "spent_amount": spent,
                        "usage_pct": usage_pct,
                        "forecast_amount": forecast,
                        "forecast_status": (
                            "estimated" if forecast is not None else "unavailable"
                        ),
                        "confidence": member_budget.get("data_status", "unavailable"),
                        "priced_requests": int(member_budget.get("priced_requests") or 0),
                        "total_requests": int(member_budget.get("total_requests") or 0),
                        "threshold_state": threshold_state,
                        "currency": "USD",
                    },
                }
            )
    return {
        "items": selected,
        "count": len(selected),
        "scope": overview_payload.get("scope"),
        "window": overview_payload.get("window"),
        "currency": "USD",
        "data_status": overview_payload.get("data_status", "unavailable"),
    }


@router.post("/budgets", status_code=201)
async def create_budget(
    body: BudgetDefinition,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _write_context(
        request,
        workspace_id=body.scope_id if body.scope_type == "workspace" else None,
    )
    if body.scope_type == "organization" and not all(
        role in {"owner", "admin"} for role in roles.values()
    ):
        raise HTTPException(status_code=403, detail="organization budget requires admin or owner")
    try:
        budget = get_finops_planning_service().create_budget(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            value=body,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"budget": budget.model_dump(mode="json")}


class BudgetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    budget: BudgetDefinition


@router.patch("/budgets/{budget_id}")
async def update_budget(
    budget_id: str,
    body: BudgetUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, _ = _write_context(
        request,
        workspace_id=body.budget.scope_id if body.budget.scope_type == "workspace" else None,
    )
    try:
        budget = get_finops_planning_service().update_budget(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            budget_id=budget_id,
            value=body.budget,
            base_version=body.base_version,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"budget": budget.model_dump(mode="json")}


@router.get("/views")
async def list_saved_views(
    request: Request,
    workspace_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    _, query, roles = _common(
        request, None, None, None, workspace_id, None, None, None
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="FinOps saved views require admin or owner")
    items = get_finops_saved_view_service().list(
        tenant_ref=query.tenant_ref,
        authorized_workspace_ids=query.authorized_workspace_ids,
    )
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.get("/roi/economics")
async def roi_economics(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    workspace_id: str = Query(..., min_length=1, max_length=160),
) -> dict[str, Any]:
    query_service, query, roles = _common(
        request, from_value, to_value, None, workspace_id, None, None, None
    )
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="ROI economics require admin or owner")
    try:
        try:
            from ..control_plane import (
                workspace_cost_value_snapshot,
                workspace_roi_snapshot,
            )
        except ImportError:
            from control_plane import (
                workspace_cost_value_snapshot,
                workspace_roi_snapshot,
            )
        snapshot = workspace_roi_snapshot(
            workspace_id, query.from_value, query.to_value
        )
        cost_value = workspace_cost_value_snapshot(
            workspace_id, query.from_value, query.to_value
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="ROI evidence service is unavailable",
        ) from exc
    metrics = (query_service.overview(query).get("metrics") or {})
    requests = int(metrics.get("requests") or 0)
    success_rate = metrics.get("success_rate_pct")
    successful_requests = (
        round(requests * float(success_rate) / 100)
        if success_rate is not None
        else 0
    )
    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), Mapping) else {}
    payload = build_roi_economics(
        cost_evidence=cost_value.get("cost_evidence") or {},
        outcome_evidence=cost_value.get("outcome_evidence") or {},
        realized_roi=cost_value.get("realized_roi") or {},
        requests=requests,
        successful_requests=successful_requests,
        analyses=int(usage.get("runs") or 0),
        artifacts=int(cost_value.get("artifact_count") or 0),
        scenarios=list(cost_value.get("scenarios") or []),
    )
    payload.update({
        "workspace_id": workspace_id,
        "window": {"from": query.from_value, "to": query.to_value},
        "currency": "USD",
    })
    return payload


def _roi_economics_payload(
    query_service: Any,
    query: FinOpsQuery,
    roi: Mapping[str, Any],
    cost_value: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = query_service.overview(query).get("metrics") or {}
    requests = int(metrics.get("requests") or 0)
    success_rate = metrics.get("success_rate_pct")
    successful_requests = (
        round(requests * float(success_rate) / 100)
        if success_rate is not None
        else 0
    )
    usage = roi.get("usage") if isinstance(roi.get("usage"), Mapping) else {}
    return build_roi_economics(
        cost_evidence=cost_value.get("cost_evidence") or {},
        outcome_evidence=cost_value.get("outcome_evidence") or {},
        realized_roi=cost_value.get("realized_roi") or {},
        requests=requests,
        successful_requests=successful_requests,
        analyses=int(usage.get("runs") or 0),
        artifacts=int(cost_value.get("artifact_count") or 0),
        scenarios=list(cost_value.get("scenarios") or []),
    )


def merge_output_trend(
    unit_items: list[Mapping[str, Any]],
    output_items: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join daily aggregate cost facts with separately observed artifacts.

    No aggregate is spread across days: a missing side stays unavailable for
    that exact bucket rather than being inferred from a neighbouring period.
    """
    costs = {
        str(item.get("bucket_at")): item
        for item in unit_items
        if str(item.get("bucket_at") or "")
    }
    outputs = {
        str(item.get("bucket_at")): item
        for item in output_items
        if str(item.get("bucket_at") or "")
    }
    result = []
    for bucket_at in sorted(set(costs) | set(outputs)):
        cost = costs.get(bucket_at) or {}
        output = outputs.get(bucket_at) or {}
        cost_status = str(cost.get("data_status") or "unavailable")
        output_count = output.get("effective_output_count")
        output_status = str(output.get("data_status") or "unavailable")
        result.append(
            {
                "bucket_at": bucket_at,
                "successful_requests": cost.get("successful_requests"),
                "estimated_cost": cost.get("estimated_cost"),
                "cost_per_successful_request": cost.get("cost_per_successful_request"),
                "cost_data_status": cost_status,
                "effective_output_count": output_count,
                "output_kind": output.get("output_kind") if output_count is not None else None,
                "output_data_status": output_status,
                # Task 1's decision projection accepts this concise display
                # form; the complete row is restored after its safety schema.
                "label": bucket_at,
                "period": bucket_at,
                "value": cost.get("cost_per_successful_request"),
                "unit": "USD per successful request",
                "currency": "USD" if cost.get("estimated_cost") is not None else None,
                "status": "estimated" if cost_status == "available" else "unavailable",
            }
        )
    return result


def _decision_envelope(
    query_service: Any,
    query: FinOpsQuery,
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = query_service.overview(query)
    return {
        key: envelope.get(key)
        for key in ("scope", "window", "freshness", "coverage", "currency", "data_status")
    } | dict(decision)


def _request_refs_by_run(
    events: list[FinOpsRequestEvent],
    *,
    run_limit: int = 300,
    refs_per_run: int = 3,
) -> dict[str, list[str]]:
    """Build a bounded run/request index from already-authorized query rows."""
    result: dict[str, list[str]] = {}
    for event in events:
        run_id = str(event.run_id or "").strip()
        request_ref = str(event.request_ref or "").strip()
        if not run_id or not request_ref.startswith("req_"):
            continue
        if run_id not in result:
            if len(result) >= run_limit:
                continue
            result[run_id] = []
        if (
            request_ref not in result[run_id]
            and len(result[run_id]) < refs_per_run
        ):
            result[run_id].append(request_ref)
    return result


def _roi_stage_source_lineage(
    query: FinOpsQuery,
    cost_value: Mapping[str, Any],
) -> tuple[list[str], int, dict[str, str]]:
    """Load stage source IDs without returning them through the public API."""
    if not query.workspace_id:
        return [], 0, {}
    start = _parse_time(query.from_value)
    end = _parse_time(query.to_value)
    try:
        artifact_count = max(0, int(cost_value.get("artifact_count") or 0))
    except (TypeError, ValueError):
        artifact_count = 0
    artifact_items: list[Any] = []
    if artifact_count:
        try:
            from ..control_plane import list_workspace_artifacts
        except ImportError:
            from control_plane import list_workspace_artifacts
        try:
            artifact_items = list_workspace_artifacts(
                query.workspace_id,
                run_limit=None,
            ).get("artifacts") or []
        except Exception:
            artifact_items = []

    artifact_runs: list[str] = []
    artifact_source_count = 0
    seen_artifact_runs: set[str] = set()
    for item in artifact_items:
        if not isinstance(item, Mapping) or start is None or end is None:
            continue
        item_workspace = str(item.get("workspace_id") or "").strip()
        if item_workspace != query.workspace_id:
            continue
        timestamp = str(
            item.get("created_at")
            or item.get("updated_at")
            or item.get("time")
            or ""
        ).strip()
        try:
            occurred_at = _parse_time(timestamp)
        except ValueError:
            continue
        run_id = str(item.get("run_id") or "").strip()
        if (
            start <= occurred_at < end
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", run_id)
        ):
            artifact_source_count += 1
            if run_id not in seen_artifact_runs and len(artifact_runs) < 300:
                seen_artifact_runs.add(run_id)
                artifact_runs.append(run_id)
            if artifact_source_count >= 300:
                break

    outcome = (
        cost_value.get("outcome_evidence")
        if isinstance(cost_value.get("outcome_evidence"), Mapping)
        else {}
    )
    selected_outcomes = {
        str(value or "").strip()
        for value in outcome.get("outcome_event_ids") or []
        if str(value or "").strip()
    }
    if not selected_outcomes:
        return artifact_runs, artifact_source_count, {}
    try:
        try:
            from ..outcome_store import list_outcome_events
        except ImportError:
            from outcome_store import list_outcome_events
        outcome_items = list_outcome_events(query.workspace_id)
    except Exception:
        outcome_items = []
    source_by_outcome: dict[str, str] = {}
    for item in outcome_items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("workspace_id") or "").strip() != query.workspace_id:
            continue
        event_id = str(item.get("event_id") or "").strip()
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        source_run_id = str(source.get("run_id") or "").strip()
        if event_id in selected_outcomes and source_run_id:
            source_by_outcome[event_id] = source_run_id
            if len(source_by_outcome) >= 300:
                break
    return artifact_runs, artifact_source_count, source_by_outcome


def _roi_decision_payload(query_service: Any, query: FinOpsQuery) -> dict[str, Any]:
    if not query.workspace_id:
        raise ValueError("ROI decision requires one workspace")
    roi = workspace_roi_snapshot(query.workspace_id, query.from_value, query.to_value)
    cost_value = workspace_cost_value_snapshot(query.workspace_id, query.from_value, query.to_value)
    economics = _roi_economics_payload(query_service, query, roi, cost_value)
    unit_trend = merge_output_trend(
        query_service.unit_economics_trend(query, "day").get("items") or [],
        cost_value.get("output_trend") or [],
    )
    artifact_run_ids, artifact_source_count, outcome_source_run_ids = _roi_stage_source_lineage(
        query,
        cost_value,
    )
    request_refs_by_run = _request_refs_by_run(query_service.events(query))
    decision = build_roi_decision(
        economics=economics,
        roi_snapshot=roi,
        cost_value=cost_value,
        unit_trend=unit_trend,
        request_refs_by_run=request_refs_by_run,
        artifact_run_ids=artifact_run_ids,
        artifact_source_count=artifact_source_count,
        outcome_source_run_ids=outcome_source_run_ids,
    )
    # Keep Task 1's schema-safe display projection while returning the full
    # bounded trend rows that this page needs for unavailable-data states.
    decision["unit_economics_trend"] = unit_trend
    return _decision_envelope(query_service, query, decision)


def _decision_anomalies_from_events(
    events: list[FinOpsRequestEvent],
    *,
    tenant_ref: str,
    scope_workspace_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    findings = evaluate_default_anomalies(
        _anomaly_evaluation_input(events, tenant_ref=tenant_ref)
    )
    managed = get_finops_anomaly_service().reconcile(
        tenant_ref=tenant_ref,
        findings=findings,
        scope_workspace_ids=scope_workspace_ids,
    )
    selected = set(scope_workspace_ids)
    return [
        {
            **item.model_dump(mode="json", exclude={"tenant_ref", "origin"}),
            "evidence_state": "observed",
        }
        for item in managed
        if set(item.workspace_ids).issubset(selected)
    ]


def _decision_opportunities(
    query_service: Any,
    query: FinOpsQuery,
    anomalies: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metrics = query_service.overview(query).get("metrics") or {}
    cost = metrics.get("estimated_cost") if isinstance(metrics.get("estimated_cost"), Mapping) else {}
    total = metrics.get("requests") or 0
    priced = cost.get("priced_requests") if isinstance(cost, Mapping) else 0
    coverage = round(float(priced) / float(total) * 100, 4) if total else None
    queue = build_opportunity_queue(
        anomalies=list(anomalies),
        recommendations=list(anomalies),
        priced_cost=cost.get("amount") if isinstance(cost, Mapping) else None,
        priced_coverage_pct=coverage,
    )
    managed_by_id = {
        str(item.get("anomaly_id") or ""): item
        for item in anomalies
        if item.get("anomaly_id")
    }
    for item in queue:
        managed = managed_by_id.get(str(item.get("anomaly_id") or ""), {})
        item["anomaly_status"] = str(managed.get("status") or "")
        if item.get("policy_type") != "cache_hit_rate":
            item["base_version"] = REMEDIATION_TEMPLATE_VERSION
            continue
        try:
            item["base_version"] = current_remediation_base_version(
                query.tenant_ref,
                str(query.workspace_id or ""),
                "cache_policy",
            )
        except Exception:
            # A configuration read affects only this typed opportunity. The
            # remaining risk decision remains safe and usable.
            item["base_version"] = None
    return queue


def _current_remediation_opportunity(
    *,
    tenant_ref: str,
    workspace_id: str,
    source_opportunity_id: str,
    permission_scope: str = "",
) -> dict[str, object] | None:
    start, end = _window(None, None)
    query = FinOpsQuery(
        tenant_ref=tenant_ref,
        authorized_workspace_ids=(workspace_id,),
        permission_scope=permission_scope,
        workspace_id=workspace_id,
        from_value=start,
        to_value=end,
    )
    service = get_finops_query_service()
    events = service.events(query)
    anomalies = _decision_anomalies_from_events(
        events,
        tenant_ref=tenant_ref,
        scope_workspace_ids=(workspace_id,),
    )
    opportunities = _decision_opportunities(service, query, anomalies)
    return next(
        (
            dict(item)
            for item in opportunities
            if item.get("opportunity_id") == source_opportunity_id
        ),
        None,
    )


def _risk_evidence_sets(
    events: list[FinOpsRequestEvent],
    opportunities: list[Mapping[str, Any]],
) -> list[EvidenceSet]:
    by_ref = {event.request_ref: event for event in events}
    result: list[EvidenceSet] = []
    for opportunity in opportunities:
        requested_refs = [
            str(value)
            for value in opportunity.get("evidence_refs") or []
            if str(value) in by_ref
        ]
        candidates = [by_ref[ref] for ref in requested_refs] if requested_refs else events
        policy_type = str(opportunity.get("policy_type") or "")
        selected = select_policy_evidence(candidates, policy_type, limit=3)
        result.append(
            selected.model_copy(
                update={
                    "subject_id": str(opportunity.get("opportunity_id") or policy_type),
                    "reason": f"{str(opportunity.get('title') or selected.reason)}证据",
                }
            )
        )
    return result


def _risk_evidence_summaries(evidence_sets: list[EvidenceSet]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence_set in evidence_sets:
        for item in evidence_set.items:
            if item.request_ref in seen:
                continue
            seen.add(item.request_ref)
            selected.append(item.model_dump(mode="json"))
    return selected[:30]


def _governance_capability() -> dict[str, Any]:
    return {
        "read_enabled": True,
        "draft_enabled": True,
        "actions_enabled": False,
        "typed_executors": ["cache_policy"],
    }


def _risk_decision_payload(query_service: Any, query: FinOpsQuery) -> dict[str, Any]:
    if not query.workspace_id:
        raise ValueError("risk decision requires one workspace")
    events = query_service.events(query)
    anomaly_items = _decision_anomalies_from_events(
        events,
        tenant_ref=query.tenant_ref,
        scope_workspace_ids=(query.workspace_id,),
    )
    opportunity_items = _decision_opportunities(query_service, query, anomaly_items)
    evidence_sets = _risk_evidence_sets(events, opportunity_items)
    evidence_summaries = _risk_evidence_summaries(evidence_sets)
    latest = get_finops_insight_service().latest(
        tenant_ref=query.tenant_ref,
        authorized_workspace_ids=(query.workspace_id,),
        agent_kind="finops",
    )
    drafts = get_finops_remediation_service().list(
        tenant_ref=query.tenant_ref,
        authorized_workspace_ids=(query.workspace_id,),
    )
    decision = build_risk_decision(
        anomalies=anomaly_items,
        opportunities=opportunity_items,
        evidence_summaries=evidence_summaries,
        evidence_sets=[item.model_dump(mode="json") for item in evidence_sets],
        insight=_public_insight(latest, include_evidence_refs=True) if latest else None,
        drafts=[
            draft.model_dump(
                mode="json",
                exclude={"tenant_ref", "created_by", "reviewed_by"},
            )
            for draft in drafts
        ],
        governance_capability=_governance_capability(),
    )
    return _decision_envelope(query_service, query, decision)


def _risk_scan_scope(query: FinOpsQuery) -> RiskScanScope:
    if not query.workspace_id:
        raise ValueError("risk scan requires one workspace")
    return RiskScanScope(
        workspace_id=query.workspace_id,
        from_value=query.from_value,
        to_value=query.to_value,
        department_id=query.department_id,
        agent_id=query.agent_id,
        actor_ref=query.actor_ref,
        model=query.model,
    )


def _risk_policy_revision(tenant_ref: str) -> str:
    policies = sorted(
        (
            item.model_dump(
                mode="json",
                exclude={"updated_by", "updated_at"},
            )
            for item in get_finops_management_service().list_policies(
                tenant_ref=tenant_ref
            )
        ),
        key=lambda item: (str(item.get("policy_type")), str(item.get("policy_id"))),
    )
    material = json.dumps(
        policies,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"policy_{hashlib.sha256(material).hexdigest()[:16]}"


def _risk_ledger_revision(events: list[FinOpsRequestEvent]) -> str:
    digest = hashlib.sha256()
    for event in sorted(events, key=lambda item: (item.occurred_at, item.request_ref)):
        digest.update(event.request_ref.encode("utf-8"))
        digest.update(event.occurred_at.isoformat().encode("utf-8"))
        digest.update(event.evidence_state.encode("utf-8"))
        revision = str(event.estimated_cost.price_card_revision or "")
        digest.update(revision.encode("utf-8"))
    return f"ledger_{digest.hexdigest()[:16]}"


def _public_risk_scan(
    scan: FinOpsRiskScan,
    *,
    events: list[FinOpsRequestEvent],
) -> dict[str, Any]:
    scope = scan.scope.model_dump(mode="json")
    scope["from"] = scope.pop("from_value")
    scope["to"] = scope.pop("to_value")
    return {
        **scan.model_dump(
            mode="json",
            exclude={"tenant_ref", "initiated_by_ref", "scope", "scope_fingerprint"},
        ),
        "scope": scope,
        "evidence_sets": [
            _scan_finding_evidence(finding, events).model_dump(mode="json")
            for finding in scan.findings
        ],
        "governance": {
            "mode": "read_only_scan",
            "automatic_actions": False,
            "explanation_agent_invoked": False,
        },
    }


def _scan_finding_evidence(
    finding: RiskScanFinding,
    events: list[FinOpsRequestEvent],
) -> EvidenceSet:
    events_by_ref = {event.request_ref: event for event in events}
    persisted_events = [
        events_by_ref[request_ref]
        for request_ref in finding.evidence_refs
        if request_ref in events_by_ref
    ]
    candidates = persisted_events if persisted_events else events
    return select_policy_evidence(candidates, finding.policy_type)


def _risk_scan_context(
    request: Request,
    *,
    from_value: str | None,
    to_value: str | None,
    department_id: str | None,
    workspace_id: str,
    agent_id: str | None,
    actor_ref: str | None,
    model: str | None,
) -> tuple[Any, FinOpsQuery, list[FinOpsRequestEvent]]:
    service, query, roles = _common(
        request,
        from_value,
        to_value,
        department_id,
        workspace_id,
        agent_id,
        actor_ref,
        model,
    )
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="risk scan requires admin or owner",
        )
    return service, query, service.events(query)


@router.post("/risk/scans", status_code=201)
async def run_risk_scan(
    body: RiskScanCreateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        service, query, events = _risk_scan_context(
            request,
            from_value=body.from_value,
            to_value=body.to_value,
            department_id=body.department_id,
            workspace_id=body.workspace_id,
            agent_id=body.agent_id,
            actor_ref=body.actor_ref,
            model=body.model,
        )
        actor = actor_from_request(request, fallback=False)
        scan = get_finops_risk_scan_service().run(
            tenant_ref=query.tenant_ref,
            scope=_risk_scan_scope(query),
            evaluation=_anomaly_evaluation_input(
                events,
                tenant_ref=query.tenant_ref,
            ),
            policy_revision=_risk_policy_revision(query.tenant_ref),
            ledger_revision=_risk_ledger_revision(events),
            initiated_by_ref=_actor_ref(actor),
        )
        _bump_finops_domains(
            query.tenant_ref,
            [body.workspace_id],
            ("risk",),
        )
        return _public_risk_scan(scan, events=events)
    except HTTPException:
        raise
    except FinOpsPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail="risk scan persistence is unavailable",
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="risk scan evidence is unavailable",
        ) from exc


@router.get("/risk/scans/latest")
async def latest_risk_scan(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str = Query(min_length=1, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    try:
        _service, query, events = _risk_scan_context(
            request,
            from_value=from_value,
            to_value=to_value,
            department_id=department_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_ref=actor_ref,
            model=model,
        )
        scan = get_finops_risk_scan_service().latest(
            tenant_ref=query.tenant_ref,
            scope=_risk_scan_scope(query),
        )
        if scan is None:
            raise HTTPException(status_code=404, detail="risk scan not found")
        return _public_risk_scan(scan, events=events)
    except HTTPException:
        raise
    except FinOpsPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail="risk scan persistence is unavailable",
        ) from exc


@router.get("/roi/decision")
async def roi_decision(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str = Query(min_length=1, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="ROI decision requires admin or owner")
    try:
        return _compose_decision(
            service,
            "roi_decision",
            query,
            lambda: _roi_decision_payload(service, query),
            force_refresh=refresh,
        )
    except FinOpsCacheBusy:
        raise
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="ROI evidence service is unavailable") from exc


@router.get("/risk/decision")
async def risk_decision(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str = Query(min_length=1, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="risk decision requires admin or owner")
    try:
        return _compose_decision(
            service,
            "risk_decision",
            query,
            lambda: _risk_decision_payload(service, query),
            force_refresh=refresh,
        )
    except FinOpsPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail="risk evidence service is unavailable",
        ) from exc


def _compose_decision(
    service: Any,
    operation: Literal["roi_decision", "risk_decision"],
    query: FinOpsQuery,
    compute: Any,
    *,
    force_refresh: bool,
) -> dict[str, Any]:
    compose = getattr(service, "compose", None)
    if callable(compose):
        return compose(
            operation,
            query,
            compute,
            force_refresh=force_refresh,
        )
    # Focused tests and integrators may provide an uncached query service.
    # Wrap it here rather than changing the authorized query or bypassing it.
    return CachedFinOpsQueryService(
        service,
        cache=cache_store,
        namespace=get_finops_cache_namespace(),
    ).compose(
        operation,
        query,
        compute,
        force_refresh=force_refresh,
    )


@router.post("/views", status_code=201)
async def create_saved_view(
    body: SavedViewCreate,
    request: Request,
) -> dict[str, Any]:
    workspace_id = body.filters.get("workspace_id")
    tenant_ref, actor_ref, _ = _write_context(request, workspace_id=workspace_id)
    try:
        saved = get_finops_saved_view_service().create(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            value=body,
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    return {"view": saved.model_dump(mode="json")}


@router.delete("/views/{view_id}")
async def delete_saved_view(view_id: str, request: Request) -> Response:
    tenant_ref, _, _ = _write_context(request)
    deleted = get_finops_saved_view_service().delete(
        tenant_ref=tenant_ref,
        view_id=view_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="FinOps saved view not found")
    return Response(status_code=204)


@router.get("/export.csv")
async def export_finops_csv(
    request: Request,
    group_by: Literal["department", "workspace", "agent", "model"] = Query(default="workspace"),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> Response:
    service, query, roles = _common(
        request, from_value, to_value, department_id, workspace_id, agent_id, None, model
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="FinOps export requires admin or owner")
    payload = service.breakdowns(query, group_by)
    content = export_breakdown_csv(payload.get("items") or [])
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dataforge-finops.csv"'},
    )


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
    service, query, roles = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    payload = service.overview(query)
    if not all(role in {"owner", "admin"} for role in roles.values()):
        _redact_unattributed_gateway_evidence(payload)
    return payload


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
    metric: Literal[
        "tokens",
        "requests",
        "estimated_cost",
        "p95_latency_ms",
    ] = Query(default="tokens"),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, _ = _common(request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model)
    return service.trends(query, bucket, metric=metric)


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


@router.get("/evidence")
async def evidence_for_subject(
    request: Request,
    metric_id: str | None = Query(default=None, max_length=96),
    policy_type: str | None = Query(default=None, max_length=64),
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    if bool(metric_id) == bool(policy_type):
        raise HTTPException(
            status_code=422,
            detail="exactly one evidence subject is required",
        )
    service, query, roles = _common(
        request,
        from_value,
        to_value,
        department_id,
        workspace_id,
        agent_id,
        actor_ref,
        model,
    )
    can_read_detail = (
        roles.get(workspace_id) in {"owner", "admin"}
        if workspace_id
        else bool(roles) and all(
            role in {"owner", "admin"} for role in roles.values()
        )
    )
    if not can_read_detail:
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.request_detail.read",
        )
    events = service.events(query)
    selected = (
        select_metric_evidence(events, str(metric_id))
        if metric_id
        else select_policy_evidence(events, str(policy_type))
    )
    events_by_ref = {event.request_ref: event for event in events}
    named_items = []
    for item in selected.items:
        event = events_by_ref.get(item.request_ref)
        display_name = item.request_name
        if event is not None:
            display_name = _assistant_evidence_name(
                event.model_dump(mode="json")
            )
        named_items.append(item.model_copy(update={"request_name": display_name}))
    return selected.model_copy(update={"items": named_items}).model_dump(mode="json")


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
    can_read_detail = (
        roles.get(workspace_id) in {"owner", "admin"}
        if workspace_id
        else bool(roles) and all(
            role in {"owner", "admin"} for role in roles.values()
        )
    )
    if not can_read_detail:
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
        _analyze_insight_with_workspace_route,
        service,
        workspace_id=body.workspace_id,
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
            "items": [
                item.model_dump(mode="json", exclude={"tenant_ref", "origin"})
                for item in managed
            ],
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
            "evidence_refs": item.evidence_refs,
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


@router.get("/opportunities")
async def opportunities(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str | None = Query(default=None, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(
        request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="FinOps opportunities require admin or owner")
    events = service.events(query)
    findings = evaluate_default_anomalies(
        _anomaly_evaluation_input(events, tenant_ref=query.tenant_ref)
    )
    anomaly_items = [
        {
            **item.model_dump(mode="json"),
            "evidence_state": "observed" if item.sample_count >= 20 else "partial",
        }
        for item in findings
    ]
    recommendation_items = [
        {
            "policy_type": item.policy_type,
            "recommendation": item.recommendation,
        }
        for item in findings
    ]
    metrics = (service.overview(query).get("metrics") or {})
    cost = metrics.get("estimated_cost") or {}
    requests = int(metrics.get("requests") or 0)
    priced_requests = int(cost.get("priced_requests") or 0)
    coverage = round(priced_requests / requests * 100, 4) if requests else None
    items = build_opportunity_queue(
        anomalies=anomaly_items,
        recommendations=recommendation_items,
        priced_cost=cost.get("amount"),
        priced_coverage_pct=coverage,
    )
    return {
        "items": items,
        "count": len(items),
        "scope": {"workspace_ids": list(query.authorized_workspace_ids)},
        "window": {"from": query.from_value, "to": query.to_value},
        "currency": "USD",
        "data_status": "complete" if coverage == 100 else "partial" if requests else "unavailable",
    }


def _public_remediation_draft(draft: Any) -> dict[str, Any]:
    return draft.model_dump(
        mode="json",
        exclude={"tenant_ref", "created_by", "reviewed_by"},
    )


def _public_remediation_action(action: Any) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "status": action.status,
        "payload": action.payload,
        "version": action.version,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }


def _remediation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, RemediationNotFound):
        return HTTPException(
            status_code=404,
            detail="remediation draft not found",
        )
    if isinstance(exc, (RemediationConflict, ActionConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ActionPermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ActionNotFound):
        return HTTPException(
            status_code=404,
            detail="FinOps action not found",
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, FinOpsPersistenceError):
        return HTTPException(
            status_code=503,
            detail="FinOps remediation service is unavailable",
        )
    return HTTPException(status_code=500, detail="FinOps remediation failed")


def _remediation_draft_context(
    request: Request,
    draft_id: str,
) -> tuple[str, str, dict[str, str], Any]:
    tenant_ref, actor_ref, roles = _write_context(request)
    try:
        draft = get_finops_remediation_service().get(
            tenant_ref=tenant_ref,
            draft_id=draft_id,
            authorized_workspace_ids=tuple(sorted(roles)),
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    _require_admin_scope(roles, [draft.workspace_id])
    return tenant_ref, actor_ref, roles, draft


@router.post("/remediation-drafts", status_code=201)
async def create_remediation_draft(
    body: RemediationDraftCreate,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _write_context(
        request,
        workspace_id=body.workspace_id,
    )
    try:
        opportunity = _current_remediation_opportunity(
            tenant_ref=tenant_ref,
            workspace_id=body.workspace_id,
            source_opportunity_id=body.source_opportunity_id,
            permission_scope=_permission_scope(roles, (body.workspace_id,)),
        )
        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="remediation opportunity not found",
            )
        draft = get_finops_remediation_service().create(
            tenant_ref=tenant_ref,
            workspace_id=body.workspace_id,
            actor_ref=actor_ref,
            opportunity=opportunity,
            base_version=body.base_version,
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    _bump_finops_domains(tenant_ref, [draft.workspace_id], ("risk",))
    return {"draft": _public_remediation_draft(draft)}


@router.get("/remediation-drafts")
async def list_remediation_drafts(
    request: Request,
    workspace_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    tenant_ref, _, roles = _write_context(
        request,
        workspace_id=workspace_id,
    )
    authorized_workspace_ids = tuple(
        sorted(
            candidate
            for candidate, role in roles.items()
            if role in {"owner", "admin"}
            and (workspace_id is None or candidate == workspace_id)
        )
    )
    try:
        drafts = get_finops_remediation_service().list(
            tenant_ref=tenant_ref,
            authorized_workspace_ids=authorized_workspace_ids,
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    return {
        "items": [_public_remediation_draft(draft) for draft in drafts],
        "count": len(drafts),
    }


@router.get("/remediation-drafts/{draft_id}")
async def get_remediation_draft(
    draft_id: str,
    request: Request,
) -> dict[str, Any]:
    _, _, _, draft = _remediation_draft_context(request, draft_id)
    return {"draft": _public_remediation_draft(draft)}


@router.post("/remediation-drafts/{draft_id}/review")
async def review_remediation_draft(
    draft_id: str,
    body: RemediationTransitionRequest,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, _ = _remediation_draft_context(
        request,
        draft_id,
    )
    try:
        draft = get_finops_remediation_service().review(
            tenant_ref=tenant_ref,
            draft_id=draft_id,
            actor_ref=actor_ref,
            base_revision=body.base_revision,
            authorized_workspace_ids=tuple(sorted(roles)),
            reason=body.reason,
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    _bump_finops_domains(tenant_ref, [draft.workspace_id], ("risk",))
    return {"draft": _public_remediation_draft(draft)}


@router.post("/remediation-drafts/{draft_id}/close")
async def close_remediation_draft(
    draft_id: str,
    body: RemediationTransitionRequest,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, _ = _remediation_draft_context(
        request,
        draft_id,
    )
    try:
        draft = get_finops_remediation_service().close(
            tenant_ref=tenant_ref,
            draft_id=draft_id,
            actor_ref=actor_ref,
            base_revision=body.base_revision,
            authorized_workspace_ids=tuple(sorted(roles)),
            reason=body.reason,
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    _bump_finops_domains(tenant_ref, [draft.workspace_id], ("risk",))
    return {"draft": _public_remediation_draft(draft)}


@router.post("/remediation-drafts/{draft_id}/promote")
async def promote_remediation_draft(
    draft_id: str,
    body: RemediationTransitionRequest,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, _ = _remediation_draft_context(
        request,
        draft_id,
    )
    try:
        draft = get_finops_remediation_service().promote(
            tenant_ref=tenant_ref,
            draft_id=draft_id,
            actor_ref=actor_ref,
            base_revision=body.base_revision,
            authorized_workspace_ids=tuple(sorted(roles)),
            reason=body.reason,
        )
        action = get_finops_action_service().get(
            tenant_ref=tenant_ref,
            action_id=str(draft.translated_action_id or ""),
        )
    except Exception as exc:
        raise _remediation_error(exc) from exc
    _bump_finops_domains(tenant_ref, [draft.workspace_id], ("risk",))
    return {
        "draft": _public_remediation_draft(draft),
        "action": _public_remediation_action(action),
        "actions_enabled": _enabled("DF_FINOPS_ACTIONS_ENABLED"),
    }


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


def _pricing_read_context(
    request: Request,
) -> tuple[str, str, dict[str, str]]:
    if not _enabled("DF_FINOPS_READ_ENABLED"):
        raise HTTPException(status_code=404, detail="FinOps capability is disabled")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(
            status_code=401,
            detail="trusted tenant identity is required",
        )
    roles = _authorized_workspace_roles(actor)
    if not roles:
        raise HTTPException(
            status_code=403,
            detail="workspace access denied for finops.pricing.read",
        )
    try:
        return _tenant_ref(actor), _actor_ref(actor), roles
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="FinOps evidence service is unavailable",
        ) from exc


def _require_admin_scope(
    roles: Mapping[str, str],
    workspace_ids: list[str] | tuple[str, ...],
) -> None:
    targets = {str(value).strip() for value in workspace_ids if str(value).strip()}
    if any(roles.get(workspace_id) not in {"owner", "admin"} for workspace_id in targets):
        raise HTTPException(status_code=403, detail="workspace access denied for finops.write")


def _require_tenant_owner(roles: Mapping[str, str]) -> None:
    """Tenant-level pricing writes require Owner across every authorized workspace."""
    if not roles or not all(role == "owner" for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="official price mapping requires owner",
        )


def _require_tenant_admin(roles: Mapping[str, str]) -> None:
    """Tenant-level policy writes require Owner/Admin across every workspace."""
    if not roles or not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(
            status_code=403,
            detail="FinOps policy management requires admin or owner across all workspaces",
        )


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


def _bump_action_transition_domains(tenant_ref: str, action: Any) -> None:
    if action.action_type not in {"cache_policy", "model_route"}:
        return
    workspace_id = str(action.payload.get("workspace_id") or "").strip()
    if workspace_id:
        _bump_finops_domains(
            tenant_ref,
            [workspace_id],
            ("cost", "roi", "risk", "overview"),
        )


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
    _bump_action_transition_domains(tenant_ref, action)
    return _action_response(action)


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().approve(action_id, tenant_ref=tenant_ref, actor_ref=actor_ref)
    except Exception as exc:
        raise _action_error(exc) from exc
    _bump_action_transition_domains(tenant_ref, action)
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
    _bump_action_transition_domains(tenant_ref, action)
    return _action_response(action)


@router.post("/actions/{action_id}/verify")
async def verify_action(action_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, _, _ = _action_write_context(request, action_id)
    try:
        action = get_finops_action_service().verify(action_id, tenant_ref=tenant_ref, actor_ref=actor_ref)
    except Exception as exc:
        raise _action_error(exc) from exc
    _bump_action_transition_domains(tenant_ref, action)
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
    _bump_action_transition_domains(tenant_ref, action)
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
    _bump_finops_domains(tenant_ref, list(current.workspace_ids), ("risk",))
    return {
        "anomaly": anomaly.model_dump(
            mode="json",
            exclude={"tenant_ref", "origin"},
        )
    }


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
    _bump_finops_domains(tenant_ref, list(current.workspace_ids), ("risk",))
    return {
        "anomaly": anomaly.model_dump(
            mode="json",
            exclude={"tenant_ref", "origin"},
        )
    }


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


def _observed_deployment_call_classes(
    tenant_ref: str,
    authorized_workspace_ids: tuple[str, ...],
    deployment: str,
) -> set[str]:
    """Collect the call classes observed for a deployment in the ledger.

    Used to validate that an official (text-model) price entry is compatible
    with the deployment before an Owner maps it. Failures are treated as no
    observation so a new, unobserved deployment can still be pre-mapped.
    """
    if not authorized_workspace_ids:
        return set()
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        query = FinOpsQuery(
            tenant_ref=tenant_ref,
            authorized_workspace_ids=authorized_workspace_ids,
            from_value=_iso(now - timedelta(days=90)),
            to_value=_iso(now),
            model=deployment,
        )
        events = get_finops_query_service().events(query)
    except Exception:
        return set()
    return {event.call_class for event in events}


@router.get("/pricing/catalog")
async def official_pricing_catalog(request: Request) -> dict[str, Any]:
    _pricing_read_context(request)
    catalog = load_official_price_catalog()
    return {
        "revision": catalog.revision,
        "currency": "USD",
        "items": [
            item.model_dump(mode="json")
            for item in catalog.entries
        ],
        "count": len(catalog.entries),
    }


@router.get("/pricing/mappings")
async def official_pricing_mappings(request: Request) -> dict[str, Any]:
    tenant_ref, _, _ = _pricing_read_context(request)
    items = get_finops_price_mapping_repository().list(tenant_ref)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
    }


@router.put("/pricing/mappings/{deployment}")
async def update_official_pricing_mapping(
    deployment: str,
    body: PriceMappingUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _pricing_read_context(request)
    _require_tenant_owner(roles)
    catalog = load_official_price_catalog()
    price = catalog.get(body.official_price_key)
    if price is None:
        raise HTTPException(
            status_code=422,
            detail="official price key is not in the server catalog",
        )
    if not official_price_supports_call_classes(
        price,
        _observed_deployment_call_classes(tenant_ref, tuple(sorted(roles)), deployment),
    ):
        raise HTTPException(
            status_code=422,
            detail="deployment usage is not compatible with the official price entry",
        )
    mapping = DeploymentPriceMapping(
        tenant_ref=tenant_ref,
        deployment=deployment,
        official_price_key=body.official_price_key,
        mapping_revision=body.base_revision + 1,
        updated_by_ref=actor_ref,
    )
    try:
        saved = get_finops_price_mapping_repository().upsert(
            mapping,
            base_revision=body.base_revision,
        )
    except PriceMappingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _bump_finops_domains(
        tenant_ref,
        tuple(sorted(roles)),
        ("cost", "roi", "risk", "overview"),
    )
    return {"mapping": saved.model_dump(mode="json")}


@router.delete("/pricing/mappings/{deployment}", status_code=204)
async def delete_official_pricing_mapping(
    deployment: str,
    request: Request,
) -> Response:
    tenant_ref, _, roles = _pricing_read_context(request)
    _require_tenant_owner(roles)
    deleted = get_finops_price_mapping_repository().delete(tenant_ref, deployment)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="official price mapping not found",
        )
    _bump_finops_domains(
        tenant_ref,
        tuple(sorted(roles)),
        ("cost", "roi", "risk", "overview"),
    )
    return Response(status_code=204)


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
    tenant_ref, actor_ref, roles = _write_context(request)
    try:
        revision = get_finops_management_service().activate_price_card(
            tenant_ref=tenant_ref,
            revision_id=revision_id,
            actor_ref=actor_ref,
            actions_enabled=_enabled("DF_FINOPS_ACTIONS_ENABLED"),
        )
    except Exception as exc:
        raise _management_error(exc) from exc
    _bump_finops_domains(
        tenant_ref,
        tuple(sorted(roles)),
        ("cost", "roi", "risk", "overview"),
    )
    return {"price_card": _price_card_response(revision)}


@router.get("/policies")
async def list_policies(request: Request) -> dict[str, Any]:
    tenant_ref, _, _ = _write_context(request)
    items = get_finops_management_service().list_policies(tenant_ref=tenant_ref)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.post("/policies", status_code=201)
async def create_policy(body: dict[str, Any], request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, roles = _write_context(request)
    _require_tenant_admin(roles)
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
    tenant_ref, actor_ref, roles = _write_context(request)
    _require_tenant_admin(roles)
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
    tenant_ref, actor_ref, roles = _write_context(request)
    _require_tenant_admin(roles)
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

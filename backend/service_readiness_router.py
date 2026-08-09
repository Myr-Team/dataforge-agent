from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Query, Request

from .cache_store import probe as cache_probe
from .dependency_health import dependency_status
from .finops.job_status import (
    InMemoryJobRunRepository,
    JobName,
    JobRunRecord,
    JobRunRepository,
    SqlJobRunRepository,
)
from .finops.normalization import canonical_tenant_ref
from .identity import actor_from_request, is_trusted_tenant_identity
from .lineage_sql import build_lineage_sql_connection_factory
from .workspace_authz import active_workspace_role


router = APIRouter(tags=["service-readiness"])

EXPECTED_JOBS: dict[JobName, dict[str, Any]] = {
    "finops_apim_reconciliation": {"label": "入口调用对账", "expected_interval_seconds": 300},
    "finops_rollup": {"label": "运营指标聚合", "expected_interval_seconds": 900},
    "finops_retention": {"label": "数据保留清理", "expected_interval_seconds": 86400},
}

_IN_MEMORY_JOB_REPOSITORY = InMemoryJobRunRepository()


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_job_run_repository() -> JobRunRepository:
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        return SqlJobRunRepository(connection_factory=build_lineage_sql_connection_factory())
    return _IN_MEMORY_JOB_REPOSITORY


def pricing_status(tenant_ref: str) -> dict[str, Any]:
    try:
        from .finops.official_pricing import load_official_price_catalog
        from .finops.router import get_finops_price_mapping_repository

        catalog = load_official_price_catalog()
        mappings = get_finops_price_mapping_repository().list(tenant_ref)
        return {
            "status": "ready",
            "catalog_revision": catalog.revision,
            "catalog_entries": len(catalog.entries),
            "mapping_count": len(mappings),
        }
    except Exception:
        return {"status": "degraded", "catalog_revision": None, "catalog_entries": 0, "mapping_count": 0}


def provider_status(tenant_ref: str) -> dict[str, Any]:
    try:
        from .model_provider_router import get_model_provider_repository

        items = get_model_provider_repository().list(tenant_ref)
        connected = sum(item.connection_state == "connected" for item in items)
        governed = sum(item.governance_state == "governed" for item in items)
        return {
            "status": "ready" if not items or connected else "degraded",
            "configured": len(items),
            "connected": connected,
            "governed": governed,
        }
    except Exception:
        return {"status": "degraded", "configured": 0, "connected": 0, "governed": 0}


def latest_risk_status(tenant_ref: str, workspace_id: str) -> dict[str, Any]:
    try:
        from .finops.router import get_finops_risk_scan_service

        scans = get_finops_risk_scan_service().list(
            tenant_ref=tenant_ref, workspace_id=workspace_id, limit=1
        )
        if not scans:
            return {
                "status": "not_run",
                "scan_status": None,
                "rules_evaluated": 0,
                "rules_triggered": 0,
                "evidence_coverage_pct": 0,
                "last_completed_at": None,
            }
        scan = scans[0]
        return {
            "status": "ready" if scan.status == "completed" else scan.status,
            "scan_status": scan.status,
            "rules_evaluated": scan.rules_evaluated,
            "rules_triggered": scan.rules_triggered,
            "evidence_coverage_pct": scan.evidence_coverage_pct,
            "last_completed_at": scan.finished_at,
        }
    except Exception:
        return {
            "status": "degraded",
            "scan_status": None,
            "rules_evaluated": 0,
            "rules_triggered": 0,
            "evidence_coverage_pct": 0,
            "last_completed_at": None,
        }


@router.get("/api/service-readiness")
async def service_readiness(
    request: Request,
    workspace_id: str = Query(min_length=1, max_length=160),
) -> dict[str, Any]:
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=401, detail="trusted tenant identity is required")
    role = active_workspace_role(workspace_id, actor)
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="workspace admin permission is required")

    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    tenant_ref = canonical_tenant_ref(actor.get("tenant_id"), secret=secret)
    observed = dependency_status()
    details = observed.get("details") if isinstance(observed, Mapping) else {}
    details = details if isinstance(details, Mapping) else {}

    data_items = [
        _dependency_item("工作区文件", "blob", details),
        _dependency_item("知识检索", "search", details),
        _cache_item(cache_probe()),
    ]
    ai_items = [
        _dependency_item("主模型服务", "foundry", details),
        _dependency_item("工具连接", "mcp", details),
        _dependency_item("语音服务", "speech", details),
        _dependency_item("内容安全", "content_safety", details),
        {"key": "external_models", "label": "外部模型", **provider_status(tenant_ref)},
    ]
    finops_items = [
        {
            "key": "ledger",
            "label": "运营账本",
            "status": "ready" if _enabled("DF_FINOPS_SQL_ENABLED") else "session_only",
            "details": {"persistence": "durable" if _enabled("DF_FINOPS_SQL_ENABLED") else "session"},
        },
        {"key": "pricing", "label": "模型计价", **_as_item_status(pricing_status(tenant_ref))},
        {"key": "risk_scan", "label": "风险扫描", **_as_item_status(latest_risk_status(tenant_ref, workspace_id))},
    ]
    repository = get_job_run_repository()
    jobs = [
        _job_item(name, definition, repository.latest(name))
        for name, definition in EXPECTED_JOBS.items()
    ]
    identity_state = str(actor.get("group_resolution_state") or "not_requested")
    return {
        "workspace_id": workspace_id,
        "generated_at": _iso(_now()),
        "groups": {
            "identity": {
                "label": "身份与权限",
                "items": [
                    {
                        "key": "signed_in_identity",
                        "label": "登录身份",
                        "status": "ready",
                        "details": {"role": role, "source": "entra"},
                    },
                    {
                        "key": "group_governance",
                        "label": "群组权限解析",
                        "status": "degraded" if identity_state == "unavailable" else "ready",
                        "details": {"state": identity_state},
                    },
                ],
            },
            "data": {"label": "数据服务", "items": data_items},
            "ai": {"label": "AI 服务", "items": ai_items},
            "finops": {"label": "成本与治理", "items": finops_items},
            "background_jobs": {"label": "后台任务", "items": jobs},
        },
    }


def _dependency_item(label: str, key: str, details: Mapping[str, Any]) -> dict[str, Any]:
    raw = details.get(key)
    raw = raw if isinstance(raw, Mapping) else {}
    state = str(raw.get("state") or "unavailable")
    status = "ready" if raw.get("ok") else ("not_configured" if state == "unconfigured" else "degraded")
    safe_details = {
        name: raw.get(name)
        for name in ("state", "latency_ms", "observed_at")
        if raw.get(name) is not None
    }
    return {"key": key, "label": label, "status": status, "details": safe_details}


def _cache_item(raw_value: Any) -> dict[str, Any]:
    raw = raw_value if isinstance(raw_value, Mapping) else {}
    state = str(raw.get("status") or "unavailable")
    status = "ready" if state in {"ok", "hit", "miss", "connected"} else "degraded"
    details = {
        name: raw.get(name)
        for name in ("status", "elapsed_ms", "configured")
        if raw.get(name) is not None
    }
    return {"key": "cache", "label": "查询缓存", "status": status, "details": details}


def _as_item_status(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(value.get("status") or "degraded"),
        "details": {key: item for key, item in value.items() if key != "status"},
    }


def _job_item(
    name: JobName,
    definition: Mapping[str, Any],
    latest: JobRunRecord | None,
) -> dict[str, Any]:
    expected = int(definition["expected_interval_seconds"])
    if latest is None:
        return {
            "key": name,
            "label": str(definition["label"]),
            "status": "not_run",
            "last_completed_at": None,
            "details": {"expected_interval_seconds": expected},
        }
    status = latest.status
    age = None
    if status == "succeeded" and latest.completed_at:
        age = max(0, int((_now() - _parse_time(latest.completed_at)).total_seconds()))
        status = "ready" if age <= expected * 2 else "stale"
    details: dict[str, Any] = {
        "expected_interval_seconds": expected,
        "rows_observed": latest.rows_observed,
        "rows_written": latest.rows_written,
    }
    if age is not None:
        details["age_seconds"] = age
    if latest.source_freshness_at:
        details["source_freshness_at"] = latest.source_freshness_at
    if latest.safe_error_category:
        details["error_category"] = latest.safe_error_category
    return {
        "key": name,
        "label": str(definition["label"]),
        "status": status,
        "last_completed_at": latest.completed_at,
        "details": details,
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["EXPECTED_JOBS", "get_job_run_repository", "router"]

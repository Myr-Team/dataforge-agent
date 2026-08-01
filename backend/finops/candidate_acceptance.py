from __future__ import annotations

import argparse
import base64
import json
import os
from collections.abc import Mapping
from typing import Any

from fastapi.testclient import TestClient


class CandidateAcceptanceError(RuntimeError):
    pass


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CandidateAcceptanceError(f"{label} is not numeric")
    number = float(value)
    if positive and number <= 0:
        raise CandidateAcceptanceError(f"{label} must be positive")
    return number


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateAcceptanceError(f"{label} is unavailable")
    return value


def _items(value: Any, label: str, *, minimum: int = 1) -> list[Mapping[str, Any]]:
    items = value.get("items") if isinstance(value, Mapping) else value
    if not isinstance(items, list) or len(items) < minimum:
        raise CandidateAcceptanceError(f"{label} lacks display rows")
    if not all(isinstance(item, Mapping) for item in items):
        raise CandidateAcceptanceError(f"{label} contains an invalid row")
    return items


def _distinct_numeric(
    items: list[Mapping[str, Any]],
    path: tuple[str, ...],
    label: str,
    *,
    allow_missing: bool = False,
) -> dict[str, int]:
    values: set[float] = set()
    known = 0
    missing = 0
    for item in items:
        value: Any = item
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            missing += 1
            continue
        known += 1
        values.add(float(value))
    if missing and not allow_missing:
        raise CandidateAcceptanceError(f"{label} contains a missing display value")
    if len(values) < 2:
        raise CandidateAcceptanceError(f"{label} chart geometry is not data-driven")
    return {"distinct": len(values), "known": known, "missing": missing}


def summarize_candidate_payloads(payloads: Mapping[str, Any]) -> dict[str, Any]:
    bootstrap = _mapping(payloads.get("bootstrap"), "bootstrap")
    overview = _mapping(bootstrap.get("overview"), "overview")
    metrics = _mapping(overview.get("metrics"), "overview metrics")
    tokens = _mapping(metrics.get("tokens"), "token metrics")
    cost = _mapping(metrics.get("estimated_cost"), "cost metrics")
    budget = _mapping(metrics.get("budget"), "budget metrics")
    latency = _mapping(metrics.get("latency"), "latency metrics")
    cache = _mapping(metrics.get("cache"), "cache metrics")

    overview_values = {
        "requests": _number(metrics.get("requests"), "requests", positive=True),
        "tokens": _number(tokens.get("total"), "tokens", positive=True),
        "estimated_cost": _number(cost.get("amount"), "estimated cost", positive=True),
        "budget_amount": _number(budget.get("amount"), "budget amount", positive=True),
        "budget_used": _number(budget.get("used_amount"), "budget used", positive=True),
        "budget_usage_pct": _number(budget.get("usage_pct"), "budget usage"),
        "p50_ms": _number(latency.get("p50_ms"), "P50 latency", positive=True),
        "p95_ms": _number(latency.get("p95_ms"), "P95 latency", positive=True),
        "error_rate_pct": _number(metrics.get("error_rate_pct"), "error rate"),
        "success_rate_pct": _number(metrics.get("success_rate_pct"), "success rate", positive=True),
        "cache_hit_rate_pct": _number(metrics.get("cache_hit_rate_pct"), "cache hit rate", positive=True),
        "cache_eligible": _number(cache.get("eligible_requests"), "cache eligible requests", positive=True),
        "cache_hits": _number(cache.get("hit"), "cache hits", positive=True),
        "cache_misses": _number(cache.get("miss"), "cache misses", positive=True),
        "cache_avoided_tokens": _number(cache.get("avoided_tokens"), "cache avoided tokens", positive=True),
        "cache_savings": _number(cache.get("estimated_savings"), "cache savings", positive=True),
        "gateway_coverage_pct": _number(metrics.get("apim_coverage_pct"), "gateway coverage", positive=True),
    }

    trend = _items(bootstrap.get("trend"), "trend", minimum=3)
    trend_geometry = {
        "requests": _distinct_numeric(trend, ("requests",), "request trend"),
        "tokens": _distinct_numeric(trend, ("tokens", "total"), "token trend"),
        "cost": _distinct_numeric(trend, ("estimated_cost",), "cost trend"),
    }
    departments = _items(bootstrap.get("departments"), "department breakdown", minimum=2)
    for index, item in enumerate(departments):
        _number(item.get("requests"), f"department[{index}] requests", positive=True)
        _number(item.get("tokens"), f"department[{index}] tokens", positive=True)
        _number(item.get("estimated_cost"), f"department[{index}] cost", positive=True)

    workspace_rows = _items(payloads.get("workspace_breakdown"), "workspace cost breakdown")
    agent_rows = _items(payloads.get("agents"), "agent cost breakdown", minimum=3)
    for index, item in enumerate(agent_rows):
        _number(item.get("requests"), f"agent[{index}] requests", positive=True)
    agent_cost_geometry = _distinct_numeric(
        agent_rows,
        ("estimated_cost",),
        "agent cost bars",
        allow_missing=True,
    )

    budgets = _items(payloads.get("budgets"), "budgets")
    progress = _mapping(budgets[0].get("progress"), "budget progress")
    budget_values = {
        "count": len(budgets),
        "amount": _number(progress.get("amount"), "budget progress amount", positive=True),
        "spent": _number(progress.get("spent_amount"), "budget progress spend", positive=True),
        "usage_pct": _number(progress.get("usage_pct"), "budget progress usage"),
        "forecast": _number(progress.get("forecast_amount"), "budget forecast", positive=True),
    }

    roi = _mapping(payloads.get("roi"), "ROI decision")
    roi_metrics = _items(roi.get("metrics"), "ROI metrics", minimum=4)
    for index, item in enumerate(roi_metrics):
        _number(item.get("value"), f"ROI metric[{index}]")
    roi_bridge = _mapping(roi.get("value_bridge"), "ROI value bridge")
    if not str(roi_bridge.get("formula_revision") or "").strip():
        raise CandidateAcceptanceError("ROI value bridge formula is unavailable")
    _number(roi_bridge.get("payback_months"), "ROI payback months", positive=True)
    roi_trend = _items(roi.get("unit_economics_trend"), "ROI unit trend", minimum=3)
    _distinct_numeric(roi_trend, ("value",), "ROI unit trend")
    scenarios = _items(roi.get("scenarios"), "ROI scenarios")

    risk = _mapping(payloads.get("risk"), "risk decision")
    risk_domains = _items(risk.get("risk_domains"), "risk domains", minimum=4)
    matrix = _items(risk.get("risk_matrix"), "risk matrix", minimum=4)
    priorities = _items(risk.get("priorities"), "risk priorities", minimum=4)
    portfolio = _items(risk.get("optimization_portfolio"), "optimization portfolio", minimum=4)
    evidence = _items(risk.get("selected_evidence_summaries"), "risk evidence", minimum=4)
    evidence_refs = {str(item.get("request_ref") or "") for item in evidence}
    if "" in evidence_refs or len(evidence_refs) < 4:
        raise CandidateAcceptanceError("risk evidence is not sufficiently distinct")
    insight = _mapping(risk.get("insight"), "risk insight")
    if not str(insight.get("summary") or "").strip():
        raise CandidateAcceptanceError("risk insight summary is unavailable")
    governance = _mapping(risk.get("governance_capability"), "governance capability")
    if governance.get("read_enabled") is not True or governance.get("draft_enabled") is not True:
        raise CandidateAcceptanceError("governance read or draft capability is unavailable")
    if governance.get("actions_enabled") is not False:
        raise CandidateAcceptanceError("production actions must remain disabled")

    detail = _mapping(payloads.get("request_detail"), "request detail")
    display = _mapping(detail.get("display"), "request display")
    detail_metrics = _mapping(detail.get("metrics"), "request metrics")
    required_detail = {
        "name": display.get("name"),
        "operation": display.get("operation"),
        "status": detail.get("status"),
        "latency_ms": detail_metrics.get("latency_ms"),
        "tokens": detail_metrics.get("tokens"),
        "cache": detail_metrics.get("cache"),
        "estimated_cost": detail_metrics.get("estimated_cost"),
        "business_request": detail.get("business_request"),
        "business_response": detail.get("business_response"),
    }
    missing_detail = [key for key, value in required_detail.items() if value in (None, "", {})]
    if missing_detail:
        raise CandidateAcceptanceError(
            "request detail lacks display evidence: " + ", ".join(missing_detail)
        )

    pricing = _mapping(payloads.get("pricing"), "pricing catalog")
    price_items = _items(pricing, "pricing catalog", minimum=2)
    official_models = {str(item.get("official_model") or "") for item in price_items}
    required_models = {"gpt-5.1", "gpt-5.6-terra"}
    if not required_models.issubset(official_models):
        raise CandidateAcceptanceError("official GPT-5.1/GPT-5.6 pricing is unavailable")
    mappings = _mapping(payloads.get("price_mappings"), "price mappings")
    mapping_items = mappings.get("items")
    if not isinstance(mapping_items, list):
        raise CandidateAcceptanceError("price mappings are unavailable")

    return {
        "ok": True,
        "overview": overview_values,
        "trend_buckets": len(trend),
        "trend_distinct_values": trend_geometry,
        "department_rows": len(departments),
        "workspace_rows": len(workspace_rows),
        "agent_rows": len(agent_rows),
        "agent_cost_values": agent_cost_geometry,
        "budget": budget_values,
        "roi": {
            "metric_count": len(roi_metrics),
            "value_bridge_ready": True,
            "trend_rows": len(roi_trend),
            "scenario_count": len(scenarios),
        },
        "risk": {
            "domain_count": len(risk_domains),
            "matrix_points": len(matrix),
            "priority_count": len(priorities),
            "portfolio_points": len(portfolio),
            "distinct_evidence": len(evidence_refs),
            "insight_ready": True,
            "actions_enabled": False,
        },
        "request_detail_complete": True,
        "pricing": {
            "catalog_entries": len(price_items),
            "mapping_entries": len(mapping_items),
            "required_models_available": True,
        },
    }


def _principal_headers() -> dict[str, str]:
    required = {
        "tenant": os.environ.get("DF_WORKSPACE_OWNER_TENANT_ID"),
        "actor": os.environ.get("DF_WORKSPACE_OWNER_OID"),
        "email": os.environ.get("DF_WORKSPACE_OWNER_EMAIL"),
        "proxy_secret": os.environ.get("DF_WEB_PROXY_SECRET"),
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise CandidateAcceptanceError(
            "candidate identity configuration is incomplete: " + ", ".join(missing)
        )
    claims = [
        {"typ": "oid", "val": required["actor"]},
        {"typ": "tid", "val": required["tenant"]},
        {"typ": "preferred_username", "val": required["email"]},
    ]
    principal = base64.urlsafe_b64encode(
        json.dumps({"claims": claims}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {
        "x-ms-client-principal": principal,
        "x-ms-client-principal-id": str(required["actor"]),
        "x-ms-client-principal-name": str(required["email"]),
        "x-dataforge-proxy-secret": str(required["proxy_secret"]),
        "x-dataforge-trusted-tenant": str(required["tenant"]),
    }


def collect_candidate_payloads(workspace_id: str) -> dict[str, Any]:
    try:
        from ..app import app
    except ImportError:
        from app import app

    client = TestClient(app, headers=_principal_headers())

    request_index = 0

    def get(path: str, **params: Any) -> dict[str, Any]:
        nonlocal request_index
        request_index += 1
        print(f"candidate_acceptance:request:{request_index}:start", flush=True)
        response = client.get(path, params={"workspace_id": workspace_id, **params})
        print(f"candidate_acceptance:request:{request_index}:done", flush=True)
        if response.status_code != 200:
            raise CandidateAcceptanceError(
                f"{path} returned HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CandidateAcceptanceError(f"{path} returned an invalid payload")
        return payload

    bootstrap = get("/api/finops/bootstrap")
    risk = get("/api/finops/risk/decision")
    evidence = risk.get("selected_evidence_summaries") or []
    request_ref = str(evidence[0].get("request_ref") or "") if evidence else ""
    if not request_ref:
        raise CandidateAcceptanceError("risk decision did not provide request evidence")
    return {
        "bootstrap": bootstrap,
        "workspace_breakdown": get("/api/finops/breakdowns", group_by="workspace"),
        "agents": get("/api/finops/agents"),
        "budgets": get("/api/finops/budgets"),
        "roi": get("/api/finops/roi/decision"),
        "risk": risk,
        "request_detail": get(f"/api/finops/requests/{request_ref}"),
        "pricing": get("/api/finops/pricing/catalog"),
        "price_mappings": get("/api/finops/pricing/mappings"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the deployed FinOps candidate using safe aggregates only."
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("DF_FINOPS_DEMO_WORKSPACE_ID", ""),
    )
    arguments = parser.parse_args(argv)
    workspace_id = str(arguments.workspace_id or "").strip()
    if not workspace_id:
        raise CandidateAcceptanceError("candidate workspace is not configured")
    summary = summarize_candidate_payloads(collect_candidate_payloads(workspace_id))
    summary["workspace_id"] = workspace_id
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

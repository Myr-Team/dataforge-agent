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


def _request_refs(value: Any, label: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list):
        raise CandidateAcceptanceError(f"{label} is unavailable")
    refs = [str(item or "").strip() for item in value]
    if len(refs) < minimum or any(
        not ref.startswith("req_") or len(ref) < 8 for ref in refs
    ):
        raise CandidateAcceptanceError(f"{label} is not request-level")
    return list(dict.fromkeys(refs))


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
    bridge_items = {
        str(item.get("id") or ""): item
        for item in _items(roi_bridge.get("items"), "ROI value bridge items", minimum=3)
    }
    try:
        bridge_benefit = _number(
            bridge_items["monthly_benefit"].get("value"),
            "ROI bridge monthly benefit",
            positive=True,
        )
        bridge_cost = _number(
            bridge_items["monthly_total_cost"].get("value"),
            "ROI bridge monthly total cost",
        )
        bridge_net = _number(
            bridge_items["monthly_net_benefit"].get("value"),
            "ROI bridge monthly net benefit",
            positive=True,
        )
    except KeyError as exc:
        raise CandidateAcceptanceError("ROI value bridge lacks formula terms") from exc
    if bridge_cost >= 0 or abs((bridge_benefit + bridge_cost) - bridge_net) > 1e-6:
        raise CandidateAcceptanceError("ROI value bridge must subtract cost")
    roi_metric_by_id = {
        str(item.get("id") or ""): item for item in roi_metrics
    }
    metric_cost = _number(
        _mapping(
            roi_metric_by_id.get("monthly_total_cost"),
            "ROI monthly total cost metric",
        ).get("value"),
        "ROI monthly total cost metric",
        positive=True,
    )
    if abs(metric_cost - abs(bridge_cost)) > 1e-6:
        raise CandidateAcceptanceError("ROI bridge cost disagrees with metric card")

    maturity = _mapping(roi.get("evidence_maturity"), "ROI evidence maturity")
    maturity_stages = _items(maturity.get("stages"), "ROI evidence maturity stages", minimum=4)
    roi_request_details = _mapping(
        payloads.get("roi_request_details"),
        "ROI request evidence details",
    )
    stage_refs: list[str] = []
    for index, stage in enumerate(maturity_stages):
        refs = _request_refs(
            stage.get("evidence_refs"),
            "ROI stage evidence",
        )
        stage_refs.extend(refs)
        for request_ref in refs:
            detail_payload = _mapping(
                roi_request_details.get(request_ref),
                f"ROI request detail {request_ref}",
            )
            if str(detail_payload.get("request_ref") or "") != request_ref:
                raise CandidateAcceptanceError(
                    f"ROI request detail {request_ref} is not openable"
                )
    roi_trend = _items(roi.get("unit_economics_trend"), "ROI unit trend", minimum=3)
    _distinct_numeric(roi_trend, ("value",), "ROI unit trend")
    scenarios = _items(roi.get("scenarios"), "ROI scenarios")

    risk = _mapping(payloads.get("risk"), "risk decision")
    risk_domains = _items(risk.get("risk_domains"), "risk domains", minimum=4)
    matrix = _items(risk.get("risk_matrix"), "risk matrix", minimum=4)
    priorities = _items(risk.get("priorities"), "risk priorities", minimum=4)
    portfolio = _items(risk.get("optimization_portfolio"), "optimization portfolio", minimum=4)
    evidence = _items(risk.get("selected_evidence_summaries"), "risk evidence", minimum=6)
    evidence_refs = {str(item.get("request_ref") or "") for item in evidence}
    if "" in evidence_refs or len(evidence_refs) < 6:
        raise CandidateAcceptanceError("risk evidence is not sufficiently distinct")
    evidence_sets = _items(risk.get("evidence_sets"), "risk policy evidence")
    if len(evidence_sets) < 6:
        raise CandidateAcceptanceError("risk policy evidence lacks distinct coverage")
    policy_types: set[str] = set()
    distinct_ref_sets: set[tuple[str, ...]] = set()
    string_signal_cases: set[tuple[str, str]] = set()
    expected_string_signals = {
        ("request_status", "failed"),
        ("cache_state", "miss"),
        ("pricing_status", "unpriced"),
        ("gateway_coverage", "unmanaged"),
    }
    for index, evidence_set in enumerate(evidence_sets):
        policy_type = str(evidence_set.get("policy_type") or "").strip()
        set_items = _items(
            evidence_set.get("items"),
            f"risk policy evidence[{index}]",
        )
        refs = _request_refs(
            [item.get("request_ref") for item in set_items],
            f"risk policy evidence[{index}] refs",
        )
        policy_types.add(policy_type)
        distinct_ref_sets.add(tuple(sorted(refs)))
        for item in set_items:
            signal = _mapping(item.get("signal"), f"risk policy signal[{index}]")
            metric = str(signal.get("metric") or "").strip()
            value = signal.get("value")
            if isinstance(value, str) and value.strip():
                string_signal_cases.add((metric, value.strip()))
    if len(policy_types) < 6 or len(distinct_ref_sets) < 6:
        raise CandidateAcceptanceError("risk policy evidence lacks distinct coverage")
    if not expected_string_signals.issubset(string_signal_cases):
        raise CandidateAcceptanceError("localized string evidence is incomplete")
    insight = _mapping(risk.get("insight"), "risk insight")
    if not str(insight.get("summary") or "").strip():
        raise CandidateAcceptanceError("risk insight summary is unavailable")
    governance = _mapping(risk.get("governance_capability"), "governance capability")
    if governance.get("read_enabled") is not True or governance.get("draft_enabled") is not True:
        raise CandidateAcceptanceError("governance read or draft capability is unavailable")
    if governance.get("actions_enabled") is not False:
        raise CandidateAcceptanceError("production actions must remain disabled")

    assistant_check = _mapping(payloads.get("assistant_check"), "assistant evidence check")
    requested_assistant_refs = _request_refs(
        assistant_check.get("requested_evidence_refs"),
        "assistant selected evidence",
    )
    assistant_response = _mapping(
        assistant_check.get("response"),
        "assistant selected-evidence response",
    )
    assistant_response_refs = _request_refs(
        assistant_response.get("evidence_refs"),
        "assistant response evidence",
    )
    labels = assistant_response.get("evidence_labels")
    if (
        assistant_response.get("status") != "ready"
        or assistant_response_refs != requested_assistant_refs
        or not isinstance(labels, list)
        or not all(str(label or "").strip() for label in labels)
    ):
        raise CandidateAcceptanceError("assistant evidence does not match selected item")

    routing = _mapping(payloads.get("model_routing"), "model routing")
    routes = {
        str(item.get("id") or ""): str(item.get("deployment") or "")
        for item in _items(routing.get("routes"), "model routes", minimum=2)
    }
    assignments = _mapping(
        _mapping(routing.get("policy"), "model routing policy").get(
            "agent_assignments"
        ),
        "model routing agent assignments",
    )
    for agent_id in ("df-finops-analyst", "df-roi-analyst"):
        assignment = _mapping(assignments.get(agent_id), f"{agent_id} assignment")
        fallback_route_id = str(assignment.get("fallback_route_id") or "").strip()
        if (
            assignment.get("primary_route_id") != "terra"
            or routes.get("terra") != "gpt-5.6-terra"
            or not fallback_route_id
            or fallback_route_id == "terra"
            or not routes.get(fallback_route_id)
        ):
            raise CandidateAcceptanceError(
                "operations analyst routing is not Terra-first"
            )

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
            "bridge_subtraction_verified": True,
            "openable_stage_count": len(maturity_stages),
            "openable_request_refs": len(set(stage_refs)),
            "trend_rows": len(roi_trend),
            "scenario_count": len(scenarios),
        },
        "risk": {
            "domain_count": len(risk_domains),
            "matrix_points": len(matrix),
            "priority_count": len(priorities),
            "portfolio_points": len(portfolio),
            "distinct_evidence": len(evidence_refs),
            "distinct_evidence_sets": len(distinct_ref_sets),
            "localized_string_signal_cases": len(
                expected_string_signals & string_signal_cases
            ),
            "insight_ready": True,
            "actions_enabled": False,
        },
        "request_detail_complete": True,
        "assistant": {"selected_item_evidence_verified": True},
        "model_routing": {"operations_analysts_on_terra": True},
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

    def call(
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal request_index
        request_index += 1
        print(f"candidate_acceptance:request:{request_index}:start", flush=True)
        response = client.request(
            method,
            path,
            params=dict(params or {}),
            json=dict(body) if body is not None else None,
        )
        print(f"candidate_acceptance:request:{request_index}:done", flush=True)
        if response.status_code != 200:
            raise CandidateAcceptanceError(
                f"{path} returned HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CandidateAcceptanceError(f"{path} returned an invalid payload")
        return payload

    def get(path: str, **params: Any) -> dict[str, Any]:
        return call(
            "GET",
            path,
            params={"workspace_id": workspace_id, **params},
        )

    bootstrap = get("/api/finops/bootstrap")
    roi = get("/api/finops/roi/decision")
    risk = get("/api/finops/risk/decision")
    evidence = risk.get("selected_evidence_summaries") or []
    request_ref = str(evidence[0].get("request_ref") or "") if evidence else ""
    if not request_ref:
        raise CandidateAcceptanceError("risk decision did not provide request evidence")

    maturity = _mapping(roi.get("evidence_maturity"), "ROI evidence maturity")
    stages = _items(maturity.get("stages"), "ROI evidence maturity stages", minimum=4)
    roi_refs = list(
        dict.fromkeys(
            ref
            for stage in stages
            for ref in _request_refs(
                stage.get("evidence_refs"),
                "ROI stage evidence",
            )
        )
    )
    roi_request_details = {
        ref: get(f"/api/finops/requests/{ref}") for ref in roi_refs
    }

    evidence_sets = _items(risk.get("evidence_sets"), "risk policy evidence", minimum=6)
    assistant_set = next(
        (
            item
            for item in evidence_sets
            if item.get("policy_type") == "p95_latency" and item.get("items")
        ),
        None,
    )
    if assistant_set is None:
        raise CandidateAcceptanceError("risk decision lacks assistant evidence")
    assistant_item = _items(
        assistant_set.get("items"),
        "assistant selected evidence",
    )[0]
    assistant_ref = _request_refs(
        [assistant_item.get("request_ref")],
        "assistant selected evidence",
    )[0]
    signal = _mapping(assistant_item.get("signal"), "assistant evidence signal")
    window = _mapping(risk.get("window"), "risk window")
    assistant_response = call(
        "POST",
        "/api/finops/assistant/query",
        body={
            "question": "请基于当前选中的代表证据解释风险、影响与下一步。",
            "metric_context": {
                "metric_id": f"risk_{assistant_set.get('policy_type')}",
                "label": str(assistant_set.get("reason") or "风险代表证据"),
                "value": signal.get("value"),
                "unit": str(signal.get("unit") or ""),
                "window": {"from": window.get("from"), "to": window.get("to")},
                "filters": {"workspace_id": workspace_id},
                "data_status": str(assistant_set.get("data_status") or "partial"),
                "evidence_state": str(
                    assistant_set.get("evidence_state") or "partial"
                ),
                "policy_type": assistant_set.get("policy_type"),
                "evidence_refs": [assistant_ref],
            },
        },
    )
    return {
        "bootstrap": bootstrap,
        "workspace_breakdown": get("/api/finops/breakdowns", group_by="workspace"),
        "agents": get("/api/finops/agents"),
        "budgets": get("/api/finops/budgets"),
        "roi": roi,
        "risk": risk,
        "roi_request_details": roi_request_details,
        "assistant_check": {
            "requested_evidence_refs": [assistant_ref],
            "response": assistant_response,
        },
        "model_routing": get(
            f"/api/workspaces/{workspace_id}/governance/model-routing"
        ),
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

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .query import FinOpsQuery


def build_finops_assistant_input(
    query: FinOpsQuery,
    query_service: Any,
    *,
    metric_context: Mapping[str, Any],
    evidence_items: Iterable[Any] = (),
    evidence_name_resolver: Callable[[Mapping[str, Any]], str] | None = None,
    include_summary: bool = True,
) -> dict[str, Any]:
    """Build the small, cached context used by interactive Operations AI."""
    bootstrap = query_service.bootstrap(query) if include_summary else {}
    projected_evidence: list[dict[str, Any]] = []
    for raw in list(evidence_items)[:3]:
        item = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
        if not isinstance(item, Mapping):
            continue
        request_ref = str(item.get("request_ref") or "").strip()
        if not request_ref:
            continue
        projected = {
            "request_ref": request_ref,
            **_pick(
                item,
                "occurred_at",
                "call_class",
                "workspace_id",
                "agent_id",
                "deployment",
                "route",
                "execution_kind",
                "status",
                "error_category",
                "latency_ms",
                "tokens",
                "result_cache",
                "provider_cache",
                "gateway_coverage",
                "estimated_cost",
                "evidence_state",
            ),
        }
        projected["display_name"] = _evidence_display_name(
            item,
            index=len(projected_evidence),
            resolver=evidence_name_resolver,
        )
        projected_evidence.append(projected)
    refs = [item["request_ref"] for item in projected_evidence]
    catalog = [
        {
            "ref": item["request_ref"],
            "display_name": str(item.get("display_name") or f"运营证据 {index + 1}"),
        }
        for index, item in enumerate(projected_evidence)
    ]
    overview = bootstrap.get("overview") if isinstance(bootstrap, Mapping) else {}
    trust = bootstrap.get("trust") if isinstance(bootstrap, Mapping) else {}
    return {
        "status": "ready" if refs else "insufficient_data",
        "agent_kind": "finops",
        "scope": {"workspace_ids": list(_selected_workspace_ids(query))},
        "window": {"from": query.from_value, "to": query.to_value},
        "metric_context": _pick(
            metric_context,
            "metric_id",
            "label",
            "value",
            "unit",
            "dimension",
            "dimension_value",
            "data_status",
            "evidence_state",
            "cache_state",
            "policy_type",
        ),
        "overview": {
            "metrics": _copy_mapping(
                overview.get("metrics") if isinstance(overview, Mapping) else {}
            )
        },
        "trust": _pick(
            trust if isinstance(trust, Mapping) else {},
            "coverage",
            "freshness",
            "data_status",
        ),
        "selected_evidence_summaries": projected_evidence,
        "evidence_refs": refs,
        "evidence_catalog": catalog,
        "evidence_gaps": [] if refs else ["当前指标缺少请求级证据"],
    }


def build_finops_agent_input(
    query: FinOpsQuery,
    query_service: Any,
    *,
    anomalies: Iterable[Mapping[str, Any]] = (),
    price_card_revision: str | None = None,
    evidence_name_resolver: Callable[[Mapping[str, Any]], str] | None = None,
    evidence_refs: Iterable[str] | None = None,
) -> dict[str, Any]:
    overview = query_service.overview(query)
    trends = query_service.trends(query, "day")
    departments = query_service.breakdowns(query, "department")
    workspaces = query_service.breakdowns(query, "workspace")
    requested_refs = (
        None
        if evidence_refs is None
        else list(dict.fromkeys(
            str(value or "").strip()
            for value in evidence_refs
            if str(value or "").strip()
        ))[:3]
    )
    if requested_refs is None:
        request_page = query_service.requests(query)
        evidence_items = [
            item
            for item in request_page.get("items", [])[:50]
            if isinstance(item, Mapping) and str(item.get("request_ref") or "").strip()
        ]
    else:
        events_by_ref = {
            event.request_ref: event
            for event in query_service.events(query)
        }
        evidence_items = [
            events_by_ref[request_ref].model_dump(mode="json")
            for request_ref in requested_refs
            if request_ref in events_by_ref
        ]
    selected_evidence_refs = [
        str(item.get("request_ref") or "").strip()
        for item in evidence_items
    ]
    evidence_catalog = [
        {
            "ref": str(item.get("request_ref") or "").strip(),
            "display_name": _evidence_display_name(
                item,
                index=index,
                resolver=evidence_name_resolver,
            ),
        }
        for index, item in enumerate(evidence_items)
    ]
    if not selected_evidence_refs:
        return {
            "status": "insufficient_data",
            "agent_kind": "finops",
            "scope": {"workspace_ids": list(_selected_workspace_ids(query))},
            "window": {"from": query.from_value, "to": query.to_value},
            "evidence_refs": [],
            "evidence_catalog": [],
            "evidence_gaps": ["请求级成本证据不足"],
        }
    return {
        "status": "ready",
        "agent_kind": "finops",
        "scope": {"workspace_ids": list(_selected_workspace_ids(query))},
        "window": {"from": query.from_value, "to": query.to_value},
        "overview": {"metrics": _copy_mapping(overview.get("metrics"))},
        "trends": [
            _pick(
                item,
                "bucket",
                "requests",
                "tokens",
                "estimated_cost",
                "data_status",
            )
            for item in trends.get("items", [])[-31:]
            if isinstance(item, Mapping)
        ],
        "breakdowns": {
            "departments": [
                _pick(
                    item,
                    "key",
                    "requests",
                    "tokens",
                    "estimated_cost",
                    "error_rate_pct",
                    "p95_latency_ms",
                    "data_status",
                )
                for item in departments.get("items", [])[:20]
                if isinstance(item, Mapping)
            ],
            "workspaces": [
                _pick(
                    item,
                    "key",
                    "requests",
                    "tokens",
                    "estimated_cost",
                    "error_rate_pct",
                    "p95_latency_ms",
                    "data_status",
                )
                for item in workspaces.get("items", [])[:20]
                if isinstance(item, Mapping)
            ],
        },
        "anomalies": [
            _pick(
                item,
                "anomaly_id",
                "policy_type",
                "severity",
                "status",
                "observed_value",
                "threshold_value",
                "sample_count",
                "evidence_refs",
            )
            for item in list(anomalies)[:20]
            if isinstance(item, Mapping)
        ],
        "price_card_revision": (
            str(price_card_revision or "").strip()[:160] or None
        ),
        "evidence_refs": list(dict.fromkeys(selected_evidence_refs)),
        "evidence_catalog": evidence_catalog,
        "evidence_gaps": [],
    }


def _evidence_display_name(
    item: Mapping[str, Any],
    *,
    index: int,
    resolver: Callable[[Mapping[str, Any]], str] | None,
) -> str:
    if resolver is not None:
        try:
            resolved = " ".join(str(resolver(item) or "").split())[:320]
        except (KeyError, TypeError, ValueError):
            resolved = ""
        if resolved:
            return resolved
    return f"运营证据 {index + 1}"


def build_roi_agent_input(
    workspace_id: str,
    window: Mapping[str, Any],
    roi_snapshot: Mapping[str, Any],
    verified_outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized_window = {
        "from": str(window.get("from") or ""),
        "to": str(window.get("to") or ""),
    }
    allowed_ids = {
        str(value or "").strip()
        for value in roi_snapshot.get("verified_outcome_event_ids", [])
        if str(value or "").strip()
    }
    projected: list[dict[str, Any]] = []
    for item in verified_outcomes:
        if not isinstance(item, Mapping):
            continue
        event_id = str(item.get("event_id") or "").strip()
        verification = (
            item.get("verification")
            if isinstance(item.get("verification"), Mapping)
            else {}
        )
        if (
            not event_id
            or event_id not in allowed_ids
            or str(item.get("workspace_id") or "").strip() != workspace_id
            or str(verification.get("status") or "").strip().lower() != "verified"
        ):
            continue
        business_value = (
            item.get("business_value")
            if isinstance(item.get("business_value"), Mapping)
            else {}
        )
        projected.append(
            {
                "event_id": event_id,
                "observed_at": item.get("observed_at"),
                "observed_value": item.get("observed_value"),
                "business_value": _pick(
                    business_value,
                    "value",
                    "currency",
                    "formula",
                    "status",
                ),
                "verification_status": "verified",
            }
        )
        if len(projected) >= 50:
            break
    if not projected:
        return {
            "status": "insufficient_data",
            "agent_kind": "roi",
            "workspace_id": workspace_id,
            "window": normalized_window,
            "evidence_refs": [],
            "evidence_gaps": ["已验证结果事件不足"],
        }
    evidence_refs = [item["event_id"] for item in projected]
    return {
        "status": "ready",
        "agent_kind": "roi",
        "workspace_id": workspace_id,
        "window": normalized_window,
        "roi_snapshot": {
            "status": roi_snapshot.get("status"),
            "cost": _copy_mapping(roi_snapshot.get("cost")),
            "business_value": _copy_mapping(roi_snapshot.get("business_value")),
            "lineage_complete": roi_snapshot.get("lineage_complete"),
            "truncated": bool(roi_snapshot.get("truncated")),
        },
        "verified_outcomes": projected,
        "evidence_refs": evidence_refs,
        "evidence_gaps": [],
    }


def _selected_workspace_ids(query: FinOpsQuery) -> tuple[str, ...]:
    return (
        (query.workspace_id,)
        if query.workspace_id
        else query.authorized_workspace_ids
    )


def _pick(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value.get(key) for key in keys if key in value}


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}

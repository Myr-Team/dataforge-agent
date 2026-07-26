from __future__ import annotations

import math
from typing import Any, Mapping


def build_roi_economics(
    *,
    cost_evidence: Mapping[str, Any],
    outcome_evidence: Mapping[str, Any],
    realized_roi: Mapping[str, Any],
    requests: int,
    successful_requests: int,
    analyses: int,
    artifacts: int,
    scenarios: list[Mapping[str, Any]],
) -> dict[str, Any]:
    complete_cost = (
        str(cost_evidence.get("status") or "") == "complete"
        and _finite_nonnegative(cost_evidence.get("total"))
        and str(cost_evidence.get("currency") or "") == "USD"
    )
    cost_total = float(cost_evidence["total"]) if complete_cost else None
    verified_outcomes = [
        str(value)
        for value in outcome_evidence.get("verified_outcome_event_ids") or []
        if str(value).strip()
    ]
    all_outcomes = [
        str(value)
        for value in outcome_evidence.get("outcome_event_ids") or []
        if str(value).strip()
    ]
    verified = (
        str(outcome_evidence.get("status") or "") == "verified"
        and bool(all_outcomes)
        and set(all_outcomes) == set(verified_outcomes)
    )

    def unit(denominator: int, label: str) -> dict[str, Any]:
        value = (
            round(cost_total / denominator, 8)
            if cost_total is not None and denominator > 0
            else None
        )
        return {
            "label": label,
            "value": value,
            "currency": "USD" if value is not None else None,
            "status": "estimated" if value is not None else "unavailable",
        }

    realized_status = str(realized_roi.get("status") or "not_recorded")
    realized_value = (
        realized_roi.get("roi_ratio")
        if complete_cost and verified and realized_status == "verified"
        else None
    )
    gaps = []
    if not complete_cost:
        gaps.append("完整成本证据")
    if not verified:
        gaps.append("独立验证的业务结果")
    if artifacts <= 0:
        gaps.append("可计数的交付产物")

    return {
        "funnel": [
            {
                "id": "investment",
                "label": "投入",
                "value": cost_total,
                "unit": "USD",
                "status": "estimated" if complete_cost else "unavailable",
            },
            {
                "id": "usage",
                "label": "使用",
                "value": max(0, requests),
                "unit": "次调用",
                "status": "observed" if requests > 0 else "unavailable",
            },
            {
                "id": "output",
                "label": "产出",
                "value": max(0, artifacts),
                "unit": "个产物",
                "secondary_value": max(0, analyses),
                "status": "observed" if artifacts > 0 or analyses > 0 else "unavailable",
            },
            {
                "id": "outcome",
                "label": "业务结果",
                "value": len(verified_outcomes) if verified else None,
                "unit": "项已验证结果",
                "status": "verified" if verified else str(outcome_evidence.get("status") or "not_recorded"),
            },
        ],
        "unit_economics": {
            "cost_per_successful_request": unit(successful_requests, "每次成功调用成本"),
            "cost_per_analysis": unit(analyses, "每次分析成本"),
            "cost_per_artifact": unit(artifacts, "每个产物成本"),
        },
        "verified_roi": {
            "status": realized_status,
            "value": realized_value,
            "currency": realized_roi.get("currency") if realized_value is not None else None,
            "net_value": realized_roi.get("net_value") if realized_value is not None else None,
        },
        "evidence_gaps": gaps,
        "scenarios": [
            dict(item)
            for item in scenarios[:20]
            if str(item.get("status") or "") == "estimated"
        ],
        "data_status": "complete" if complete_cost and verified else "partial",
    }


def _finite_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0

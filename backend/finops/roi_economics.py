from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RoiCalculation:
    monthly_benefit: float
    implementation_amortization: float
    monthly_total_cost: float
    monthly_net_benefit: float
    roi_ratio: float | None
    payback_months: float | None
    formula_revision: str = "dataforge-roi-v1"


def calculate_roi(
    *,
    hours_saved: float,
    hourly_value: float,
    avoided_loss_or_revenue: float,
    implementation_cost: float,
    monthly_fixed_cost: float,
    model_cost: float,
    evaluation_months: int,
) -> RoiCalculation:
    values = {
        "hours_saved": hours_saved,
        "hourly_value": hourly_value,
        "avoided_loss_or_revenue": avoided_loss_or_revenue,
        "implementation_cost": implementation_cost,
        "monthly_fixed_cost": monthly_fixed_cost,
        "model_cost": model_cost,
    }
    normalized: dict[str, float] = {}
    for field, value in values.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be numeric") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{field} must be finite and non-negative")
        normalized[field] = number
    if isinstance(evaluation_months, bool):
        raise ValueError("evaluation_months must be a positive integer")
    try:
        months = int(evaluation_months)
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation_months must be a positive integer") from exc
    if months <= 0 or float(evaluation_months) != months:
        raise ValueError("evaluation_months must be a positive integer")

    monthly_benefit = (
        normalized["hours_saved"] * normalized["hourly_value"]
        + normalized["avoided_loss_or_revenue"]
    )
    implementation_amortization = normalized["implementation_cost"] / months
    monthly_total_cost = (
        implementation_amortization
        + normalized["monthly_fixed_cost"]
        + normalized["model_cost"]
    )
    monthly_net_benefit = monthly_benefit - monthly_total_cost
    roi_ratio = (
        monthly_net_benefit / monthly_total_cost
        if monthly_total_cost > 0
        else None
    )
    recurring_net_benefit = (
        monthly_benefit
        - normalized["monthly_fixed_cost"]
        - normalized["model_cost"]
    )
    payback_months = (
        normalized["implementation_cost"] / recurring_net_benefit
        if recurring_net_benefit > 0
        else None
    )
    return RoiCalculation(
        monthly_benefit=monthly_benefit,
        implementation_amortization=implementation_amortization,
        monthly_total_cost=monthly_total_cost,
        monthly_net_benefit=monthly_net_benefit,
        roi_ratio=roi_ratio,
        payback_months=payback_months,
    )


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

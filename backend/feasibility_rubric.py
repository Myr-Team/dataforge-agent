from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = ROOT / "agents" / "rubrics" / "feasibility.json"
GUARDRAIL_VERSION = "batch11-p0-guardrails-v1"

VERDICT_LABELS = {
    "feasible": "可行",
    "conditional": "有条件可行",
    "not_yet_feasible": "暂不可行",
    "unknown": "待判断",
}
CONFIDENCE_LABELS = {
    "data_confirmed": "数据已证实",
    "market_inferred": "市场推断",
    "speculative": "证据不足",
    "unknown": "待判断",
}
AUDIT_LABELS = {
    "pass": "通过",
    "revise": "需修订",
    "not_run": "未运行",
}


def load_rubric(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or RUBRIC_PATH).read_text(encoding="utf-8"))


def rubric_version(rubric: dict[str, Any] | None = None) -> str:
    return str((rubric or load_rubric()).get("rubric_version") or "unknown-rubric")


def dimension_label(name: Any, rubric: dict[str, Any] | None = None) -> str:
    value = str(name or "").strip()
    for dimension in (rubric or load_rubric()).get("dimensions") or []:
        if str(dimension.get("name") or "") == value:
            return str(dimension.get("display_name") or value)
    return value or "未命名维度"


def verdict_label(value: Any) -> str:
    return VERDICT_LABELS.get(str(value or "").strip(), str(value or "待判断"))


def confidence_label(value: Any) -> str:
    return CONFIDENCE_LABELS.get(str(value or "").strip(), str(value or "待判断"))


def audit_label(value: Any) -> str:
    return AUDIT_LABELS.get(str(value or "").strip(), str(value or "未运行"))


def rubric_dimensions(rubric: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = rubric or load_rubric()
    return [
        {
            "name": item.get("name"),
            "display_name": item.get("display_name"),
            "weight": item.get("weight"),
        }
        for item in data.get("dimensions") or []
    ]


def attach_rubric_metadata(report: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    data = copy.deepcopy(report)
    active = rubric or load_rubric()
    scorecard, weighted = score_report_dimensions(data, active)
    data["rubric_version"] = rubric_version(active)
    data["rubric_dimensions"] = rubric_dimensions(active)
    data["rubric_scorecard"] = scorecard
    data["rubric_weighted_score"] = weighted
    data["guardrail_version"] = GUARDRAIL_VERSION
    return data


def score_report_dimensions(report: dict[str, Any], rubric: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], float]:
    active = rubric or load_rubric()
    by_name = {
        str(item.get("name") or ""): max(0.0, min(5.0, _float(item.get("score"), 0.0)))
        for item in report.get("dimensions") or []
        if isinstance(item, dict)
    }
    scorecard: list[dict[str, Any]] = []
    weighted = 0.0
    for dimension in active.get("dimensions") or []:
        name = str(dimension.get("name") or "")
        weight = _float(dimension.get("weight"), 0.0)
        score = by_name.get(name, 0.0)
        weighted += score * weight
        scorecard.append(
            {
                "name": name,
                "display_name": dimension.get("display_name") or name,
                "weight": weight,
                "score": round(score, 1),
                "missing": name not in by_name,
            }
        )
    return scorecard, round(weighted, 1)


def expected_verdict(report: dict[str, Any], rubric: dict[str, Any] | None = None) -> str:
    active = rubric or load_rubric()
    scorecard, weighted = score_report_dimensions(report, active)
    scores = {str(item["name"]): _float(item.get("score"), 0.0) for item in scorecard}
    missing = sum(1 for item in scorecard if item.get("missing"))
    thresholds = active.get("verdict_thresholds") or {}
    feasible = thresholds.get("feasible") or {}
    conditional = thresholds.get("conditional") or {}
    if (
        weighted >= _float(feasible.get("weighted_min"), 3.8)
        and scores.get("asset_data", 0.0) >= _float(feasible.get("asset_data_min"), 3.5)
        and scores.get("market", 0.0) >= _float(feasible.get("market_min"), 3.0)
        and missing <= int(feasible.get("max_missing_material_dimensions", 0))
    ):
        return "feasible"
    if weighted >= _float(conditional.get("weighted_min"), 2.4) and scores.get("asset_data", 0.0) >= _float(
        conditional.get("asset_data_min"), 2.0
    ):
        return "conditional"
    return "not_yet_feasible"


def apply_pre_audit_guardrails(report: dict[str, Any], catalog: list[dict[str, Any]], user_request: str) -> dict[str, Any]:
    data = copy.deepcopy(report)
    notes: list[str] = []
    if _has_preset_outcome_request(user_request):
        _cap_scores(data, 3)
        if data.get("verdict") == "feasible":
            data["verdict"] = "conditional"
        data["overall_confidence"] = _min_confidence(data.get("overall_confidence"), "market_inferred")
        _append_gap(data, "不能按预设结论直接判为可行或打高分；以下判断只按工作区证据给出。")
        notes.append("preset_outcome_request_rejected")
    if len(catalog) == 0:
        data["dimensions"] = []
        data["verdict"] = "not_yet_feasible"
        data["overall_confidence"] = "speculative"
        _append_gap(data, "当前没有可核验的工作区证据，不能形成可行性结论。")
        notes.append("empty_evidence_forces_not_yet_feasible")
    elif len(catalog) < 2:
        _cap_dimension_score(data, "asset_data", 2)
        if data.get("verdict") == "feasible":
            data["verdict"] = "conditional"
        data["overall_confidence"] = _min_confidence(data.get("overall_confidence"), "speculative")
        _append_gap(data, "可核验证据少于 2 条，数据充分度不足，不能给出高置信判断。")
        notes.append("thin_evidence_caps_confidence")
    data = attach_rubric_metadata(data)
    expected = expected_verdict(data)
    if _verdict_rank(data.get("verdict")) > _verdict_rank(expected):
        data["verdict"] = expected
        _append_gap(data, f"按当前 rubric 复核后，结论降级为“{verdict_label(expected)}”。")
        notes.append("rubric_expected_verdict_cap")
    if notes:
        data.setdefault("guardrails", [])
        data["guardrails"] = list(dict.fromkeys([*data.get("guardrails", []), *notes]))
    return data


def apply_post_audit_guardrails(
    report: dict[str, Any],
    blind_report: dict[str, Any],
    catalog: list[dict[str, Any]],
    audit: dict[str, Any] | None,
) -> dict[str, Any]:
    data = copy.deepcopy(report)
    notes: list[str] = []
    audit_issues = " ".join(str(item) for item in (audit or {}).get("issues") or []).lower()
    audit_requires_revision = str((audit or {}).get("verdict") or "") == "revise"
    thin_evidence = len(catalog) < 3
    blind_rank = _verdict_rank((blind_report or {}).get("verdict"))
    if thin_evidence and blind_rank >= _verdict_rank("conditional"):
        _cap_dimension_score(data, "asset_data", 2)
        if data.get("verdict") == "feasible":
            data["verdict"] = "conditional"
        if _verdict_rank(data.get("verdict")) > _verdict_rank("conditional"):
            data["verdict"] = "conditional"
        data["overall_confidence"] = _min_confidence(data.get("overall_confidence"), "speculative")
        _append_gap(data, "审计复核发现证据池偏薄，已下调数据充分度和整体置信度。")
        notes.append("post_audit_thin_evidence_revision")
    if audit_requires_revision and re.search(r"over.?strong|too strongly|missing evidence|lacks evidence|证据不足|过强", audit_issues):
        if data.get("verdict") == "feasible":
            data["verdict"] = "conditional"
        data["overall_confidence"] = _min_confidence(data.get("overall_confidence"), "market_inferred")
        _append_gap(data, "审计员认为原结论强度超过证据支撑，已保守修订。")
        notes.append("audit_strength_revision")
    data = attach_rubric_metadata(data)
    expected = expected_verdict(data)
    if _verdict_rank(data.get("verdict")) > _verdict_rank(expected):
        data["verdict"] = expected
        _append_gap(data, f"按当前 rubric 复核后，结论降级为“{verdict_label(expected)}”。")
        notes.append("post_audit_rubric_expected_verdict_cap")
    if notes:
        data.setdefault("guardrails", [])
        data["guardrails"] = list(dict.fromkeys([*data.get("guardrails", []), *notes]))
    return data


def make_blind_verdict(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "judgment": verdict_label(report.get("verdict")),
        "confidence": str(report.get("overall_confidence") or "speculative"),
        "rubric_version": str(report.get("rubric_version") or rubric_version()),
        "weighted_score": report.get("rubric_weighted_score"),
    }


def finalize_verdict_contract(artifact: dict[str, Any], audit: dict[str, Any] | None = None) -> dict[str, Any]:
    blind = copy.deepcopy(artifact.get("_blind_feasibility") or artifact.get("feasibility") or {})
    current = copy.deepcopy(artifact.get("feasibility") or {})
    disagreement = _dimension_disagreement(blind, current, audit)
    changed = _verdict_changed(blind, current) or bool(disagreement)
    before_verdict = str(blind.get("verdict") or "")
    after_verdict = str(current.get("verdict") or "")
    dimension_downgrade = next(
        (
            item
            for item in disagreement
            if isinstance(item.get("delta"), (int, float)) and float(item.get("delta") or 0) < 0
        ),
        None,
    )
    verdict_downgrade = bool(
        before_verdict
        and after_verdict
        and _verdict_rank(after_verdict) < _verdict_rank(before_verdict)
    )
    downgraded = verdict_downgrade or bool(dimension_downgrade)
    contract = {
        "blind": make_blind_verdict(blind),
        "revised": make_blind_verdict(current) if changed else None,
        "revised_by": "审计员" if changed else None,
        "disagreement": disagreement,
    }
    if downgraded:
        reason = _downgrade_reason(disagreement, audit, current)
        downgrade = {
            "kind": "verdict" if verdict_downgrade else "dimension",
            "verdict_before": before_verdict,
            "verdict_after": after_verdict,
            "verdict_before_label": verdict_label(before_verdict),
            "verdict_after_label": verdict_label(after_verdict),
            "downgrade_reason": reason,
            "source": "audit_guardrail",
        }
        if dimension_downgrade:
            downgrade.update(
                {
                    "dimension": dimension_downgrade.get("dim"),
                    "score_before": dimension_downgrade.get("blind"),
                    "score_after": dimension_downgrade.get("revised"),
                }
            )
        contract.update(downgrade)
        contract["downgrade"] = downgrade
        artifact["verdict_downgrade"] = downgrade
    artifact["verdict"] = contract
    return contract


def score_dataset_records(records: list[dict[str, Any]], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    active = rubric or load_rubric()
    row_count = len(records)
    keys = sorted({key for row in records for key in row})
    numeric_values: dict[str, list[float]] = {key: [] for key in keys}
    text_blob = json.dumps(records, ensure_ascii=False).lower()
    missing = 0
    total = 0
    for row in records:
        for key in keys:
            total += 1
            value = row.get(key)
            if value in (None, ""):
                missing += 1
                continue
            number = _number(value)
            if number is not None:
                numeric_values[key].append(number)
    completeness = 1 - (missing / total) if total else 0
    numeric_fields = [key for key, values in numeric_values.items() if values]
    distinct_signal = sum(1 for key in keys if len({str(row.get(key, "")) for row in records if row.get(key, "") != ""}) >= 2)
    pain_terms = len(re.findall(r"pain|risk|churn|需求|痛点|投诉|高|增长|复购|转化|budget|revenue|cost|score", text_blob))
    outcome_terms = len(re.findall(r"revenue|conversion|retention|score|cost|rate|orders|visits|收入|转化|复购|成本|评分|到访", text_blob))

    scores = {
        "asset_data": min(5.0, 0.7 + row_count * 0.25 + len(keys) * 0.18 + completeness * 1.1),
        "market": min(5.0, 0.6 + min(pain_terms, 8) * 0.38 + distinct_signal * 0.16),
        "technical": min(5.0, 0.8 + len(numeric_fields) * 0.55 + len(keys) * 0.13 + min(row_count, 12) * 0.08),
        "resource_cost": min(5.0, 3.2 if row_count >= 4 and len(keys) >= 4 else 2.0 + row_count * 0.2),
        "differentiation_risk": min(5.0, 0.8 + distinct_signal * 0.28 + min(outcome_terms, 8) * 0.22),
    }
    if row_count < 3:
        scores["asset_data"] = min(scores["asset_data"], 2.0)
        scores["resource_cost"] = min(scores["resource_cost"], 2.4)
    dimensions = [
        {"name": item.get("name"), "score": round(scores.get(str(item.get("name")), 0.0), 1)}
        for item in active.get("dimensions") or []
    ]
    report = {"dimensions": dimensions, "verdict": "conditional", "overall_confidence": "data_confirmed", "gap_list": []}
    report = attach_rubric_metadata(report, active)
    report["verdict"] = expected_verdict(report, active)
    return report


def _dimension_disagreement(blind: dict[str, Any], current: dict[str, Any], audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    active = load_rubric()
    blind_scores = {str(item.get("name")): _float(item.get("score"), 0.0) for item in blind.get("dimensions") or []}
    current_scores = {str(item.get("name")): _float(item.get("score"), 0.0) for item in current.get("dimensions") or []}
    issues = "; ".join(str(item) for item in (audit or {}).get("issues") or [])
    rows: list[dict[str, Any]] = []
    for name in sorted(set(blind_scores) | set(current_scores)):
        before = blind_scores.get(name, 0.0)
        after = current_scores.get(name, 0.0)
        delta = round(after - before, 1)
        if math.isclose(delta, 0.0):
            continue
        rows.append(
            {
                "dim": dimension_label(name, active),
                "blind": before,
                "revised": after,
                "delta": delta,
                "reason": issues[:180] or "审计或 guardrail 复核后调整。",
            }
        )
    if not rows and _verdict_changed(blind, current):
        rows.append(
            {
                "dim": "总体结论",
                "blind": verdict_label(blind.get("verdict")),
                "revised": verdict_label(current.get("verdict")),
                "delta": None,
                "reason": issues[:180] or "审计或 guardrail 复核后调整。",
            }
        )
    return rows


def _verdict_changed(blind: dict[str, Any], current: dict[str, Any]) -> bool:
    return (
        str(blind.get("verdict") or "") != str(current.get("verdict") or "")
        or str(blind.get("overall_confidence") or "") != str(current.get("overall_confidence") or "")
    )


def _downgrade_reason(disagreement: list[dict[str, Any]], audit: dict[str, Any] | None, current: dict[str, Any]) -> str:
    for item in disagreement:
        reason = str(item.get("reason") or "").strip()
        if reason:
            return reason[:220]
    for issue in (audit or {}).get("issues") or []:
        text = str(issue or "").strip()
        if text:
            return text[:220]
    for gap in current.get("gap_list") or []:
        text = str(gap or "").strip()
        if text:
            return text[:220]
    return "审计复核后认为证据强度不足以支撑原结论。"


def _has_preset_outcome_request(text: str) -> bool:
    return bool(
        re.search(
            r"(无论如何|不管证据|不管资料|一定|必须|直接).{0,12}(可行|高分|通过)|打高分|always say feasible|force feasible",
            text,
            re.I,
        )
    )


def _append_gap(data: dict[str, Any], gap: str) -> None:
    gaps = [str(item) for item in data.get("gap_list") or [] if str(item).strip()]
    if gap not in gaps:
        gaps.append(gap)
    data["gap_list"] = gaps


def _cap_scores(data: dict[str, Any], cap: int | float) -> None:
    for dimension in data.get("dimensions") or []:
        if isinstance(dimension, dict):
            dimension["score"] = min(_float(dimension.get("score"), 0.0), float(cap))


def _cap_dimension_score(data: dict[str, Any], name: str, cap: int | float) -> None:
    for dimension in data.get("dimensions") or []:
        if isinstance(dimension, dict) and dimension.get("name") == name:
            dimension["score"] = min(_float(dimension.get("score"), 0.0), float(cap))


def _min_confidence(left: Any, right: str) -> str:
    rank = {"speculative": 0, "market_inferred": 1, "data_confirmed": 2}
    labels = {value: key for key, value in rank.items()}
    return labels[min(rank.get(str(left or "speculative"), 0), rank.get(right, 0))]


def _verdict_rank(value: Any) -> int:
    return {"not_yet_feasible": 0, "conditional": 1, "feasible": 2}.get(str(value or ""), 0)


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None

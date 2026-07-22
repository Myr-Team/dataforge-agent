from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTEXT_EVALUATION_CASES_PATH = ROOT / "eval" / "context_optimization_cases.json"
DEFAULT_CONTEXT_EVALUATION_SUMMARY_PATH = ROOT / "eval" / "context_optimization_summary.json"
DEFAULT_STALE_DAYS = 30
_METRIC_KEYS = ("evidence_coverage", "completion")


EvaluationRunner = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class EvaluationSummary:
    sample_count: int
    baseline: Mapping[str, float]
    candidate: Mapping[str, float]
    evaluator_version: str
    route_id: str = "followup"
    status: str = "evaluated"
    generated_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "status": self.status,
            "generated_at": self.generated_at,
            "sample_count": self.sample_count,
            "evaluator_version": self.evaluator_version,
            "baseline": _metrics_payload(self.baseline),
            "candidate": _metrics_payload(self.candidate),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EvaluationSummary":
        route_id = str(payload.get("route_id") or "followup").strip().lower() or "followup"
        status = str(payload.get("status") or "evaluated").strip().lower() or "evaluated"
        evaluator_version = str(payload.get("evaluator_version") or "").strip()
        if not evaluator_version:
            raise ValueError("evaluator_version is required")
        sample_count = _as_int(payload.get("sample_count"))
        if sample_count is None or sample_count < 0:
            raise ValueError("sample_count is invalid")
        generated_at = _normalized_time(payload.get("generated_at"))
        if payload.get("generated_at") is not None and generated_at is None:
            raise ValueError("generated_at is invalid")
        baseline = _validated_metrics(payload.get("baseline"))
        candidate = _validated_metrics(payload.get("candidate"))
        return cls(
            sample_count=sample_count,
            baseline=baseline,
            candidate=candidate,
            evaluator_version=evaluator_version,
            route_id=route_id,
            status=status,
            generated_at=generated_at,
        )


def candidate_route_eligible(summary: EvaluationSummary) -> bool:
    return (
        summary.sample_count >= 20
        and summary.candidate["evidence_coverage"] >= summary.baseline["evidence_coverage"]
        and summary.candidate["completion"] >= summary.baseline["completion"]
    )


def evaluate_context_candidate(
    cases: Sequence[Mapping[str, Any]],
    runner: EvaluationRunner,
    *,
    route_id: str = "followup",
    evaluator_version: str,
    generated_at: str | None = None,
) -> EvaluationSummary:
    baseline_totals = {key: 0.0 for key in _METRIC_KEYS}
    candidate_totals = {key: 0.0 for key in _METRIC_KEYS}
    sample_count = 0
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        result = runner(case, variant="paired")
        if not isinstance(result, Mapping):
            raise ValueError("runner must return a mapping")
        baseline = _validated_metrics(result.get("baseline"))
        candidate = _validated_metrics(result.get("candidate"))
        for key in _METRIC_KEYS:
            baseline_totals[key] += baseline[key]
            candidate_totals[key] += candidate[key]
        sample_count += 1
    return EvaluationSummary(
        route_id=str(route_id or "followup").strip().lower() or "followup",
        status="evaluated",
        generated_at=_normalized_time(generated_at),
        sample_count=sample_count,
        evaluator_version=str(evaluator_version or "").strip(),
        baseline={key: _rounded_ratio(baseline_totals[key], sample_count) for key in _METRIC_KEYS},
        candidate={key: _rounded_ratio(candidate_totals[key], sample_count) for key in _METRIC_KEYS},
    )


def load_context_optimization_cases(path: Path | str = DEFAULT_CONTEXT_EVALUATION_CASES_PATH) -> list[dict[str, Any]]:
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("context optimization cases must be a list")
    return [dict(item) for item in raw if isinstance(item, dict)]


def load_evaluation_gate(
    path: Path | str = DEFAULT_CONTEXT_EVALUATION_SUMMARY_PATH,
    *,
    route_id: str = "followup",
    now: str | datetime | None = None,
    stale_after_days: int | None = None,
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return _gate_projection("unavailable", None, None, False)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _gate_projection("malformed", None, None, False)
    if not isinstance(payload, Mapping):
        return _gate_projection("malformed", None, None, False)
    if str(payload.get("route_id") or "followup").strip().lower() != str(route_id or "followup").strip().lower():
        return _gate_projection("unavailable", None, None, False)
    try:
        summary = EvaluationSummary.from_payload(payload)
    except ValueError:
        return _gate_projection("malformed", None, None, False)
    if _is_stale(summary, now=now, stale_after_days=stale_after_days):
        return _gate_projection("stale", summary.sample_count, summary.evaluator_version, False)
    status = summary.status if summary.status else "evaluated"
    return _gate_projection(status, summary.sample_count, summary.evaluator_version, status == "evaluated" and candidate_route_eligible(summary))


def _is_stale(
    summary: EvaluationSummary,
    *,
    now: str | datetime | None = None,
    stale_after_days: int | None = None,
) -> bool:
    generated_at = _parse_time(summary.generated_at)
    if generated_at is None:
        return True
    max_age_days = stale_after_days if stale_after_days is not None else _configured_stale_days()
    current = _parse_time(now) or datetime.now(timezone.utc)
    return generated_at < current - timedelta(days=max_age_days)


def _configured_stale_days() -> int:
    value = _as_int(os.environ.get("DF_CONTEXT_EVALUATION_STALE_DAYS"))
    return value if value is not None and value > 0 else DEFAULT_STALE_DAYS


def _gate_projection(status: str, sample_count: int | None, evaluator_version: str | None, eligible: bool) -> dict[str, Any]:
    return {
        "status": str(status or "unavailable").strip().lower() or "unavailable",
        "sample_count": sample_count if isinstance(sample_count, int) else None,
        "evaluator_version": str(evaluator_version).strip() if evaluator_version else None,
        "eligible": bool(eligible),
    }


def _validated_metrics(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("metrics payload is invalid")
    metrics: dict[str, float] = {}
    for key in _METRIC_KEYS:
        ratio = _as_float(value.get(key))
        if ratio is None or ratio < 0 or ratio > 1:
            raise ValueError(f"{key} is invalid")
        metrics[key] = round(ratio, 2)
    return metrics


def _metrics_payload(value: Mapping[str, float]) -> dict[str, float]:
    return {key: round(float(value[key]), 2) for key in _METRIC_KEYS}


def _rounded_ratio(total: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return round(float(total) / float(count), 2)


def _normalized_time(value: Any) -> str | None:
    parsed = _parse_time(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None

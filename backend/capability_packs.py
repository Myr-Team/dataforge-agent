"""Data-driven capability-pack selection without dataset-name heuristics."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from numbers import Real
from pathlib import Path
from typing import Any

from .schemas import CapabilityPack, CapabilitySelection


_PACK_FILE = Path(__file__).resolve().parent / "data" / "capability_packs.json"
_OPPORTUNITY_PACK_IDS = frozenset(
    {
        "growth_retention",
        "pricing_productization",
        "site_channel_selection",
        "operations",
        "campaign_service",
    }
)
_READINESS_PACK_ID = "risk_data_readiness"
_MIN_OPPORTUNITY_CONFIDENCE = 0.34
_PROFILE_CONTRACT = ("schema_roles", "metric_families", "entity_relationships")
_RELATION_CONNECTORS = frozenset({"to", "and", "by", "of", "for"})


@dataclass(frozen=True)
class _QualityAvailability:
    score: float
    valid: bool


@dataclass(frozen=True)
class _TemporalSuitability:
    score: float
    valid: bool


def _normalize_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "_", str(value or "").casefold()).strip("_")
    if text.endswith("s") and len(text) > 3:
        text = text[:-1]
    return text


def _goal_terms(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    raw_values: list[str] = []
    for token in re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+", value.casefold()):
        raw_values.append(token)
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            for size in (2, 3, 4):
                raw_values.extend(token[index : index + size] for index in range(max(0, len(token) - size + 1)))
    normalized: set[str] = set()
    for item in raw_values:
        term = _normalize_text(item)
        if not term:
            continue
        normalized.add(term)
        normalized.update(part for part in term.split("_") if part)
    return normalized


def _normalized_profile_terms(value: Any, allowed_terms: set[str]) -> set[str]:
    """Accept only a sequence of declared semantic terms, never raw column objects."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {
        normalized
        for item in value
        if isinstance(item, str)
        if (normalized := _normalize_text(item)) in allowed_terms
    }


def _normalized_relationship_roles(value: Any, allowed_roles: set[str]) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    matched_roles: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        parts = [part for part in _normalize_text(item).split("_") if part and part not in _RELATION_CONNECTORS]
        if len(parts) >= 2 and all(part in allowed_roles for part in parts):
            matched_roles.update(parts)
    return matched_roles


def _finite_fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number < 0 or number > 100:
        return None
    if number > 1:
        number /= 100
    return number


def _validated_quality_values(quality: Mapping[str, Any], keys: Sequence[str]) -> list[float] | None:
    values: list[float] = []
    for key in keys:
        if key not in quality:
            continue
        fraction = _finite_fraction(quality[key])
        if fraction is None:
            return None
        values.append(fraction)
    return values


def _quality_availability(quality: Mapping[str, Any] | None) -> _QualityAvailability:
    if not isinstance(quality, Mapping):
        return _QualityAvailability(score=0.0, valid=False)
    completeness_values = _validated_quality_values(quality, ("completeness",))
    missing_values = _validated_quality_values(quality, ("missing_rate", "missing_pct"))
    duplicate_values = _validated_quality_values(quality, ("duplicate_rate", "duplicate_pct"))
    if completeness_values is None or missing_values is None or duplicate_values is None:
        return _QualityAvailability(score=0.0, valid=False)
    evidence_completeness = [*completeness_values, *(1.0 - value for value in missing_values)]
    if not evidence_completeness or not duplicate_values:
        return _QualityAvailability(score=0.0, valid=False)
    if max(evidence_completeness) - min(evidence_completeness) > 0.05:
        return _QualityAvailability(score=0.0, valid=False)
    if max(duplicate_values) - min(duplicate_values) > 0.05:
        return _QualityAvailability(score=0.0, valid=False)
    completeness = sum(evidence_completeness) / len(evidence_completeness)
    duplicate_rate = sum(duplicate_values) / len(duplicate_values)
    return _QualityAvailability(
        score=max(0.0, min(1.0, completeness * (1.0 - min(duplicate_rate, 0.8)))),
        valid=True,
    )


def _temporal_endpoint(value: Any) -> datetime | float | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _valid_temporal_evidence(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    for item in value:
        if not isinstance(item, Mapping):
            continue
        start = _temporal_endpoint(item.get("start"))
        end = _temporal_endpoint(item.get("end"))
        if start is not None and end is not None and type(start) is type(end) and start < end:
            return True
    return False


def _positive_period(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _temporal_suitability(profile: Mapping[str, Any]) -> _TemporalSuitability:
    coverage = profile.get("temporal_coverage")
    if not isinstance(coverage, Mapping) or coverage.get("available") is not True:
        return _TemporalSuitability(score=0.0, valid=False)
    period_values: list[float] = []
    for key in ("periods", "count"):
        if key not in coverage:
            continue
        period = _positive_period(coverage[key])
        if period is None:
            return _TemporalSuitability(score=0.0, valid=False)
        period_values.append(period)
    if period_values:
        return _TemporalSuitability(score=min(1.0, max(period_values) / 4), valid=True)
    if _valid_temporal_evidence(coverage.get("evidence")):
        return _TemporalSuitability(score=0.35, valid=True)
    return _TemporalSuitability(score=0.0, valid=False)


def _overlap(observed: set[str], expected: Sequence[str]) -> list[str]:
    expected_terms = {_normalize_text(value) for value in expected}
    return sorted(observed & expected_terms)


def _missing(expected: Sequence[str], matched: Sequence[str], prefix: str) -> list[str]:
    matched_set = set(matched)
    return [f"{prefix}: {value}" for value in expected if _normalize_text(value) not in matched_set]


@lru_cache(maxsize=1)
def _loaded_capability_packs() -> tuple[CapabilityPack, ...]:
    raw = json.loads(_PACK_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) != 6:
        raise ValueError("capability-pack registry must contain exactly six packs")
    packs = tuple(CapabilityPack.model_validate(item) for item in raw)
    ids = [pack.pack_id for pack in packs]
    if len(ids) != len(set(ids)) or set(ids) != _OPPORTUNITY_PACK_IDS | {_READINESS_PACK_ID}:
        raise ValueError("capability-pack registry IDs are invalid")
    return packs


def load_capability_packs() -> list[CapabilityPack]:
    """Load detached, data-only capability packs from the bundled registry."""
    return [pack.model_copy(deep=True) for pack in _loaded_capability_packs()]


def _readiness_selection(
    pack: CapabilityPack,
    *,
    roles: set[str],
    metrics: set[str],
    temporal: float,
    quality: float,
    reason: str,
) -> CapabilitySelection:
    matched_roles = _overlap(roles, pack.schema_roles)
    matched_metrics = _overlap(metrics, pack.metric_families)
    missing = _missing(pack.schema_roles, matched_roles, "schema role")
    missing.extend(_missing(pack.metric_families, matched_metrics, "metric family"))
    if temporal < 0.5:
        missing.append("time coverage")
    if quality < 0.6:
        missing.append("data quality")
    return CapabilitySelection(
        pack_id=pack.pack_id,
        confidence=round(min(1.0, 0.45 + (quality * 0.2) + (temporal * 0.1)), 4),
        reasons=[reason],
        matched_schema_roles=matched_roles,
        missing_evidence=list(dict.fromkeys(missing))[:24],
    )


def select_capability_packs(
    goal: str,
    schema_profile: Mapping[str, Any] | None,
    quality: Mapping[str, Any] | None,
) -> list[CapabilitySelection]:
    """Select packs from the explicit semantic-profile contract only.

    Accepted profile fields are string sequences in `schema_roles`,
    `metric_families`, and `entity_relationships`; temporal evidence is a
    separate typed `temporal_coverage` mapping. Raw columns, dataset labels,
    names, types, families, and any other profile keys are deliberately ignored.
    """
    profile = (
        {
            key: schema_profile.get(key)
            for key in (*_PROFILE_CONTRACT, "temporal_coverage")
        }
        if isinstance(schema_profile, Mapping)
        else {}
    )
    packs = {pack.pack_id: pack for pack in load_capability_packs()}
    allowed_roles = {_normalize_text(role) for pack in packs.values() for role in pack.schema_roles}
    allowed_metrics = {_normalize_text(metric) for pack in packs.values() for metric in pack.metric_families}
    goal_terms = _goal_terms(goal)
    roles = _normalized_profile_terms(profile.get("schema_roles"), allowed_roles)
    metrics = _normalized_profile_terms(profile.get("metric_families"), allowed_metrics)
    relationships = _normalized_relationship_roles(profile.get("entity_relationships"), allowed_roles)
    temporal = _temporal_suitability(profile)
    quality_availability = _quality_availability(quality)

    if (
        len(roles) < 2
        or not metrics
        or not temporal.valid
        or temporal.score < 0.35
        or not quality_availability.valid
        or quality_availability.score < 0.5
    ):
        return [
            _readiness_selection(
                packs[_READINESS_PACK_ID],
                roles=roles,
                metrics=metrics,
                temporal=temporal.score,
                quality=quality_availability.score,
                reason="opportunity evidence is insufficient for a reliable capability selection",
            )
        ]

    selections: list[CapabilitySelection] = []
    for pack_id in sorted(_OPPORTUNITY_PACK_IDS):
        pack = packs[pack_id]
        matched_goals = _overlap(goal_terms, pack.goal_signals)
        matched_roles = _overlap(roles, pack.schema_roles)
        matched_metrics = _overlap(metrics, pack.metric_families)
        matched_relationships = _overlap(relationships, pack.schema_roles)
        if not matched_roles or not matched_metrics:
            continue
        confidence = (
            0.22 * (len(matched_goals) / len(pack.goal_signals))
            + 0.34 * (len(matched_roles) / len(pack.schema_roles))
            + 0.29 * (len(matched_metrics) / len(pack.metric_families))
            + 0.05 * min(1.0, len(matched_relationships) / 2)
            + 0.06 * temporal.score
            + 0.04 * quality_availability.score
        )
        if confidence < _MIN_OPPORTUNITY_CONFIDENCE:
            continue
        reasons: list[str] = []
        if matched_goals:
            reasons.append("matched business-goal concepts: " + ", ".join(matched_goals))
        reasons.append("matched schema roles: " + ", ".join(matched_roles))
        reasons.append("matched metric families: " + ", ".join(matched_metrics))
        missing = _missing(pack.schema_roles, matched_roles, "schema role")
        missing.extend(_missing(pack.metric_families, matched_metrics, "metric family"))
        if temporal.score < 0.75:
            missing.append("longer time coverage")
        selections.append(
            CapabilitySelection(
                pack_id=pack.pack_id,
                confidence=round(min(1.0, confidence), 4),
                reasons=reasons,
                matched_schema_roles=matched_roles,
                missing_evidence=list(dict.fromkeys(missing))[:24],
            )
        )

    if selections:
        return sorted(selections, key=lambda item: (-item.confidence, item.pack_id))[:3]

    return [
        _readiness_selection(
            packs[_READINESS_PACK_ID],
            roles=roles,
            metrics=metrics,
            temporal=temporal.score,
            quality=quality_availability.score,
            reason="available semantic evidence does not support an opportunity capability pack",
        )
    ]


__all__ = [
    "CapabilityPack",
    "CapabilitySelection",
    "load_capability_packs",
    "select_capability_packs",
]

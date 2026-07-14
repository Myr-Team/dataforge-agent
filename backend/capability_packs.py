"""Data-driven capability-pack selection without dataset-name heuristics."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
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
_SEMANTIC_INPUT_KEYS = ("schema_roles", "semantic_roles", "roles")
_METRIC_INPUT_KEYS = ("metric_families", "metric_types", "metrics")
_RELATIONSHIP_INPUT_KEYS = ("entity_relationships", "relationships")


def _normalize_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "_", str(value or "").casefold()).strip("_")
    if text.endswith("s") and len(text) > 3:
        text = text[:-1]
    return text


def _terms(value: Any) -> set[str]:
    if isinstance(value, str):
        raw_values = []
        for token in re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+", value.casefold()):
            raw_values.append(token)
            if re.fullmatch(r"[\u3400-\u9fff]+", token):
                for size in (2, 3, 4):
                    raw_values.extend(token[index : index + size] for index in range(max(0, len(token) - size + 1)))
    elif isinstance(value, Mapping):
        raw_values = []
        for candidate in (value.get("role"), value.get("name"), value.get("type"), value.get("family")):
            if isinstance(candidate, str):
                raw_values.extend(re.findall(r"[a-zA-Z0-9_]+", candidate.casefold()))
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_values = []
        for item in value:
            raw_values.extend(_terms(item))
    else:
        raw_values = []
    normalized: set[str] = set()
    for item in raw_values:
        term = _normalize_text(item)
        if not term:
            continue
        normalized.add(term)
        normalized.update(part for part in term.split("_") if part)
    return normalized


def _profile_terms(profile: Mapping[str, Any], keys: Sequence[str]) -> set[str]:
    terms: set[str] = set()
    for key in keys:
        terms.update(_terms(profile.get(key)))
    return terms


def _finite_fraction(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _quality_availability(quality: Mapping[str, Any] | None) -> float:
    quality = quality or {}
    completeness = _finite_fraction(quality.get("completeness"))
    if completeness is None:
        missing = _finite_fraction(quality.get("missing_rate", quality.get("missing_pct")))
        completeness = 1.0 - missing if missing is not None else 0.0
    duplicate_rate = _finite_fraction(quality.get("duplicate_rate", quality.get("duplicate_pct")))
    duplicate_rate = duplicate_rate if duplicate_rate is not None else 0.0
    return max(0.0, min(1.0, completeness * (1.0 - min(duplicate_rate, 0.8))))


def _temporal_suitability(profile: Mapping[str, Any]) -> float:
    coverage = profile.get("temporal_coverage")
    if isinstance(coverage, Mapping):
        available = bool(coverage.get("available", coverage.get("has_time", False)))
        periods = coverage.get("periods", coverage.get("count", 0))
    else:
        available = bool(coverage)
        periods = 1 if coverage else 0
    try:
        count = max(0, int(periods))
    except (TypeError, ValueError):
        count = 0
    if not available:
        return 0.0
    return min(1.0, count / 4) if count else 0.35


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
    """Select evidence-oriented packs from declared semantic profile fields only."""
    profile = schema_profile if isinstance(schema_profile, Mapping) else {}
    goal_terms = _terms(goal)
    roles = _profile_terms(profile, _SEMANTIC_INPUT_KEYS)
    metrics = _profile_terms(profile, _METRIC_INPUT_KEYS)
    relationships = _profile_terms(profile, _RELATIONSHIP_INPUT_KEYS)
    temporal = _temporal_suitability(profile)
    quality_score = _quality_availability(quality)
    packs = {pack.pack_id: pack for pack in load_capability_packs()}

    if len(roles) < 2 or not metrics or temporal < 0.35 or quality_score < 0.5:
        return [
            _readiness_selection(
                packs[_READINESS_PACK_ID],
                roles=roles,
                metrics=metrics,
                temporal=temporal,
                quality=quality_score,
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
            + 0.06 * temporal
            + 0.04 * quality_score
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
        if temporal < 0.75:
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
            temporal=temporal,
            quality=quality_score,
            reason="available semantic evidence does not support an opportunity capability pack",
        )
    ]


__all__ = [
    "CapabilityPack",
    "CapabilitySelection",
    "load_capability_packs",
    "select_capability_packs",
]

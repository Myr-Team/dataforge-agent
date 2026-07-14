"""Shared, bounded evidence views for MAF participants."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from pydantic import BaseModel, Field

from .capability_packs import load_capability_packs, select_capability_packs
from .schemas import Evidence


MAX_EVIDENCE_ITEMS = 12
MAX_EVIDENCE_QUOTE_CHARS = 320
MAX_BUNDLE_GAPS = 20
MAX_CAPABILITY_PACK_IDS = 16
MAX_CAPABILITY_PACKS = 3
_CAPABILITY_SELECTION_FIELDS = (
    "pack_id",
    "confidence",
    "reasons",
    "matched_schema_roles",
    "missing_evidence",
)
_AGENT_GUIDANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "df-coordinator": ("questions",),
    "df-corpus-analyst": ("questions",),
    "df-market-researcher": ("questions", "validation_methods"),
    "df-feasibility-analyst": ("questions", "validation_methods"),
    "df-auditor": ("validation_methods",),
    "df-producer": ("artifact_sections",),
}
_SELECTION_CONTEXT_KEY = "capability_selection_context"
_SAFE_REASON_PREFIXES = {
    "matched business-goal concepts: ": "goal_signals",
    "matched schema roles: ": "schema_roles",
    "matched metric families: ": "metric_families",
}
_SAFE_READINESS_REASONS = frozenset(
    {
        "opportunity evidence is insufficient for a reliable capability selection",
        "available semantic evidence does not support an opportunity capability pack",
    }
)
_SAFE_MISSING_EXACT = frozenset({"time coverage", "data quality", "longer time coverage"})


class BundleLimits(BaseModel):
    max_items: int = Field(default=MAX_EVIDENCE_ITEMS, ge=1, le=40)
    max_quote_chars: int = Field(default=MAX_EVIDENCE_QUOTE_CHARS, ge=80, le=1000)
    max_profile_facts: int = Field(default=20, ge=1, le=80)


class EvidenceBundle(BaseModel):
    workspace_id: str
    fingerprint: str
    evidence: list[Evidence]
    profile_facts: list[str]
    gaps: list[str] = Field(max_length=MAX_BUNDLE_GAPS)
    capability_pack_ids: list[str] = Field(max_length=MAX_CAPABILITY_PACK_IDS)
    capability_packs: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_CAPABILITY_PACKS)

    def persisted_metadata(self) -> dict[str, Any]:
        """Return the run-record projection without raw evidence or profile text."""
        return {
            "fingerprint": self.fingerprint,
            "evidence_count": len(self.evidence),
            "profile_fact_count": len(self.profile_facts),
            "gap_count": len(self.gaps),
            "capability_pack_ids": list(self.capability_pack_ids),
        }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _workspace_id(corpus: Mapping[str, Any], route: Mapping[str, Any]) -> str:
    profile = _as_mapping(corpus.get("profile"))
    for candidate in (
        route.get("workspace_id"),
        _as_mapping(route.get("payload")).get("workspace_id"),
        profile.get("workspace_id"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return "unknown"


def _evidence_ref(hit: Mapping[str, Any]) -> str:
    direct = str(hit.get("id") or "").strip()
    if direct:
        return direct
    source = str(hit.get("source_file") or "").strip()
    chunk = str(hit.get("chunk_id") or hit.get("row") or "").strip()
    return f"{source}#{chunk}".strip("#")


def _bounded_evidence(value: Any, limits: BundleLimits) -> Evidence | None:
    try:
        evidence = Evidence.model_validate(value)
    except (TypeError, ValueError):
        return None
    return evidence.model_copy(
        update={"quote": str(evidence.quote or "")[: limits.max_quote_chars]}
    )


def _profile_facts(profile: Mapping[str, Any], limits: BundleLimits) -> list[str]:
    facts: list[str] = []
    for key in sorted(profile):
        value = profile[key]
        if key in {"asset_evidence", "gaps_observed"} or value is None:
            continue
        if isinstance(value, (str, int, float, bool)) and not isinstance(value, bytes):
            fact = f"{key}: {value}".strip()
            if fact:
                facts.append(fact[: limits.max_quote_chars])
        if len(facts) >= limits.max_profile_facts:
            break
    return facts


def _registered_capability_packs() -> dict[str, Any]:
    return {pack.pack_id: pack for pack in load_capability_packs()}


def _requested_capability_pack_ids(packs: Sequence[Any] | None) -> list[str]:
    """Read untrusted selection inputs as exact registry IDs only."""
    definitions = _registered_capability_packs()
    ids: list[str] = []
    for value in packs or ():
        candidate = value if isinstance(value, str) else _as_mapping(value).get("pack_id")
        pack_id = candidate if isinstance(candidate, str) else ""
        if pack_id not in definitions or pack_id in ids:
            continue
        ids.append(pack_id)
        if len(ids) >= MAX_CAPABILITY_PACKS:
            break
    return ids


def _computed_capability_selections(route: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Recompute selections from bounded semantic inputs, never caller metadata."""
    context = _as_mapping(route.get(_SELECTION_CONTEXT_KEY))
    goal = context.get("goal")
    profile = context.get("schema_profile")
    quality = context.get("quality")
    if not isinstance(goal, str) or not isinstance(profile, Mapping) or not isinstance(quality, Mapping):
        return {}
    try:
        selections = select_capability_packs(goal[:4096], profile, quality)
    except (TypeError, ValueError):
        return {}
    return {
        selection.pack_id: {
            key: selection.model_dump(mode="json")[key]
            for key in _CAPABILITY_SELECTION_FIELDS
        }
        for selection in selections
    }


def _capability_pack_contract(
    packs: Sequence[Any] | None,
    route: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Keep only caller IDs that an internal selector independently chose."""
    computed = _computed_capability_selections(route)
    return [computed[pack_id] for pack_id in _requested_capability_pack_ids(packs) if pack_id in computed]


def _canonical_terms(value: Any, allowed: Sequence[str]) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    canonical = {str(item).casefold(): str(item) for item in allowed}
    terms: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.casefold() not in canonical:
            return None
        term = canonical[item.casefold()]
        if term not in terms:
            terms.append(term)
    return sorted(terms)


def _canonical_reasons(pack: Any, value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    reasons: list[str] = []
    for reason in value:
        if not isinstance(reason, str):
            return None
        if reason in _SAFE_READINESS_REASONS:
            canonical = reason
        else:
            canonical = ""
            for prefix, attr in _SAFE_REASON_PREFIXES.items():
                if not reason.startswith(prefix):
                    continue
                terms = _canonical_terms(
                    [part.strip() for part in reason[len(prefix) :].split(",") if part.strip()],
                    getattr(pack, attr),
                )
                if terms is None:
                    return None
                canonical = prefix + ", ".join(terms)
                break
            if not canonical:
                return None
        if canonical not in reasons:
            reasons.append(canonical)
    return reasons


def _canonical_missing_evidence(pack: Any, value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    missing: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        if item in _SAFE_MISSING_EXACT:
            canonical = item
        elif item.startswith("schema role: "):
            role = item.removeprefix("schema role: ")
            if role not in pack.schema_roles:
                return None
            canonical = f"schema role: {role}"
        elif item.startswith("metric family: "):
            metric = item.removeprefix("metric family: ")
            if metric not in pack.metric_families:
                return None
            canonical = f"metric family: {metric}"
        else:
            return None
        if canonical not in missing:
            missing.append(canonical)
    return missing


def sanitize_capability_pack_records(packs: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Defensively project stored records to registry-derived, non-free-text values."""
    definitions = _registered_capability_packs()
    records: list[dict[str, Any]] = []
    for pack_id in _requested_capability_pack_ids(packs):
        raw = next(
            (
                _as_mapping(value)
                for value in packs or ()
                if isinstance(_as_mapping(value).get("pack_id"), str)
                and _as_mapping(value).get("pack_id") == pack_id
            ),
            {},
        )
        pack = definitions[pack_id]
        reasons = _canonical_reasons(pack, raw.get("reasons"))
        roles = _canonical_terms(raw.get("matched_schema_roles"), pack.schema_roles)
        missing = _canonical_missing_evidence(pack, raw.get("missing_evidence"))
        confidence = raw.get("confidence")
        valid_confidence = (
            isinstance(confidence, Real)
            and not isinstance(confidence, bool)
            and math.isfinite(float(confidence))
            and 0 <= float(confidence) <= 1
        )
        if reasons is None or roles is None or missing is None or not valid_confidence:
            records.append(
                {
                    "pack_id": pack_id,
                    "confidence": 0.0,
                    "reasons": [f"registered capability pack: {pack.label}"],
                    "matched_schema_roles": [],
                    "missing_evidence": list(pack.evidence_requirements)[:24],
                }
            )
        else:
            records.append(
                {
                    "pack_id": pack_id,
                    "confidence": round(float(confidence), 4),
                    "reasons": reasons[:12],
                    "matched_schema_roles": roles[:24],
                    "missing_evidence": missing[:24],
                }
            )
    return records


def _capability_pack_ids(packs: Sequence[Any] | None) -> list[str]:
    """Preserve the bounded legacy ID projection from registered IDs only."""
    return _requested_capability_pack_ids(packs)


def _capability_guidance(
    contracts: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    definitions = {pack.pack_id: pack for pack in load_capability_packs()}
    guidance: list[dict[str, Any]] = []
    for selection in contracts:
        pack_id = str(selection.get("pack_id") or "")
        definition = definitions.get(pack_id)
        if definition is None:
            continue
        item = {"pack_id": pack_id}
        for field in fields:
            value = getattr(definition, field, ())
            if isinstance(value, list):
                item[field] = [str(entry)[:240] for entry in value[:24] if str(entry).strip()]
        guidance.append(item)
    return guidance


def build_evidence_bundle(
    corpus: Any,
    route: Any,
    packs: Sequence[Any] | None,
    limits: BundleLimits | Mapping[str, Any] | None = None,
) -> EvidenceBundle:
    """Build one deterministic view from authoritative corpus inputs only."""
    bounded_limits = (
        limits
        if isinstance(limits, BundleLimits)
        else BundleLimits.model_validate(limits or {})
    )
    corpus_data = _as_mapping(corpus)
    route_data = _as_mapping(route)
    profile = _as_mapping(corpus_data.get("profile"))
    candidates: list[Evidence] = []
    seen_refs: set[str] = set()

    def collect(candidate: Any) -> None:
        item = _bounded_evidence(candidate, bounded_limits)
        if item is None:
            return
        candidates.append(item)

    for item in profile.get("asset_evidence") or ():
        collect(item)
    for hit in corpus_data.get("hits") or ():
        hit_data = _as_mapping(hit)
        ref = _evidence_ref(hit_data)
        if not ref:
            continue
        collect(
            {
                "source_type": "corpus",
                "ref": ref,
                "quote": str(hit_data.get("content") or ""),
            }
        )

    evidence: list[Evidence] = []
    for item in sorted(candidates, key=lambda evidence: (evidence.ref, evidence.source_type, evidence.quote or "")):
        if item.ref in seen_refs or len(evidence) >= bounded_limits.max_items:
            continue
        seen_refs.add(item.ref)
        evidence.append(item)
    gaps = sorted(
        {
            str(item).strip()[:240]
            for item in profile.get("gaps_observed") or ()
            if str(item).strip()
        }
    )[:MAX_BUNDLE_GAPS]
    capability_packs = _capability_pack_contract(packs, route_data)
    payload = {
        "workspace_id": _workspace_id(corpus_data, route_data),
        "evidence": [item.model_dump(mode="json", exclude_none=True) for item in evidence],
        "profile_facts": _profile_facts(profile, bounded_limits),
        "gaps": gaps,
        "capability_pack_ids": _capability_pack_ids(packs),
        "capability_packs": capability_packs,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EvidenceBundle(fingerprint=fingerprint, **payload)


def bundle_for_agent(bundle: EvidenceBundle, agent_id: str) -> dict[str, Any]:
    """Return the minimum bounded evidence view required by an agent role."""
    policies: dict[str, tuple[frozenset[str], bool, bool]] = {
        "df-coordinator": (frozenset({"corpus", "computed"}), False, False),
        "df-corpus-analyst": (frozenset({"corpus", "computed"}), True, True),
        "df-market-researcher": (frozenset({"corpus", "computed"}), False, True),
        "df-feasibility-analyst": (frozenset({"corpus", "market", "computed"}), True, False),
        "df-auditor": (frozenset({"corpus", "market", "computed"}), True, False),
        "df-producer": (frozenset({"computed"}), False, False),
    }
    policy = policies.get(str(agent_id or "").strip())
    if policy is None:
        raise ValueError("unsupported agent_id for an evidence bundle view")
    evidence_types, include_profile, include_pack_ids = policy
    return {
        "workspace_id": bundle.workspace_id,
        "fingerprint": bundle.fingerprint,
        "evidence": [
            item.model_dump(mode="json", exclude_none=True)
            for item in bundle.evidence
            if item.source_type in evidence_types
        ],
        "profile_facts": list(bundle.profile_facts) if include_profile else [],
        "gaps": list(bundle.gaps),
        "capability_pack_ids": list(bundle.capability_pack_ids) if include_pack_ids else [],
        "capability_guidance": _capability_guidance(
            bundle.capability_packs,
            _AGENT_GUIDANCE_FIELDS[str(agent_id)],
        ),
        "capability_guidance_is_observed_evidence": False,
    }


__all__ = [
    "BundleLimits",
    "EvidenceBundle",
    "MAX_BUNDLE_GAPS",
    "MAX_CAPABILITY_PACK_IDS",
    "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_QUOTE_CHARS",
    "build_evidence_bundle",
    "bundle_for_agent",
    "sanitize_capability_pack_records",
]

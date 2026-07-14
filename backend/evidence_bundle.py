"""Shared, bounded evidence views for MAF participants."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from .capability_packs import load_capability_packs
from .schemas import CapabilitySelection, Evidence


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


def _capability_pack_contract(packs: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Resolve only registered packs and keep selection metadata separate from evidence."""
    definitions = {pack.pack_id: pack for pack in load_capability_packs()}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in packs or ():
        try:
            selection = CapabilitySelection.model_validate(_as_mapping(value))
        except (TypeError, ValueError):
            continue
        if selection.pack_id in seen or selection.pack_id not in definitions:
            continue
        seen.add(selection.pack_id)
        selected.append(
            {
                key: selection.model_dump(mode="json")[key]
                for key in _CAPABILITY_SELECTION_FIELDS
            }
        )
        if len(selected) >= MAX_CAPABILITY_PACKS:
            break
    return selected


def _capability_pack_ids(packs: Sequence[Any] | None) -> list[str]:
    """Preserve the bounded ID-only contract used by existing evidence bundles."""
    ids: list[str] = []
    for pack in packs or ():
        if isinstance(pack, str):
            candidate = pack
        else:
            value = _as_mapping(pack)
            candidate = value.get("id") or value.get("pack_id") or value.get("name")
        text = str(candidate or "").strip()
        if text and text not in ids:
            ids.append(text[:120])
    return sorted(ids)[:MAX_CAPABILITY_PACK_IDS]


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
    capability_packs = _capability_pack_contract(packs)
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
]

"""Shared, bounded evidence views for MAF participants."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from pydantic import BaseModel, Field

from .audit_store import _active_key, _key_for
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
_SELECTION_SCOPE_KEY = "capability_selection_scope"
_CAPABILITY_PROVENANCE_SOURCE = "normalized_goal_schema_profile_quality"
_CAPABILITY_PROVENANCE_VERSION = "2"
_MAX_SCOPE_ID_CHARS = 256
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
    capability_pack_provenance: dict[str, str] = Field(default_factory=dict)

    def persisted_metadata(self) -> dict[str, Any]:
        """Return the run-record projection without raw evidence or profile text."""
        return {
            "fingerprint": self.fingerprint,
            "evidence_count": len(self.evidence),
            "profile_fact_count": len(self.profile_facts),
            "gap_count": len(self.gaps),
            "capability_pack_ids": list(self.capability_pack_ids),
            "capability_pack_provenance": dict(self.capability_pack_provenance),
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


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _selector_input_projection(route: Mapping[str, Any]) -> dict[str, Any]:
    """Retain only the semantic selector inputs in a non-reversible signed digest."""
    context = _as_mapping(route.get(_SELECTION_CONTEXT_KEY))
    profile = _as_mapping(context.get("schema_profile"))
    quality = _as_mapping(context.get("quality"))
    return {
        "goal": str(context.get("goal") or "")[:4096],
        "schema_profile": {
            key: profile.get(key)
            for key in ("schema_roles", "metric_families", "entity_relationships", "temporal_coverage")
        },
        "quality": {
            key: quality.get(key)
            for key in ("completeness", "missing_rate", "missing_pct", "duplicate_rate", "duplicate_pct")
        },
    }


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _selection_scope(value: Any) -> dict[str, str]:
    """Return the bounded server-owned workspace and durable conversation scope."""
    source = _as_mapping(value)
    workspace_id = str(source.get("workspace_id") or "").strip()
    scope_id = str(source.get("scope_id") or "").strip()
    if (
        not workspace_id
        or not scope_id
        or len(workspace_id) > _MAX_SCOPE_ID_CHARS
        or len(scope_id) > _MAX_SCOPE_ID_CHARS
        or "\x00" in workspace_id
        or "\x00" in scope_id
    ):
        return {}
    return {"workspace_id": workspace_id, "scope_id": scope_id}


def _scope_fingerprint(scope: Mapping[str, str]) -> str:
    return hashlib.sha256(_canonical_json(scope)).hexdigest()


def _capability_pack_provenance(
    route: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Sign an internally selected capability contract without persisting selector inputs."""
    scope = _selection_scope(route.get(_SELECTION_SCOPE_KEY))
    if not records or not _as_mapping(route.get(_SELECTION_CONTEXT_KEY)) or not scope:
        return {}
    canonical_records = sanitize_capability_pack_records(records)
    if not canonical_records:
        return {}
    pack_ids = [str(record["pack_id"]) for record in canonical_records]
    selection_fingerprint = hashlib.sha256(
        _canonical_json({"inputs": _selector_input_projection(route), "pack_ids": pack_ids})
    ).hexdigest()
    records_fingerprint = hashlib.sha256(_canonical_json(canonical_records)).hexdigest()
    try:
        key_id, key = _active_key()
    except Exception:
        return {}
    nonce = secrets.token_hex(32)
    payload = {
        "source": _CAPABILITY_PROVENANCE_SOURCE,
        "version": _CAPABILITY_PROVENANCE_VERSION,
        "selection_fingerprint": selection_fingerprint,
        "records_fingerprint": records_fingerprint,
        "pack_ids": pack_ids,
        "workspace_id": scope["workspace_id"],
        "scope_id": scope["scope_id"],
        "nonce": nonce,
    }
    return {
        "source": _CAPABILITY_PROVENANCE_SOURCE,
        "version": _CAPABILITY_PROVENANCE_VERSION,
        "selection_fingerprint": selection_fingerprint,
        "records_fingerprint": records_fingerprint,
        "workspace_fingerprint": hashlib.sha256(scope["workspace_id"].encode("utf-8")).hexdigest(),
        "scope_fingerprint": _scope_fingerprint(scope),
        "key_id": str(key_id),
        "nonce": nonce,
        "signature": hmac.new(key, _canonical_json(payload), hashlib.sha256).hexdigest(),
    }


def internally_selected_capability_pack_contract(
    selection_context: Mapping[str, Any],
    selection_scope: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return only selector-produced records plus their server-verifiable provenance."""
    route = {
        _SELECTION_CONTEXT_KEY: _as_mapping(selection_context),
        _SELECTION_SCOPE_KEY: _selection_scope(selection_scope),
    }
    records = list(_computed_capability_selections(route).values())
    provenance = _capability_pack_provenance(route, records)
    return (records, provenance) if records and provenance else ([], {})


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


def sanitize_capability_pack_contract(
    packs: Sequence[Any] | None,
    provenance: Any,
    expected_scope: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Accept records only when an internal selector signature binds their exact projection."""
    records = sanitize_capability_pack_records(packs)
    source = _as_mapping(provenance)
    scope = _selection_scope(expected_scope)
    key_id = source.get("key_id")
    signature = source.get("signature")
    selection_fingerprint = source.get("selection_fingerprint")
    records_fingerprint = source.get("records_fingerprint")
    workspace_fingerprint = source.get("workspace_fingerprint")
    scope_fingerprint = source.get("scope_fingerprint")
    nonce = source.get("nonce")
    if (
        not records
        or not scope
        or source.get("source") != _CAPABILITY_PROVENANCE_SOURCE
        or source.get("version") != _CAPABILITY_PROVENANCE_VERSION
        or not isinstance(key_id, str)
        or len(key_id) > 64
        or not _valid_digest(signature)
        or not _valid_digest(selection_fingerprint)
        or not _valid_digest(records_fingerprint)
        or not _valid_digest(workspace_fingerprint)
        or not _valid_digest(scope_fingerprint)
        or not _valid_digest(nonce)
    ):
        return [], {}
    expected_workspace_fingerprint = hashlib.sha256(scope["workspace_id"].encode("utf-8")).hexdigest()
    if not hmac.compare_digest(workspace_fingerprint, expected_workspace_fingerprint):
        return [], {}
    if not hmac.compare_digest(scope_fingerprint, _scope_fingerprint(scope)):
        return [], {}
    pack_ids = [str(record["pack_id"]) for record in records]
    if not hmac.compare_digest(records_fingerprint, hashlib.sha256(_canonical_json(records)).hexdigest()):
        return [], {}
    payload = {
        "source": _CAPABILITY_PROVENANCE_SOURCE,
        "version": _CAPABILITY_PROVENANCE_VERSION,
        "selection_fingerprint": selection_fingerprint,
        "records_fingerprint": records_fingerprint,
        "pack_ids": pack_ids,
        "workspace_id": scope["workspace_id"],
        "scope_id": scope["scope_id"],
        "nonce": nonce,
    }
    try:
        expected_signature = hmac.new(_key_for(key_id), _canonical_json(payload), hashlib.sha256).hexdigest()
    except Exception:
        return [], {}
    if not hmac.compare_digest(signature, expected_signature):
        return [], {}
    return records, {
        "source": _CAPABILITY_PROVENANCE_SOURCE,
        "version": _CAPABILITY_PROVENANCE_VERSION,
        "selection_fingerprint": selection_fingerprint,
        "records_fingerprint": records_fingerprint,
        "workspace_fingerprint": workspace_fingerprint,
        "scope_fingerprint": scope_fingerprint,
        "key_id": key_id,
        "nonce": nonce,
        "signature": signature,
    }


def capability_pack_ids_from_records(packs: Sequence[Any] | None) -> list[str]:
    """Derive legacy IDs only from a sanitized capability-pack projection."""
    return [
        str(item["pack_id"])
        for item in sanitize_capability_pack_records(packs)
        if isinstance(item.get("pack_id"), str)
    ]


def sanitize_capability_metadata(value: Any, expected_scope: Mapping[str, Any] | None = None) -> Any:
    """Recursively rebuild capability metadata before it reaches a run-facing contract."""
    if isinstance(value, Mapping):
        sanitized = {
            str(key): sanitize_capability_metadata(item, expected_scope)
            for key, item in value.items()
            if key != "capability_pack_provenance"
        }
        if "capability_packs" in value or "capability_pack_ids" in value:
            raw_packs = value.get("capability_packs")
            records, provenance = sanitize_capability_pack_contract(
                raw_packs if isinstance(raw_packs, list) else [],
                value.get("capability_pack_provenance"),
                expected_scope,
            )
            sanitized["capability_packs"] = records
            sanitized["capability_pack_ids"] = [str(record["pack_id"]) for record in records]
            if provenance:
                sanitized["capability_pack_provenance"] = provenance
        return sanitized
    if isinstance(value, list):
        return [sanitize_capability_metadata(item, expected_scope) for item in value]
    return value


_PUBLIC_PROVENANCE_FIELDS = frozenset(
    {
        "capability_pack_provenance",
        "signature",
        "nonce",
        "scope_fingerprint",
        "workspace_fingerprint",
        "selection_fingerprint",
        "records_fingerprint",
        "key_id",
    }
)


def public_artifact_projection(value: Any, expected_scope: Mapping[str, Any] | None = None) -> Any:
    """Project artifacts for API and SSE clients without reusable provenance material."""
    if isinstance(value, Mapping):
        raw_packs = value.get("capability_packs")
        has_capability_metadata = "capability_packs" in value or "capability_pack_ids" in value
        records, provenance = sanitize_capability_pack_contract(
            raw_packs if isinstance(raw_packs, list) else [],
            value.get("capability_pack_provenance"),
            expected_scope,
        )
        projected = {
            str(key): public_artifact_projection(item, expected_scope)
            for key, item in value.items()
            if str(key) not in _PUBLIC_PROVENANCE_FIELDS
        }
        if has_capability_metadata:
            projected["capability_packs"] = records
            projected["capability_pack_ids"] = [str(record["pack_id"]) for record in records]
            projected["capability_pack_integrity"] = (
                {
                    "status": "verified",
                    "source": _CAPABILITY_PROVENANCE_SOURCE,
                    "version": _CAPABILITY_PROVENANCE_VERSION,
                }
                if provenance
                else {"status": "unavailable"}
            )
        return projected
    if isinstance(value, list):
        return [public_artifact_projection(item, expected_scope) for item in value]
    return value


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
    capability_pack_provenance = _capability_pack_provenance(route_data, capability_packs)
    if capability_packs and not capability_pack_provenance:
        capability_packs = []
    payload = {
        "workspace_id": _workspace_id(corpus_data, route_data),
        "evidence": [item.model_dump(mode="json", exclude_none=True) for item in evidence],
        "profile_facts": _profile_facts(profile, bounded_limits),
        "gaps": gaps,
        "capability_pack_ids": [str(item["pack_id"]) for item in capability_packs],
        "capability_packs": capability_packs,
        "capability_pack_provenance": capability_pack_provenance if capability_packs else {},
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
    "capability_pack_ids_from_records",
    "internally_selected_capability_pack_contract",
    "public_artifact_projection",
    "sanitize_capability_metadata",
    "sanitize_capability_pack_contract",
    "sanitize_capability_pack_records",
]

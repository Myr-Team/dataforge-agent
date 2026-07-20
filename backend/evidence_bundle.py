"""Shared, bounded evidence views for MAF participants."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
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
        metadata = {
            "fingerprint": self.fingerprint,
            "evidence_count": len(self.evidence),
            "profile_fact_count": len(self.profile_facts),
            "gap_count": len(self.gaps),
            "capability_pack_ids": list(self.capability_pack_ids),
        }
        if self.capability_pack_provenance:
            metadata["capability_pack_provenance"] = dict(self.capability_pack_provenance)
        return metadata


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
        "capability_pack_integrity",
        "capability_packs",
        "capability_pack_ids",
    }
)

_CONVERSATION_ROUTE_MODES = frozenset(
    {"direct", "followup", "grounded_followup", "plan_draft", "reanalyze", "clarify"}
)
_CONVERSATION_ROUTE_REASONS = {
    "direct": "该问题不需要读取当前工作区证据。",
    "followup": "Follow-up",
    "grounded_followup": "该回复需要结合当前工作区证据。",
    "plan_draft": "该请求会把已有分析整理为可执行方案。",
    "reanalyze": "当前工作区证据或分析请求需要重新评估。",
    "clarify": "需要补充关键信息后才能给出基于证据的回答。",
}
_SAFE_ROUTING_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SAFE_PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")
_PUBLIC_ARTIFACT_KINDS = frozenset(
    {
        "pdf",
        "concept_image",
        "audio_summary",
        "pilot_plan",
        "action_plan",
        "roadmap",
        "validation_plan",
        "risk_register",
        "poster",
    }
)
_MAX_PUBLIC_TEXT_CHARS = 32_768
_MAX_PUBLIC_LIST_ITEMS = 32


def _public_text(value: Any, limit: int = _MAX_PUBLIC_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def _public_identifier(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_PUBLIC_IDENTIFIER.fullmatch(text) else None


def _public_number(value: Any) -> int | float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        return None
    return value


def _public_text_list(value: Any, *, limit: int = 320, items: int = _MAX_PUBLIC_LIST_ITEMS) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        text
        for raw in value[:items]
        if (text := _public_text(raw, limit)) is not None
    ]


def _public_identifier_list(value: Any, *, items: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        identifier
        for raw in value[:items]
        if (identifier := _public_identifier(raw)) is not None
    ]


def _public_url(value: Any) -> str | None:
    text = _public_text(value, 2048)
    if text is None:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in ("sig=", "token=", "secret=", "password=", "credential=")):
        return None
    if text.startswith("/") or text.startswith("https://") or text.startswith("http://"):
        return text
    return None


def conversation_route_projection(value: Any) -> dict[str, Any]:
    """Expose route state without retaining model rationale or user-derived gaps."""
    if isinstance(value, Mapping):
        mode = str(value.get("mode") or "").strip()
        evidence_required = value.get("evidence_required") is True
    else:
        mode = str(value or "").strip()
        evidence_required = False
    if mode not in _CONVERSATION_ROUTE_MODES:
        return {}
    return {
        "mode": mode,
        "reason": _CONVERSATION_ROUTE_REASONS[mode],
        "evidence_required": evidence_required,
    }


def _routing_projection(value: Any, route: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    intent = str(value.get("intent") or "").strip()
    if _SAFE_ROUTING_IDENTIFIER.fullmatch(intent):
        projected["intent"] = intent
    experts = [
        item
        for raw in (value.get("experts") or [])[:12]
        if _SAFE_ROUTING_IDENTIFIER.fullmatch(item := str(raw).strip())
    ]
    if experts or isinstance(value.get("experts"), list):
        projected["experts"] = experts
    output_mode = str(value.get("output_mode") or "").strip()
    if output_mode in {"chat", "report", "full_package"}:
        projected["output_mode"] = output_mode
    if isinstance(value.get("needs_clarification"), bool):
        projected["needs_clarification"] = value["needs_clarification"]
    safe_route = conversation_route_projection(value.get("conversation_route")) or dict(route or {})
    if safe_route:
        projected["conversation_route"] = safe_route
    return projected


def _llm_metadata_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("mode", "response_id"):
        item = str(value.get(key) or "").strip()
        if _SAFE_ROUTING_IDENTIFIER.fullmatch(item):
            projected[key] = item
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        safe_usage = {
            key: max(0, int(item))
            for key, item in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
        }
        if safe_usage:
            projected["usage"] = safe_usage
    return projected


def _project_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("id", "marker", "source_type", "ref", "source_file", "sheet", "chunk_id", "confidence"):
        if identifier := _public_identifier(value.get(key)):
            projected[key] = identifier
    for key in ("title", "quote", "snippet", "excerpt", "content", "claim", "summary", "description", "positioning"):
        if text := _public_text(value.get(key), 640):
            projected[key] = text
    for key in ("row", "score", "rank"):
        if (number := _public_number(value.get(key))) is not None:
            projected[key] = number
    for key in ("url", "source_url"):
        if url := _public_url(value.get(key)):
            projected[key] = url
    return projected


def _project_evidence_list(value: Any, *, items: int = 24) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for raw in value[:items] if (item := _project_evidence(raw))]


def _project_answer(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("text", "markdown"):
        if text := _public_text(value.get(key)):
            projected[key] = text
    if isinstance(value.get("citations"), list):
        projected["citations"] = _project_evidence_list(value.get("citations"))
    for key in ("confidence", "confidence_label"):
        if text := _public_text(value.get(key), 80):
            projected[key] = text
    if "_llm" in value:
        projected["_llm"] = _llm_metadata_projection(value.get("_llm"))
    return projected


def _project_clarify(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    if question := _public_text(value.get("question"), 800):
        projected["question"] = question
    options: list[dict[str, str]] = []
    for index, raw in enumerate((value.get("options") or [])[:5] if isinstance(value.get("options"), list) else []):
        if isinstance(raw, Mapping):
            label = _public_text(raw.get("label"), 160)
            option_id = _public_identifier(raw.get("id")) or f"option-{index + 1}"
        else:
            label = _public_text(raw, 160)
            option_id = f"option-{index + 1}"
        if label:
            options.append({"id": option_id, "label": label})
    if options:
        projected["options"] = options
    for key in ("allow_multi", "allow_freeform"):
        if isinstance(value.get(key), bool):
            projected[key] = value[key]
    return projected


def _project_output_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("version", "intent", "citation_style", "answer_style"):
        if text := _public_text(value.get(key), 120):
            projected[key] = text
    if isinstance(value.get("sections"), list):
        projected["sections"] = _public_text_list(value.get("sections"), limit=120, items=16)
    for key in ("customer_text", "raw_field_names_allowed", "no_numbered_action_plan", "no_dimension_scores"):
        if isinstance(value.get(key), bool):
            projected[key] = value[key]
    if (number := _public_number(value.get("max_target_chars"))) is not None:
        projected["max_target_chars"] = int(number)
    return projected


def _project_feasibility(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("opportunity_id", "verdict", "overall_confidence", "rubric_version", "guardrail_version", "plan_version"):
        if text := _public_text(value.get(key), 160):
            projected[key] = text
    dimensions: list[dict[str, Any]] = []
    for raw in (value.get("dimensions") or [])[:10] if isinstance(value.get("dimensions"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in ("name", "confidence"):
            if text := _public_text(raw.get(key), 80):
                item[key] = text
        if (score := _public_number(raw.get("score"))) is not None:
            item["score"] = score
        if isinstance(raw.get("evidence"), list):
            item["evidence"] = _project_evidence_list(raw.get("evidence"), items=12)
        if item:
            dimensions.append(item)
    if dimensions or isinstance(value.get("dimensions"), list):
        projected["dimensions"] = dimensions
    for key in ("guardrails", "evidence_warnings"):
        if isinstance(value.get(key), list):
            projected[key] = _public_text_list(value.get(key), limit=360)
    if (score := _public_number(value.get("rubric_weighted_score"))) is not None:
        projected["rubric_weighted_score"] = score
    if "_llm" in value:
        projected["_llm"] = _llm_metadata_projection(value.get("_llm"))
    return projected


def _project_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("verdict", "status", "target_expert", "agent", "orchestrator"):
        if text := _public_identifier(value.get(key)):
            projected[key] = text
    for key in ("issues", "reason_codes"):
        if isinstance(value.get(key), (list, tuple)):
            projected[key] = _public_text_list(value.get(key), limit=360)
    return projected


def _project_verdict_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "judgment",
        "confidence",
        "rubric_version",
        "revised_by",
        "kind",
        "verdict_before",
        "verdict_after",
        "verdict_before_label",
        "verdict_after_label",
        "downgrade_reason",
        "source",
        "dimension",
        "dim",
        "reason",
    ):
        if text := _public_text(value.get(key), 360):
            projected[key] = text
    for key in ("weighted_score", "score_before", "score_after", "blind", "revised", "delta"):
        raw = value.get(key)
        if isinstance(raw, Mapping):
            projected[key] = _project_verdict_summary(raw)
        elif (number := _public_number(raw)) is not None:
            projected[key] = number
    if isinstance(value.get("disagreement"), list):
        projected["disagreement"] = [
            item
            for raw in value["disagreement"][:12]
            if (item := _project_verdict_summary(raw))
        ]
    if isinstance(value.get("downgrade"), Mapping):
        projected["downgrade"] = _project_verdict_summary(value.get("downgrade"))
    return projected


def _project_corpus(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    if isinstance(value.get("hits"), list):
        projected["hits"] = _project_evidence_list(value.get("hits"), items=24)
    opportunities: list[dict[str, Any]] = []
    for raw in (value.get("opportunities") or [])[:12] if isinstance(value.get("opportunities"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        item = _project_evidence(raw)
        evidence = _project_evidence_list(raw.get("supporting_evidence"), items=12)
        if evidence:
            item["supporting_evidence"] = evidence
        if item:
            opportunities.append(item)
    if opportunities or isinstance(value.get("opportunities"), list):
        projected["opportunities"] = opportunities
    profile = value.get("profile")
    if isinstance(profile, Mapping):
        public_profile: dict[str, Any] = {}
        for key in ("workspace_id", "summary", "customer_summary", "profile_summary"):
            if text := _public_text(profile.get(key), 1200):
                public_profile[key] = text
        for key in ("assets", "gaps_observed"):
            if isinstance(profile.get(key), list):
                public_profile[key] = _public_text_list(profile.get(key), limit=320)
        if isinstance(profile.get("asset_evidence"), list):
            public_profile["asset_evidence"] = _project_evidence_list(profile.get("asset_evidence"), items=24)
        projected["profile"] = public_profile
    return projected


def _project_market(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("opportunity_id", "positioning_note", "market_evidence_status", "confidence"):
        if text := _public_text(value.get(key), 1200):
            projected[key] = text
    for key in ("competitors", "external_findings", "sources"):
        if isinstance(value.get(key), list):
            projected[key] = _project_evidence_list(value.get(key), items=24)
    if isinstance(value.get("gaps"), list):
        projected["gaps"] = _public_text_list(value.get("gaps"), limit=360)
    if "_llm" in value:
        projected["_llm"] = _llm_metadata_projection(value.get("_llm"))
    return projected


def _project_proposal_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("mode", "status", "content_type"):
        if identifier := _public_identifier(value.get(key)):
            projected[key] = identifier
    if (size := _public_number(value.get("bytes"))) is not None:
        projected["bytes"] = max(0, int(size))
    for key in ("artifact_url", "url", "blob_url"):
        if url := _public_url(value.get(key)):
            projected[key] = url
    return projected


def _project_proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for kind in _PUBLIC_ARTIFACT_KINDS:
        if isinstance(value.get(kind), Mapping):
            projected[kind] = _project_proposal_item(value.get(kind))
    urls = value.get("artifact_urls")
    if isinstance(urls, Mapping):
        projected["artifact_urls"] = {
            kind: url
            for kind in _PUBLIC_ARTIFACT_KINDS
            if (url := _public_url(urls.get(kind))) is not None
        }
    return projected


def _project_simple_status(
    value: Any,
    _conversation_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "mode",
        "status",
        "runtime",
        "orchestrator",
        "execution_path",
        "fast_path",
        "producer_suppressed",
        "version",
        "answer_style",
        "source",
        "kind",
        "label",
        "route_hint",
        "answer_type",
        "experiment_version_id",
        "source_run_id",
    ):
        if text := _public_text(value.get(key), 240):
            projected[key] = text
    for key in ("ready", "lightweight", "needs_full_analysis", "should_clarify", "degraded", "fallback"):
        if isinstance(value.get(key), bool):
            projected[key] = value[key]
    for key in ("count", "revision", "max_revisions", "evidence_revision"):
        if (number := _public_number(value.get(key))) is not None:
            projected[key] = max(0, int(number))
    return projected


def _project_actor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("name", "email", "actor_id", "tenant_id", "source"):
        if text := _public_text(value.get(key), 320):
            projected[key] = text
    for key in ("roles", "groups"):
        if isinstance(value.get(key), (list, tuple)):
            projected[key] = _public_identifier_list(value.get(key), items=20)
    return projected


def sanitize_conversation_metadata(
    value: Any,
    expected_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a typed public artifact envelope; unknown keys are never retained."""
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("workspace_id", "run_id", "origin", "conversation_id", "source_analysis_run_id", "experiment_version_id"):
        if key in value and (identifier := _public_identifier(value.get(key))):
            projected[key] = identifier
    for key in ("text", "summary", "recommendation", "confidence_label"):
        if key in value and (text := _public_text(value.get(key))):
            projected[key] = text
    for key in ("mode", "confidence", "verdict_source"):
        if key in value and (text := _public_text(value.get(key), 120)):
            projected[key] = text
    if (revision := _public_number(value.get("evidence_revision"))) is not None:
        projected["evidence_revision"] = max(0, int(revision))
    if isinstance(value.get("routing"), Mapping):
        projected["routing"] = _routing_projection(value.get("routing"))
    if route := conversation_route_projection(value.get("conversation_route")):
        projected["conversation_route"] = route
    if isinstance(value.get("routing_meta"), Mapping):
        projected["routing_meta"] = _project_simple_status(value.get("routing_meta"))
    if isinstance(value.get("answer"), Mapping):
        projected["answer"] = _project_answer(value.get("answer"))
    if isinstance(value.get("artifact"), Mapping):
        projected["artifact"] = public_artifact_projection(value.get("artifact"), expected_scope)
    if isinstance(value.get("feasibility"), Mapping):
        projected["feasibility"] = _project_feasibility(value.get("feasibility"))
    if isinstance(value.get("audit"), Mapping):
        projected["audit"] = _project_audit(value.get("audit"))
    if isinstance(value.get("verdict"), Mapping):
        projected["verdict"] = _project_verdict_summary(value.get("verdict"))
    if isinstance(value.get("verdict_downgrade"), Mapping):
        projected["verdict_downgrade"] = _project_verdict_summary(value.get("verdict_downgrade"))
    if isinstance(value.get("corpus"), Mapping):
        projected["corpus"] = _project_corpus(value.get("corpus"))
    if isinstance(value.get("market"), Mapping):
        projected["market"] = _project_market(value.get("market"))
    if isinstance(value.get("proposal"), Mapping):
        projected["proposal"] = _project_proposal(value.get("proposal"))
    if isinstance(value.get("artifact_urls"), Mapping):
        projected["artifact_urls"] = _project_proposal({"artifact_urls": value.get("artifact_urls")}).get("artifact_urls", {})
    if isinstance(value.get("citations"), list):
        projected["citations"] = _project_evidence_list(value.get("citations"))
    for key in ("action_plan", "artifact_warnings"):
        if isinstance(value.get(key), list):
            projected[key] = _public_text_list(value.get(key), limit=480)
    for key in ("output_contract", "produce_offer", "pm_skill", "maf", "experiment_attachment"):
        if isinstance(value.get(key), Mapping):
            projected[key] = _project_output_contract(value.get(key)) if key == "output_contract" else _project_simple_status(value.get(key))
    if isinstance(value.get("actor"), Mapping):
        projected["actor"] = _project_actor(value.get("actor"))
    if isinstance(value.get("trace"), Mapping):
        projected["trace"] = {
            key: identifier
            for key in ("trace_id", "agent_id")
            if (identifier := _public_identifier(value["trace"].get(key))) is not None
        }
    if isinstance(value.get("reference_images"), list):
        projected["reference_images"] = [
            item
            for raw in value["reference_images"][:12]
            if (item := _project_proposal_item(raw))
        ]
    return projected


def public_artifact_projection(value: Any, expected_scope: Mapping[str, Any] | None = None) -> Any:
    """Project one final/artifact envelope without arbitrary nested pass-through."""
    if not isinstance(value, Mapping):
        return {}
    projected = sanitize_conversation_metadata(value, expected_scope)
    raw_packs = value.get("capability_packs")
    has_capability_metadata = "capability_packs" in value or "capability_pack_ids" in value
    records, provenance = sanitize_capability_pack_contract(
        raw_packs if isinstance(raw_packs, list) else [],
        value.get("capability_pack_provenance"),
        expected_scope,
    )
    if has_capability_metadata and provenance:
        projected["capability_packs"] = records
        projected["capability_pack_ids"] = [str(record["pack_id"]) for record in records]
        projected["capability_pack_integrity"] = {
            "status": "verified",
            "source": _CAPABILITY_PROVENANCE_SOURCE,
            "version": _CAPABILITY_PROVENANCE_VERSION,
        }
    return projected


def _project_answer_delta(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    delta = _public_text(data.get("delta"), 8192)
    return {"delta": delta} if delta is not None else {}


def _project_final(data: Any, conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    artifact = data.get("artifact")
    workspace_id = artifact.get("workspace_id") if isinstance(artifact, Mapping) else None
    return public_artifact_projection(
        data,
        {"workspace_id": workspace_id, "scope_id": conversation_id},
    )


def _project_model_response(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected = _llm_metadata_projection(data)
    for key in ("agent", "orchestrator", "status", "error_category"):
        if identifier := _public_identifier(data.get(key)):
            projected[key] = identifier
    if (revision := _public_number(data.get("revision"))) is not None:
        projected["revision"] = max(0, int(revision))
    if isinstance(data.get("retry_count"), int) and not isinstance(data.get("retry_count"), bool):
        projected["retry_count"] = max(0, min(100, data["retry_count"]))
    if isinstance(data.get("tool_names"), (list, tuple)):
        projected["tool_names"] = _public_identifier_list(data.get("tool_names"))
    if isinstance(data.get("cache_hit"), bool):
        projected["cache_hit"] = data["cache_hit"]
    return projected


def _project_followup(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected: dict[str, Any] = {}
    if text := _public_text(data.get("text")):
        projected["text"] = text
    route = conversation_route_projection(data.get("conversation_route") or data.get("mode"))
    if route:
        projected["conversation_route"] = route
    if isinstance(data.get("clarify"), Mapping):
        clarify = _project_clarify(data.get("clarify"))
        if clarify:
            projected["clarify"] = clarify
    if isinstance(data.get("lightweight"), bool):
        projected["lightweight"] = data["lightweight"]
    projected["experts"] = _public_identifier_list(data.get("experts"))
    if not projected["experts"] and not isinstance(data.get("experts"), list):
        projected.pop("experts")
    return projected


def _project_clarify_event(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    return _project_clarify(data)


def _project_route_event(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected = _routing_projection(data)
    route = conversation_route_projection(data.get("conversation_route") or data.get("mode"))
    if route:
        projected.update(
            {
                "mode": route["mode"],
                "conversation_route": route["mode"],
                "reason": route["reason"],
                "route_reason": route["reason"],
                "evidence_required": route["evidence_required"],
            }
        )
    if (revision := _public_number(data.get("evidence_revision"))) is not None:
        projected["evidence_revision"] = max(0, int(revision))
    return projected


def _project_progress(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected = _project_simple_status(data)
    for key in ("agent", "name", "branch_id"):
        if identifier := _public_identifier(data.get(key)):
            projected[key] = identifier
    return projected


def _project_tool_call(data: Any, conversation_id: str | None) -> dict[str, Any]:
    projected = _project_progress(data, conversation_id)
    if isinstance(data, Mapping) and isinstance(data.get("autonomous"), bool):
        projected["autonomous"] = data["autonomous"]
    return projected


def _project_tool_result(data: Any, conversation_id: str | None) -> dict[str, Any]:
    projected = _project_tool_call(data, conversation_id)
    if not isinstance(data, Mapping):
        return projected
    for key in ("count", "bytes"):
        if (number := _public_number(data.get(key))) is not None:
            projected[key] = max(0, int(number))
    if url := _public_url(data.get("artifact_url")):
        projected["artifact_url"] = url
    for key in ("retrieval_modes", "tool_names"):
        if isinstance(data.get(key), (list, tuple)):
            projected[key] = _public_identifier_list(data.get(key))
    return projected


def _project_audit_event(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    return _project_audit(data)


def _project_revised_verdict(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    return _project_verdict_summary(data)


def _project_error(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    projected: dict[str, Any] = {"code": "request_failed"}
    if isinstance(data, Mapping) and isinstance(data.get("retryable"), bool):
        projected["retryable"] = data["retryable"]
    return projected


def _project_ready(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("run_id", "conversation_id", "workspace_id", "origin"):
        if identifier := _public_identifier(data.get(key)):
            projected[key] = identifier
    if isinstance(data.get("trace"), Mapping):
        projected["trace"] = {
            key: identifier
            for key in ("trace_id", "agent_id")
            if (identifier := _public_identifier(data["trace"].get(key))) is not None
        }
    if (revision := _public_number(data.get("evidence_revision"))) is not None:
        projected["evidence_revision"] = max(0, int(revision))
    return projected


def _project_content_safety(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in ("blocked", "jailbreak"):
        if isinstance(data.get(key), bool):
            projected[key] = data[key]
    if isinstance(data.get("categories"), (list, tuple)):
        projected["categories"] = _public_identifier_list(data.get("categories"), items=12)
    return projected


def _project_iteration_inputs(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    count = _public_number(data.get("count"))
    return {"count": max(0, int(count))} if count is not None else {}


def _project_blind_verdict(data: Any, _conversation_id: str | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    projected: dict[str, Any] = {}
    if agent := _public_identifier(data.get("agent")):
        projected["agent"] = agent
    if isinstance(data.get("verdict"), Mapping):
        projected["verdict"] = _project_verdict_summary(data.get("verdict"))
    dimensions: list[dict[str, Any]] = []
    for raw in (data.get("dimensions") or [])[:10] if isinstance(data.get("dimensions"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        if text := _public_text(raw.get("dim"), 120):
            item["dim"] = text
        if (score := _public_number(raw.get("score"))) is not None:
            item["score"] = score
        if confidence := _public_identifier(raw.get("confidence")):
            item["confidence"] = confidence
        if item:
            dimensions.append(item)
    if dimensions:
        projected["dimensions"] = dimensions
    return projected


def _project_maf_status(data: Any, conversation_id: str | None) -> dict[str, Any]:
    projected = _project_model_response(data, conversation_id)
    if not isinstance(data, Mapping):
        return projected
    for key in ("sequence", "duration_ms", "max_revisions"):
        if (number := _public_number(data.get(key))) is not None:
            projected[key] = number
    for key in ("branch_id", "source_agent_id", "target_agent_id"):
        if identifier := _public_identifier(data.get(key)):
            projected[key] = identifier
    for key in ("reason_codes", "selected_agents", "skipped_agents"):
        if isinstance(data.get(key), (list, tuple)):
            projected[key] = _public_identifier_list(data.get(key))
    return projected


_PUBLIC_EVENT_PROJECTORS = {
    "answer_delta": _project_answer_delta,
    "delta": _project_answer_delta,
    "final": _project_final,
    "model_response": _project_model_response,
    "followup": _project_followup,
    "clarify": _project_clarify_event,
    "route": _project_route_event,
    "plan": _project_route_event,
    "progress": _project_progress,
    "role_change": _project_progress,
    "tool_call": _project_tool_call,
    "tool_result": _project_tool_result,
    "audit": _project_audit_event,
    "revised_verdict": _project_revised_verdict,
    "error": _project_error,
    "ready": _project_ready,
    "content_safety": _project_content_safety,
    "iteration_inputs": _project_iteration_inputs,
    "blind_verdict": _project_blind_verdict,
    "cache": _project_progress,
    "maf_workflow": _project_simple_status,
    "maf_fallback": _project_maf_status,
    "market_relevance_gate": _project_simple_status,
    "capability_pack_selection": _project_simple_status,
}


def public_conversation_event(
    event: str,
    data: Any,
    conversation_id: str | None,
) -> dict[str, Any]:
    """Return the only payload permitted to reach public conversation sinks."""
    event_name = str(event or "")
    projector = _PUBLIC_EVENT_PROJECTORS.get(event_name)
    if projector is None and event_name.startswith("maf_"):
        projector = _project_maf_status
    return projector(data, conversation_id) if projector else {}


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
    "conversation_route_projection",
    "internally_selected_capability_pack_contract",
    "public_artifact_projection",
    "public_conversation_event",
    "sanitize_capability_metadata",
    "sanitize_capability_pack_contract",
    "sanitize_capability_pack_records",
    "sanitize_conversation_metadata",
]

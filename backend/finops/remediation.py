from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .governance import FinOpsActionService, validate_typed_action_payload


DraftStatus = Literal["draft", "reviewed", "pending_approval", "promoted", "closed"]
ActionKind = Literal[
    "cache_policy", "model_route", "price_mapping", "budget_notification", "investigation"
]
ExecutionCapability = Literal["advisory_only", "typed_action_available"]


class RemediationConflict(RuntimeError):
    pass


class RemediationNotFound(KeyError):
    pass


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[
        "ttl_seconds",
        "enabled",
        "deployment",
        "price_mapping",
        "notification_threshold",
        "investigation_scope",
    ]
    current_value: bool | int | float | str | None
    candidate_value: bool | int | float | str
    rationale: str = Field(min_length=1, max_length=500)


class ExpectedImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None
    unit: Literal["USD", "percentage_point", "milliseconds", "requests"] | None
    status: Literal["observed", "estimated", "partial", "unavailable"]
    calculation_basis: str = Field(min_length=1, max_length=500)


class VerificationCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal[
        "cache_hit_rate_pct",
        "unit_cost",
        "result_consistency_pct",
        "success_rate_pct",
        "p95_latency_ms",
        "pricing_coverage_pct",
    ]
    operator: Literal["gte", "lte", "no_worse_than_pct"]
    baseline_value: float | None
    baseline_window: str = Field(min_length=1, max_length=128)
    target: float
    candidate_window_minutes: int = Field(ge=5, le=10080)
    minimum_samples: int = Field(ge=20, le=100000)


class RemediationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    tenant_ref: str
    workspace_id: str
    source_opportunity_id: str
    source_anomaly_id: str | None
    risk_type: str
    title: str
    summary: str
    scope: dict[str, str | None]
    evidence_refs: list[str]
    proposed_changes: list[ProposedChange]
    expected_impact: ExpectedImpact
    prerequisites: list[str]
    risks_and_guardrails: list[str]
    verification_plan: list[VerificationCriterion]
    rollback_plan: list[str]
    action_kind: ActionKind
    execution_capability: ExecutionCapability
    base_version: str
    status: DraftStatus
    revision: int = Field(ge=1)
    created_by: str
    reviewed_by: str | None = None
    translated_action_id: str | None = None
    created_at: str
    updated_at: str


class RemediationDraftRepository(Protocol):
    def save(self, draft: RemediationDraft, *, expected_revision: int) -> RemediationDraft: ...
    def get(self, tenant_ref: str, draft_id: str) -> RemediationDraft | None: ...
    def list(self, tenant_ref: str) -> list[RemediationDraft]: ...


class InMemoryRemediationDraftRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._drafts: dict[tuple[str, str], RemediationDraft] = {}

    def save(self, draft: RemediationDraft, *, expected_revision: int) -> RemediationDraft:
        with self._lock:
            existing = self._drafts.get((draft.tenant_ref, draft.draft_id))
            current_revision = existing.revision if existing is not None else 0
            if current_revision != expected_revision:
                raise RemediationConflict("remediation revision conflict")
            self._drafts[(draft.tenant_ref, draft.draft_id)] = draft.model_copy(deep=True)
        return draft.model_copy(deep=True)

    def get(self, tenant_ref: str, draft_id: str) -> RemediationDraft | None:
        with self._lock:
            draft = self._drafts.get((tenant_ref, draft_id))
        return draft.model_copy(deep=True) if draft else None

    def list(self, tenant_ref: str) -> list[RemediationDraft]:
        with self._lock:
            rows = [
                value.model_copy(deep=True)
                for (tenant, _), value in self._drafts.items()
                if tenant == tenant_ref
            ]
        return sorted(rows, key=lambda item: (item.created_at, item.draft_id), reverse=True)


VersionResolver = Callable[[str, str, ActionKind], str]
_PayloadTranslator = Callable[[RemediationDraft], tuple[str, dict[str, Any]]]


class FinOpsRemediationService:
    def __init__(
        self,
        *,
        repository: RemediationDraftRepository,
        action_service: FinOpsActionService,
        version_resolver: VersionResolver,
    ) -> None:
        self._repository = repository
        self._action_service = action_service
        self._version_resolver = version_resolver
        self._translators: dict[ActionKind, _PayloadTranslator] = {
            "cache_policy": _translate_cache_policy,
        }

    def create(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        actor_ref: str,
        opportunity: dict[str, object],
        base_version: str,
    ) -> RemediationDraft:
        policy_type = str(opportunity.get("policy_type") or "")
        template = _template(policy_type, workspace_id, base_version)
        action_kind = template["action_kind"]
        execution_capability: ExecutionCapability = (
            "typed_action_available" if action_kind in self._translators else "advisory_only"
        )
        if execution_capability == "typed_action_available":
            current_version = self._version_resolver(tenant_ref, workspace_id, action_kind)
            if base_version != current_version:
                raise RemediationConflict("base version changed")

        now = _now()
        draft = RemediationDraft(
            draft_id=f"remediation_{uuid4().hex}",
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            source_opportunity_id=_required_safe_identifier(opportunity.get("opportunity_id")),
            source_anomaly_id=_optional_safe_identifier(opportunity.get("anomaly_id")),
            risk_type=policy_type if policy_type in _POLICY_TYPES else "investigation",
            title=template["title"],
            summary=template["summary"],
            scope={"workspace_id": workspace_id, "resource_id": None},
            evidence_refs=_safe_evidence_refs(opportunity.get("evidence_refs")),
            proposed_changes=template["proposed_changes"],
            expected_impact=template["expected_impact"],
            prerequisites=template["prerequisites"],
            risks_and_guardrails=template["risks_and_guardrails"],
            verification_plan=template["verification_plan"],
            rollback_plan=template["rollback_plan"],
            action_kind=action_kind,
            execution_capability=execution_capability,
            base_version=base_version,
            status="draft",
            revision=1,
            created_by=actor_ref,
            created_at=now,
            updated_at=now,
        )
        return self._repository.save(draft, expected_revision=0)

    def review(
        self,
        *,
        tenant_ref: str,
        draft_id: str,
        actor_ref: str,
        base_revision: int,
        authorized_workspace_ids: tuple[str, ...],
    ) -> RemediationDraft:
        draft = self._require(tenant_ref, draft_id, authorized_workspace_ids)
        self._check_revision(draft, base_revision)
        if draft.status != "draft":
            raise RemediationConflict("only draft remediation may be reviewed")
        draft.status = "reviewed"
        draft.reviewed_by = actor_ref
        draft.revision += 1
        draft.updated_at = _now()
        return self._repository.save(draft, expected_revision=base_revision)

    def close(
        self,
        *,
        tenant_ref: str,
        draft_id: str,
        actor_ref: str,
        base_revision: int,
        authorized_workspace_ids: tuple[str, ...],
    ) -> RemediationDraft:
        del actor_ref
        draft = self._require(tenant_ref, draft_id, authorized_workspace_ids)
        self._check_revision(draft, base_revision)
        if draft.status not in {"draft", "reviewed", "pending_approval"}:
            raise RemediationConflict("only active remediation may be closed")
        draft.status = "closed"
        draft.revision += 1
        draft.updated_at = _now()
        return self._repository.save(draft, expected_revision=base_revision)

    def promote(
        self,
        *,
        tenant_ref: str,
        draft_id: str,
        actor_ref: str,
        base_revision: int,
        authorized_workspace_ids: tuple[str, ...],
    ) -> RemediationDraft:
        draft = self._require(tenant_ref, draft_id, authorized_workspace_ids)
        self._check_revision(draft, base_revision)
        if draft.execution_capability == "advisory_only":
            raise RemediationConflict("advisory draft cannot be promoted")
        if draft.status == "reviewed":
            draft.status = "pending_approval"
            draft.revision += 1
            draft.updated_at = _now()
            draft = self._repository.save(draft, expected_revision=base_revision)
        elif draft.status != "pending_approval":
            raise RemediationConflict("only reviewed remediation may be promoted")

        translator = self._translators.get(draft.action_kind)
        if translator is None:
            raise RemediationConflict("typed translator is unavailable")
        action_type, payload = translator(draft)
        clean_payload = validate_typed_action_payload(action_type, payload)
        action = self._action_service.create(
            tenant_ref=tenant_ref,
            action_type=action_type,
            payload=clean_payload,
            actor_ref=actor_ref,
            actor_kind="human",
            action_id=_remediation_action_id(draft),
        )
        pending_revision = draft.revision
        draft.status = "promoted"
        draft.translated_action_id = action.action_id
        draft.revision += 1
        draft.updated_at = _now()
        return self._repository.save(draft, expected_revision=pending_revision)

    def list(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
    ) -> list[RemediationDraft]:
        allowed = set(authorized_workspace_ids)
        return [
            draft
            for draft in self._repository.list(tenant_ref)
            if draft.workspace_id in allowed
        ]

    def get(
        self,
        *,
        tenant_ref: str,
        draft_id: str,
        authorized_workspace_ids: tuple[str, ...],
    ) -> RemediationDraft:
        return self._require(tenant_ref, draft_id, authorized_workspace_ids)

    def _require(
        self,
        tenant_ref: str,
        draft_id: str,
        authorized_workspace_ids: tuple[str, ...],
    ) -> RemediationDraft:
        draft = self._repository.get(tenant_ref, draft_id)
        if draft is None:
            raise RemediationNotFound(draft_id)
        if draft.workspace_id not in set(authorized_workspace_ids):
            raise RemediationNotFound(draft_id)
        return draft

    @staticmethod
    def _check_revision(draft: RemediationDraft, base_revision: int) -> None:
        if draft.revision != base_revision:
            raise RemediationConflict("remediation revision conflict")


_POLICY_TYPES = frozenset(
    {
        "cache_hit_rate",
        "p95_latency",
        "error_rate",
        "unpriced_requests",
        "daily_cost_budget",
    }
)


def _template(policy_type: str, workspace_id: str, base_version: str) -> dict[str, Any]:
    del workspace_id, base_version
    unknown_impact = ExpectedImpact(
        amount=None,
        unit=None,
        status="unavailable",
        calculation_basis="No quantified impact is available from the authorized opportunity alone.",
    )
    common = {
        "expected_impact": unknown_impact,
        "prerequisites": ["Confirm the evidence window and minimum sample count."],
        "risks_and_guardrails": [
            "This draft is a proposal only; no production configuration is changed.",
            "Do not introduce scripts, policy documents, URLs, or secrets into the draft.",
        ],
        "rollback_plan": ["Close the draft or use the existing governed rollback workflow after an approved action."],
    }
    if policy_type == "cache_hit_rate":
        return {
            **common,
            "title": "Cache policy review",
            "summary": "Review the server-defined cache policy candidate against observed service criteria.",
            "proposed_changes": [
                ProposedChange(
                    field="ttl_seconds",
                    current_value=None,
                    candidate_value=1800,
                    rationale="Use the approved candidate duration for a controlled cache-policy review.",
                )
            ],
            "verification_plan": [
                _criterion("cache_hit_rate_pct", "gte", 70),
                _criterion("unit_cost", "no_worse_than_pct", 5),
                _criterion("result_consistency_pct", "no_worse_than_pct", 2),
                _criterion("p95_latency_ms", "no_worse_than_pct", 5),
            ],
            "action_kind": "cache_policy",
        }
    if policy_type == "p95_latency":
        template = _investigation_template(
            common,
            title="Latency investigation",
            summary="Compare model and batch characteristics before any separately selected route change.",
            target=5,
        )
        template["verification_plan"] = [
            _criterion("result_consistency_pct", "no_worse_than_pct", 2),
            _criterion("p95_latency_ms", "no_worse_than_pct", 5),
        ]
        return template
    if policy_type == "error_rate":
        return _investigation_template(
            common,
            title="Error-rate investigation",
            summary="Classify failure patterns before proposing a separately governed configuration change.",
            target=99,
        )
    if policy_type == "unpriced_requests":
        return {
            **common,
            "title": "Pricing coverage review",
            "summary": "An administrator must select an official price revision through the existing settings workflow.",
            "proposed_changes": [
                ProposedChange(
                    field="price_mapping",
                    current_value=None,
                    candidate_value="administrator-selected official price revision",
                    rationale="Price mapping remains an administrator-owned settings decision.",
                )
            ],
            "verification_plan": [_criterion("pricing_coverage_pct", "gte", 95)],
            "action_kind": "price_mapping",
        }
    if policy_type == "daily_cost_budget":
        return {
            **common,
            "title": "Budget notification review",
            "summary": "Review the existing notification configuration; this cannot create a quota action.",
            "proposed_changes": [
                ProposedChange(
                    field="notification_threshold",
                    current_value=None,
                    candidate_value="review existing notification configuration",
                    rationale="Budget notifications remain advisory until configured through the existing workflow.",
                )
            ],
            "verification_plan": [_criterion("unit_cost", "no_worse_than_pct", 5)],
            "action_kind": "budget_notification",
        }
    return _investigation_template(
        common,
        title="Operations investigation",
        summary="Review the authorized evidence before selecting any separately governed change.",
        target=99,
    )


def _investigation_template(
    common: dict[str, Any],
    *,
    title: str,
    summary: str,
    target: float,
) -> dict[str, Any]:
    return {
        **common,
        "title": title,
        "summary": summary,
        "proposed_changes": [
            ProposedChange(
                field="investigation_scope",
                current_value=None,
                candidate_value="compare model and batch characteristics",
                rationale="Gather bounded evidence before selecting a separate typed action.",
            )
        ],
        "verification_plan": [
            _criterion("success_rate_pct", "gte", target),
            _criterion("p95_latency_ms", "no_worse_than_pct", 5),
        ],
        "action_kind": "investigation",
    }


def _criterion(
    metric: Literal[
        "cache_hit_rate_pct",
        "unit_cost",
        "result_consistency_pct",
        "success_rate_pct",
        "p95_latency_ms",
        "pricing_coverage_pct",
    ],
    operator: Literal["gte", "lte", "no_worse_than_pct"],
    target: float,
) -> VerificationCriterion:
    return VerificationCriterion(
        metric=metric,
        operator=operator,
        baseline_value=None,
        baseline_window="authorized evidence window",
        target=target,
        candidate_window_minutes=60,
        minimum_samples=20,
    )


def _translate_cache_policy(draft: RemediationDraft) -> tuple[str, dict[str, Any]]:
    ttl_change = next(change for change in draft.proposed_changes if change.field == "ttl_seconds")
    return "cache_policy", {
        "workspace_id": draft.workspace_id,
        "enabled": True,
        "ttl_seconds": ttl_change.candidate_value,
        "base_version": draft.base_version,
    }


def _remediation_action_id(draft: RemediationDraft) -> str:
    material = f"{draft.tenant_ref}:{draft.workspace_id}:{draft.draft_id}".encode("utf-8")
    return f"remediation_{hashlib.sha256(material).hexdigest()}"


def _required_safe_identifier(value: object) -> str:
    identifier = _optional_safe_identifier(value)
    if identifier is None:
        raise ValueError("authorized opportunity requires a safe identifier")
    return identifier


def _optional_safe_identifier(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 128:
        return None
    if not candidate.replace("-", "").replace("_", "").isalnum():
        return None
    return candidate


def _safe_evidence_refs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        identifier
        for item in value[:5]
        if (identifier := _optional_safe_identifier(item)) is not None
    ))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

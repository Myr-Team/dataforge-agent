from __future__ import annotations

import pytest

from backend.finops.governance import FinOpsActionService, InMemoryActionRepository
from backend.finops.remediation import (
    FinOpsRemediationService,
    InMemoryRemediationDraftRepository,
    RemediationConflict,
    RemediationNotFound,
)


def _service(current_version: str = "cache-policy-v1") -> FinOpsRemediationService:
    return FinOpsRemediationService(
        repository=InMemoryRemediationDraftRepository(),
        action_service=FinOpsActionService(
            repository=InMemoryActionRepository(),
            executors={},
        ),
        version_resolver=lambda tenant_ref, workspace_id, action_kind: (
            current_version if action_kind == "cache_policy" else "investigation-v1"
        ),
    )


def _opportunity(policy_type: str) -> dict[str, object]:
    return {
        "opportunity_id": f"opp-{policy_type}",
        "anomaly_id": f"anom-{policy_type}",
        "policy_type": policy_type,
        "title": "client supplied title must not be trusted",
        "recommendation": "client supplied prose must not be trusted",
        "evidence_refs": [f"req-{policy_type}-000001"],
        "sample_count": 54,
    }


def test_cache_opportunity_creates_reviewable_draft_without_execution() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )

    assert draft.status == "draft"
    assert draft.action_kind == "cache_policy"
    assert draft.execution_capability == "typed_action_available"
    assert draft.proposed_changes[0].field == "ttl_seconds"
    assert draft.proposed_changes[0].candidate_value == 1800
    assert draft.translated_action_id is None
    assert "client supplied" not in draft.title
    assert "client supplied" not in draft.summary


def test_investigation_draft_cannot_promote() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("error_rate"),
        base_version="investigation-v1",
    )
    reviewed = service.review(
        tenant_ref="tenant-a",
        draft_id=draft.draft_id,
        actor_ref="owner-b",
        base_revision=1,
        authorized_workspace_ids=("ws-a",),
    )
    with pytest.raises(RemediationConflict, match="advisory draft"):
        service.promote(
            tenant_ref="tenant-a",
            draft_id=reviewed.draft_id,
            actor_ref="owner-b",
            base_revision=2,
            authorized_workspace_ids=("ws-a",),
        )


def test_review_rejects_stale_revision() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    with pytest.raises(RemediationConflict, match="revision"):
        service.review(
            tenant_ref="tenant-a",
            draft_id=draft.draft_id,
            actor_ref="owner-b",
            base_revision=0,
            authorized_workspace_ids=("ws-a",),
        )


def test_create_rejects_stale_base_version_before_save() -> None:
    service = _service(current_version="cache-policy-v2")
    with pytest.raises(RemediationConflict, match="base version"):
        service.create(
            tenant_ref="tenant-a",
            workspace_id="ws-a",
            actor_ref="owner-a",
            opportunity=_opportunity("cache_hit_rate"),
            base_version="cache-policy-v1",
        )
    assert service.list(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
    ) == []


def test_promote_creates_only_a_human_proposed_draft_action() -> None:
    action_repository = InMemoryActionRepository()
    action_service = FinOpsActionService(repository=action_repository, executors={})
    service = FinOpsRemediationService(
        repository=InMemoryRemediationDraftRepository(),
        action_service=action_service,
        version_resolver=lambda tenant_ref, workspace_id, action_kind: "cache-policy-v1",
    )
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    reviewed = service.review(
        tenant_ref="tenant-a",
        draft_id=draft.draft_id,
        actor_ref="owner-b",
        base_revision=draft.revision,
        authorized_workspace_ids=("ws-a",),
    )

    promoted = service.promote(
        tenant_ref="tenant-a",
        draft_id=reviewed.draft_id,
        actor_ref="owner-b",
        base_revision=reviewed.revision,
        authorized_workspace_ids=("ws-a",),
    )

    assert promoted.status == "promoted"
    assert promoted.revision == 4
    assert promoted.translated_action_id is not None
    actions = action_service.list(tenant_ref="tenant-a")
    assert len(actions) == 1
    assert actions[0].action_type == "cache_policy"
    assert actions[0].status == "draft"
    assert actions[0].proposed_by == "owner-b"
    assert [transition.to_status for transition in actions[0].transitions] == ["draft"]
    assert actions[0].payload == {
        "workspace_id": "ws-a",
        "enabled": True,
        "ttl_seconds": 1800,
        "base_version": "cache-policy-v1",
    }


def test_price_mapping_and_budget_notifications_remain_advisory() -> None:
    service = _service()
    for policy_type, base_version, action_kind in (
        ("unpriced_requests", "evidence-price-v1", "price_mapping"),
        ("daily_cost_budget", "evidence-budget-v1", "budget_notification"),
    ):
        draft = service.create(
            tenant_ref="tenant-a",
            workspace_id="ws-a",
            actor_ref="owner-a",
            opportunity=_opportunity(policy_type),
            base_version=base_version,
        )
        assert draft.action_kind == action_kind
        assert draft.execution_capability == "advisory_only"


def test_latency_draft_uses_advisory_model_batch_comparison_criteria() -> None:
    draft = _service().create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("p95_latency"),
        base_version="evidence-latency-v1",
    )

    assert draft.action_kind == "investigation"
    assert draft.execution_capability == "advisory_only"
    assert draft.proposed_changes[0].candidate_value == "compare model and batch characteristics"
    assert {criterion.metric for criterion in draft.verification_plan} == {
        "result_consistency_pct",
        "p95_latency_ms",
    }


def test_unit_cost_criteria_do_not_treat_unknown_cost_as_zero() -> None:
    service = _service()
    for policy_type, base_version in (
        ("cache_hit_rate", "cache-policy-v1"),
        ("daily_cost_budget", "evidence-budget-v1"),
    ):
        draft = service.create(
            tenant_ref="tenant-a",
            workspace_id="ws-a",
            actor_ref="owner-a",
            opportunity=_opportunity(policy_type),
            base_version=base_version,
        )
        criterion = next(item for item in draft.verification_plan if item.metric == "unit_cost")
        assert criterion.baseline_value is None
        assert criterion.operator == "no_worse_than_pct"
        assert criterion.target == 5


def test_repository_cas_rejects_a_concurrent_stale_transition() -> None:
    repository = InMemoryRemediationDraftRepository()
    service = FinOpsRemediationService(
        repository=repository,
        action_service=FinOpsActionService(repository=InMemoryActionRepository(), executors={}),
        version_resolver=lambda tenant_ref, workspace_id, action_kind: "cache-policy-v1",
    )
    created = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    first = repository.get("tenant-a", created.draft_id)
    second = repository.get("tenant-a", created.draft_id)
    assert first is not None and second is not None
    first.status = "reviewed"
    first.revision = 2
    repository.save(first, expected_revision=1)
    second.status = "closed"
    second.revision = 2

    with pytest.raises(RemediationConflict, match="revision"):
        repository.save(second, expected_revision=1)

    assert repository.get("tenant-a", created.draft_id).status == "reviewed"


def test_workspace_authorization_is_required_for_reads_and_mutations() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    denied = ("ws-other",)

    with pytest.raises(RemediationNotFound):
        service.get(
            tenant_ref="tenant-a",
            draft_id=draft.draft_id,
            authorized_workspace_ids=denied,
        )
    for method in (service.review, service.close, service.promote):
        with pytest.raises(RemediationNotFound):
            method(
                tenant_ref="tenant-a",
                draft_id=draft.draft_id,
                actor_ref="owner-b",
                base_revision=draft.revision,
                authorized_workspace_ids=denied,
            )
    with pytest.raises(TypeError):
        service.get(tenant_ref="tenant-a", draft_id=draft.draft_id)


class _FailFirstActionRepository:
    def __init__(self) -> None:
        self._delegate = InMemoryActionRepository()
        self._failed = False

    def save(self, action):
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated action persistence failure")
        return self._delegate.save(action)

    def get(self, tenant_ref, action_id):
        return self._delegate.get(tenant_ref, action_id)

    def list(self, tenant_ref):
        return self._delegate.list(tenant_ref)


class _FailPromotedDraftSaveRepository(InMemoryRemediationDraftRepository):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def save(self, draft, *, expected_revision):
        if draft.status == "promoted" and not self._failed:
            self._failed = True
            raise RuntimeError("simulated final draft persistence failure")
        return super().save(draft, expected_revision=expected_revision)


def _reviewed_cache_draft(service: FinOpsRemediationService):
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    return service.review(
        tenant_ref="tenant-a",
        draft_id=draft.draft_id,
        actor_ref="owner-b",
        base_revision=draft.revision,
        authorized_workspace_ids=("ws-a",),
    )


def test_action_creation_failure_leaves_pending_draft_for_revisioned_retry() -> None:
    action_repository = _FailFirstActionRepository()
    action_service = FinOpsActionService(repository=action_repository, executors={})
    service = FinOpsRemediationService(
        repository=InMemoryRemediationDraftRepository(),
        action_service=action_service,
        version_resolver=lambda tenant_ref, workspace_id, action_kind: "cache-policy-v1",
    )
    reviewed = _reviewed_cache_draft(service)

    with pytest.raises(RuntimeError, match="action persistence"):
        service.promote(
            tenant_ref="tenant-a",
            draft_id=reviewed.draft_id,
            actor_ref="owner-b",
            base_revision=reviewed.revision,
            authorized_workspace_ids=("ws-a",),
        )
    pending = service.get(
        tenant_ref="tenant-a",
        draft_id=reviewed.draft_id,
        authorized_workspace_ids=("ws-a",),
    )
    assert pending.status == "pending_approval"
    assert pending.revision == 3
    assert pending.translated_action_id is None

    promoted = service.promote(
        tenant_ref="tenant-a",
        draft_id=pending.draft_id,
        actor_ref="owner-b",
        base_revision=pending.revision,
        authorized_workspace_ids=("ws-a",),
    )
    assert promoted.status == "promoted"
    assert len(action_service.list(tenant_ref="tenant-a")) == 1


def test_final_draft_save_retry_reuses_the_single_created_action() -> None:
    action_service = FinOpsActionService(repository=InMemoryActionRepository(), executors={})
    service = FinOpsRemediationService(
        repository=_FailPromotedDraftSaveRepository(),
        action_service=action_service,
        version_resolver=lambda tenant_ref, workspace_id, action_kind: "cache-policy-v1",
    )
    reviewed = _reviewed_cache_draft(service)

    with pytest.raises(RuntimeError, match="final draft persistence"):
        service.promote(
            tenant_ref="tenant-a",
            draft_id=reviewed.draft_id,
            actor_ref="owner-b",
            base_revision=reviewed.revision,
            authorized_workspace_ids=("ws-a",),
        )
    pending = service.get(
        tenant_ref="tenant-a",
        draft_id=reviewed.draft_id,
        authorized_workspace_ids=("ws-a",),
    )
    assert pending.status == "pending_approval"
    assert pending.translated_action_id is None
    assert len(action_service.list(tenant_ref="tenant-a")) == 1

    promoted = service.promote(
        tenant_ref="tenant-a",
        draft_id=pending.draft_id,
        actor_ref="owner-b",
        base_revision=pending.revision,
        authorized_workspace_ids=("ws-a",),
    )
    assert promoted.status == "promoted"
    assert len(action_service.list(tenant_ref="tenant-a")) == 1

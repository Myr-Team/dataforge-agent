from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.finops.remediation import RemediationConflict, RemediationDraft
from backend.finops.sql_remediation import SqlRemediationDraftRepository
from backend.finops.sql_repository import FinOpsPersistenceError


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, operation: str, *parameters: object) -> "_Cursor":
        self.calls.append((operation, parameters))
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self.rows)


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.autocommit = True
        self.cursor_value = _Cursor(rows)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self, *connections: _Connection) -> None:
        self.connections = list(connections)

    def __call__(self) -> _Connection:
        return self.connections.pop(0)


def _draft(
    *,
    tenant_ref: str = "tenant-a",
    workspace_id: str = "ws-a",
    revision: int = 1,
    status: str = "draft",
) -> RemediationDraft:
    return RemediationDraft.model_validate(
        {
            "draft_id": "remediation_safe123",
            "tenant_ref": tenant_ref,
            "workspace_id": workspace_id,
            "source_opportunity_id": "opp-cache",
            "source_anomaly_id": "anomaly-cache",
            "risk_type": "cache_hit_rate",
            "title": "缓存策略复核",
            "summary": "Review the bounded server-defined candidate.",
            "scope": {"workspace_id": workspace_id, "resource_id": None},
            "evidence_refs": ["req_safe123"],
            "proposed_changes": [
                {
                    "field": "ttl_seconds",
                    "current_value": None,
                    "candidate_value": 1800,
                    "rationale": "Use the approved candidate duration.",
                }
            ],
            "expected_impact": {
                "amount": None,
                "unit": None,
                "status": "unavailable",
                "calculation_basis": "No quantified impact is available.",
            },
            "prerequisites": ["Confirm evidence."],
            "risks_and_guardrails": ["Draft only."],
            "verification_plan": [
                {
                    "metric": "cache_hit_rate_pct",
                    "operator": "gte",
                    "baseline_value": None,
                    "baseline_window": "authorized evidence window",
                    "target": 70,
                    "candidate_window_minutes": 60,
                    "minimum_samples": 20,
                }
            ],
            "rollback_plan": ["Close the draft."],
            "action_kind": "cache_policy",
            "execution_capability": "typed_action_available",
            "base_version": "cache-policy-v1",
            "status": status,
            "revision": revision,
            "created_by": "actor-owner",
            "reviewed_by": "actor-reviewer" if status != "draft" else None,
            "translated_action_id": None,
            "created_at": "2026-07-31T01:00:00Z",
            "updated_at": "2026-07-31T01:00:00Z",
        }
    )


def _row(draft: RemediationDraft) -> tuple[object, ...]:
    def encoded(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    return (
        draft.draft_id,
        draft.workspace_id,
        draft.source_opportunity_id,
        draft.source_anomaly_id,
        draft.risk_type,
        draft.title,
        draft.summary,
        encoded(draft.scope),
        encoded(draft.evidence_refs),
        encoded([item.model_dump(mode="json") for item in draft.proposed_changes]),
        encoded(draft.expected_impact.model_dump(mode="json")),
        encoded(draft.prerequisites),
        encoded(draft.risks_and_guardrails),
        encoded([item.model_dump(mode="json") for item in draft.verification_plan]),
        encoded(draft.rollback_plan),
        draft.action_kind,
        draft.execution_capability,
        draft.base_version,
        draft.status,
        draft.revision,
        draft.created_by,
        draft.reviewed_by,
        draft.translated_action_id,
        datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc),
    )


def test_sql_remediation_repository_is_tenant_scoped() -> None:
    draft = _draft()
    save_connection = _Connection(rows=[(None, "draft", 1)])
    wrong_tenant_connection = _Connection()
    correct_tenant_connection = _Connection(rows=[_row(draft)])
    repository = SqlRemediationDraftRepository(
        connection_factory=_Factory(
            save_connection,
            wrong_tenant_connection,
            correct_tenant_connection,
        )
    )

    saved = repository.save(
        draft,
        expected_revision=0,
        actor_ref="actor-owner",
    )

    assert repository.get("tenant-b", saved.draft_id) is None
    assert repository.get("tenant-a", saved.draft_id) == saved
    wrong_query, wrong_parameters = wrong_tenant_connection.cursor_value.calls[0]
    assert "WHERE tenant_ref = ? AND draft_id = ?" in wrong_query
    assert wrong_parameters == ("tenant-b", draft.draft_id)


def test_sql_remediation_save_uses_atomic_workspace_scoped_revision_cas() -> None:
    connection = _Connection(rows=[("draft", "reviewed", 2)])
    repository = SqlRemediationDraftRepository(connection_factory=lambda: connection)
    reviewed = _draft(revision=2, status="reviewed")

    saved = repository.save(
        reviewed,
        expected_revision=1,
        actor_ref="actor-reviewer",
        reason="evidence confirmed",
    )

    operation, parameters = connection.cursor_value.calls[0]
    normalized = " ".join(operation.split())
    assert "MERGE df_finops.remediation_draft WITH (HOLDLOCK)" in normalized
    assert "target.tenant_ref = source.tenant_ref" in normalized
    assert "target.draft_id = source.draft_id" in normalized
    assert "target.workspace_id = source.workspace_id" in normalized
    assert "target.revision = source.expected_revision" in normalized
    assert parameters[:4] == (
        reviewed.tenant_ref,
        reviewed.workspace_id,
        reviewed.draft_id,
        1,
    )
    assert saved == reviewed
    transition_query, transition_parameters = connection.cursor_value.calls[1]
    assert "finops:insert-remediation-transition" in transition_query
    assert transition_parameters[:5] == (
        reviewed.tenant_ref,
        reviewed.workspace_id,
        reviewed.draft_id,
        "draft",
        "reviewed",
    )
    assert transition_parameters[5:7] == (
        "actor-reviewer",
        "evidence confirmed",
    )
    assert connection.commits == 1


def test_sql_remediation_save_rejects_stale_revision_without_insert() -> None:
    connection = _Connection()
    repository = SqlRemediationDraftRepository(connection_factory=lambda: connection)

    with pytest.raises(RemediationConflict, match="revision conflict"):
        repository.save(
            _draft(revision=2, status="reviewed"),
            expected_revision=1,
            actor_ref="actor-reviewer",
            reason="must not be appended",
        )

    assert len(connection.cursor_value.calls) == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_sql_remediation_serializes_json_as_sorted_compact_utf8() -> None:
    connection = _Connection(rows=[(None, "draft", 1)])
    repository = SqlRemediationDraftRepository(connection_factory=lambda: connection)

    repository.save(
        _draft(),
        expected_revision=0,
        actor_ref="actor-owner",
    )

    serialized_parameters = "|".join(str(value) for value in connection.cursor_value.calls[0][1])
    assert "缓存策略复核" in serialized_parameters
    assert "\\u7f13" not in serialized_parameters
    assert '"resource_id":null,"workspace_id":"ws-a"' in serialized_parameters


def test_sql_remediation_wraps_database_failures_without_internal_details() -> None:
    class _FailingCursor(_Cursor):
        def execute(self, operation: str, *parameters: object) -> "_Cursor":
            raise RuntimeError("server=internal; password=do-not-expose")

    connection = _Connection()
    connection.cursor_value = _FailingCursor()
    repository = SqlRemediationDraftRepository(connection_factory=lambda: connection)

    with pytest.raises(FinOpsPersistenceError) as captured:
        repository.get("tenant-a", "remediation_safe123")

    assert str(captured.value) == "FinOps remediation SQL operation failed"
    assert "password" not in str(captured.value)
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_sql_remediation_transition_failure_rolls_back_draft_and_audit() -> None:
    class _FailingTransitionCursor(_Cursor):
        def execute(self, operation: str, *parameters: object) -> "_Cursor":
            result = super().execute(operation, *parameters)
            if "finops:insert-remediation-transition" in operation:
                raise RuntimeError("audit insert failed with internal detail")
            return result

    connection = _Connection(rows=[("draft", "reviewed", 2)])
    connection.cursor_value = _FailingTransitionCursor(
        [("draft", "reviewed", 2)]
    )
    repository = SqlRemediationDraftRepository(connection_factory=lambda: connection)

    with pytest.raises(
        FinOpsPersistenceError,
        match="FinOps remediation SQL operation failed",
    ):
        repository.save(
            _draft(revision=2, status="reviewed"),
            expected_revision=1,
            actor_ref="actor-reviewer",
            reason="evidence confirmed",
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True

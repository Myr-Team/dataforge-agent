from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .remediation import RemediationConflict, RemediationDraft
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlRemediationDraftRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save(
        self,
        draft: RemediationDraft,
        *,
        expected_revision: int,
    ) -> RemediationDraft:
        scope_json = _json(draft.scope)
        evidence_refs_json = _json(draft.evidence_refs)
        proposed_changes_json = _json(
            [item.model_dump(mode="json") for item in draft.proposed_changes]
        )
        expected_impact_json = _json(
            draft.expected_impact.model_dump(mode="json")
        )
        prerequisites_json = _json(draft.prerequisites)
        risks_and_guardrails_json = _json(draft.risks_and_guardrails)
        verification_plan_json = _json(
            [item.model_dump(mode="json") for item in draft.verification_plan]
        )
        rollback_plan_json = _json(draft.rollback_plan)

        result: Any = None
        with self._transaction() as cursor:
            result = cursor.execute(
                """/* finops:save-remediation-draft */
                MERGE df_finops.remediation_draft WITH (HOLDLOCK) AS target
                USING (
                    SELECT ? AS tenant_ref, ? AS workspace_id, ? AS draft_id,
                        ? AS expected_revision
                ) AS source
                ON target.tenant_ref = source.tenant_ref
                    AND target.draft_id = source.draft_id
                WHEN MATCHED
                    AND target.workspace_id = source.workspace_id
                    AND target.revision = source.expected_revision
                THEN UPDATE SET
                    source_opportunity_id = ?,
                    source_anomaly_id = ?,
                    risk_type = ?,
                    title = ?,
                    summary = ?,
                    scope_json = ?,
                    evidence_refs_json = ?,
                    proposed_changes_json = ?,
                    expected_impact_json = ?,
                    prerequisites_json = ?,
                    risks_and_guardrails_json = ?,
                    verification_plan_json = ?,
                    rollback_plan_json = ?,
                    action_kind = ?,
                    execution_capability = ?,
                    base_version = ?,
                    draft_status = ?,
                    revision = ?,
                    reviewed_by_ref = ?,
                    translated_action_id = ?,
                    updated_at = ?
                WHEN NOT MATCHED BY TARGET
                    AND source.expected_revision = 0
                THEN INSERT (
                    tenant_ref, draft_id, workspace_id,
                    source_opportunity_id, source_anomaly_id, risk_type,
                    title, summary, scope_json, evidence_refs_json,
                    proposed_changes_json, expected_impact_json,
                    prerequisites_json, risks_and_guardrails_json,
                    verification_plan_json, rollback_plan_json,
                    action_kind, execution_capability, base_version,
                    draft_status, revision, created_by_ref, reviewed_by_ref,
                    translated_action_id, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                OUTPUT deleted.draft_status, inserted.draft_status,
                    inserted.revision;""",
                draft.tenant_ref,
                draft.workspace_id,
                draft.draft_id,
                expected_revision,
                draft.source_opportunity_id,
                draft.source_anomaly_id,
                draft.risk_type,
                draft.title,
                draft.summary,
                scope_json,
                evidence_refs_json,
                proposed_changes_json,
                expected_impact_json,
                prerequisites_json,
                risks_and_guardrails_json,
                verification_plan_json,
                rollback_plan_json,
                draft.action_kind,
                draft.execution_capability,
                draft.base_version,
                draft.status,
                draft.revision,
                draft.reviewed_by,
                draft.translated_action_id,
                draft.updated_at,
                draft.tenant_ref,
                draft.draft_id,
                draft.workspace_id,
                draft.source_opportunity_id,
                draft.source_anomaly_id,
                draft.risk_type,
                draft.title,
                draft.summary,
                scope_json,
                evidence_refs_json,
                proposed_changes_json,
                expected_impact_json,
                prerequisites_json,
                risks_and_guardrails_json,
                verification_plan_json,
                rollback_plan_json,
                draft.action_kind,
                draft.execution_capability,
                draft.base_version,
                draft.status,
                draft.revision,
                draft.created_by,
                draft.reviewed_by,
                draft.translated_action_id,
                draft.created_at,
                draft.updated_at,
            ).fetchone()
            if result is not None:
                from_status = (
                    str(result[0]) if result[0] is not None else None
                )
                to_status = str(result[1])
                if from_status != to_status:
                    actor_ref = draft.reviewed_by or draft.created_by
                    occurred_at = (
                        draft.created_at if from_status is None else draft.updated_at
                    )
                    cursor.execute(
                        """/* finops:insert-remediation-transition */
                        INSERT INTO df_finops.remediation_transition (
                            tenant_ref, workspace_id, draft_id,
                            from_status, to_status, actor_ref, reason,
                            occurred_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        draft.tenant_ref,
                        draft.workspace_id,
                        draft.draft_id,
                        from_status,
                        to_status,
                        actor_ref,
                        None,
                        occurred_at,
                    )
        if result is None:
            raise RemediationConflict("remediation revision conflict")
        return draft.model_copy(deep=True)

    def get(
        self,
        tenant_ref: str,
        draft_id: str,
    ) -> RemediationDraft | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-remediation-draft */
                SELECT draft_id, workspace_id, source_opportunity_id,
                    source_anomaly_id, risk_type, title, summary, scope_json,
                    evidence_refs_json, proposed_changes_json,
                    expected_impact_json, prerequisites_json,
                    risks_and_guardrails_json, verification_plan_json,
                    rollback_plan_json, action_kind, execution_capability,
                    base_version, draft_status, revision, created_by_ref,
                    reviewed_by_ref, translated_action_id, created_at, updated_at
                FROM df_finops.remediation_draft
                WHERE tenant_ref = ? AND draft_id = ?""",
                tenant_ref,
                draft_id,
            ).fetchone()
            return _draft(tenant_ref, row) if row is not None else None

    def list(self, tenant_ref: str) -> list[RemediationDraft]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-remediation-drafts */
                SELECT draft_id, workspace_id, source_opportunity_id,
                    source_anomaly_id, risk_type, title, summary, scope_json,
                    evidence_refs_json, proposed_changes_json,
                    expected_impact_json, prerequisites_json,
                    risks_and_guardrails_json, verification_plan_json,
                    rollback_plan_json, action_kind, execution_capability,
                    base_version, draft_status, revision, created_by_ref,
                    reviewed_by_ref, translated_action_id, created_at, updated_at
                FROM df_finops.remediation_draft
                WHERE tenant_ref = ?
                ORDER BY created_at DESC, draft_id DESC""",
                tenant_ref,
            ).fetchall()
            return [_draft(tenant_ref, row) for row in rows]

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = None
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise FinOpsPersistenceError(
                "FinOps remediation SQL operation failed"
            ) from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _draft(tenant_ref: str, row: Any) -> RemediationDraft:
    return RemediationDraft.model_validate(
        {
            "draft_id": str(row[0]),
            "tenant_ref": tenant_ref,
            "workspace_id": str(row[1]),
            "source_opportunity_id": str(row[2]),
            "source_anomaly_id": (
                str(row[3]) if row[3] is not None else None
            ),
            "risk_type": str(row[4]),
            "title": str(row[5]),
            "summary": str(row[6]),
            "scope": json.loads(str(row[7])),
            "evidence_refs": json.loads(str(row[8])),
            "proposed_changes": json.loads(str(row[9])),
            "expected_impact": json.loads(str(row[10])),
            "prerequisites": json.loads(str(row[11])),
            "risks_and_guardrails": json.loads(str(row[12])),
            "verification_plan": json.loads(str(row[13])),
            "rollback_plan": json.loads(str(row[14])),
            "action_kind": str(row[15]),
            "execution_capability": str(row[16]),
            "base_version": str(row[17]),
            "status": str(row[18]),
            "revision": int(row[19]),
            "created_by": str(row[20]),
            "reviewed_by": (
                str(row[21]) if row[21] is not None else None
            ),
            "translated_action_id": (
                str(row[22]) if row[22] is not None else None
            ),
            "created_at": _iso(row[23]),
            "updated_at": _iso(row[24]),
        }
    )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return (
            parsed.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(value)

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from .risk_scans import FinOpsRiskScan, RiskScanFinding
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlRiskScanRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save(self, value: FinOpsRiskScan) -> FinOpsRiskScan:
        scope_json = json.dumps(
            value.scope.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-risk-scan */
                MERGE df_finops.risk_scan WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS scan_ref) AS source
                ON target.tenant_ref = source.tenant_ref
                   AND target.scan_ref = source.scan_ref
                WHEN MATCHED THEN UPDATE SET
                    workspace_id = ?, scope_fingerprint = ?, scope_json = ?,
                    scan_status = ?, policy_revision = ?, ledger_revision = ?,
                    rules_evaluated = ?, rules_triggered = ?, rules_clear = ?,
                    rules_insufficient = ?, rules_unavailable = ?,
                    request_sample_count = ?, evidence_bound_findings = ?,
                    evidence_coverage_pct = ?, finished_at = ?,
                    safe_error_category = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, scan_ref, workspace_id, scope_fingerprint,
                    scope_json, scan_status, policy_revision, ledger_revision,
                    rules_evaluated, rules_triggered, rules_clear,
                    rules_insufficient, rules_unavailable,
                    request_sample_count, evidence_bound_findings,
                    evidence_coverage_pct, started_at, finished_at,
                    initiated_by_ref, safe_error_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                value.tenant_ref,
                value.scan_ref,
                value.scope.workspace_id,
                value.scope_fingerprint,
                scope_json,
                value.status,
                value.policy_revision,
                value.ledger_revision,
                value.rules_evaluated,
                value.rules_triggered,
                value.rules_clear,
                value.rules_insufficient,
                value.rules_unavailable,
                value.request_sample_count,
                value.evidence_bound_findings,
                value.evidence_coverage_pct,
                value.finished_at,
                value.safe_error_category,
                value.tenant_ref,
                value.scan_ref,
                value.scope.workspace_id,
                value.scope_fingerprint,
                scope_json,
                value.status,
                value.policy_revision,
                value.ledger_revision,
                value.rules_evaluated,
                value.rules_triggered,
                value.rules_clear,
                value.rules_insufficient,
                value.rules_unavailable,
                value.request_sample_count,
                value.evidence_bound_findings,
                value.evidence_coverage_pct,
                value.started_at,
                value.finished_at,
                value.initiated_by_ref,
                value.safe_error_category,
            )
            cursor.execute(
                """/* finops:replace-risk-scan-findings */
                DELETE FROM df_finops.risk_scan_finding
                WHERE tenant_ref = ? AND scan_ref = ?""",
                value.tenant_ref,
                value.scan_ref,
            )
            for index, finding in enumerate(value.findings):
                cursor.execute(
                    """/* finops:insert-risk-scan-finding */
                    INSERT INTO df_finops.risk_scan_finding (
                        tenant_ref, scan_ref, finding_index, policy_type,
                        rule_status, severity, rule_revision, observed_value,
                        threshold_value, unit, sample_count, minimum_samples,
                        recommendation, reason, evidence_refs_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    value.tenant_ref,
                    value.scan_ref,
                    index,
                    finding.policy_type,
                    finding.status,
                    finding.severity,
                    finding.rule_revision,
                    finding.observed_value,
                    finding.threshold_value,
                    finding.unit,
                    finding.sample_count,
                    finding.minimum_samples,
                    finding.recommendation,
                    finding.reason,
                    json.dumps(finding.evidence_refs, separators=(",", ":")),
                )
        return value.model_copy(deep=True)

    def get(self, tenant_ref: str, scan_ref: str) -> FinOpsRiskScan | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-risk-scan */
                SELECT scan_ref, tenant_ref, workspace_id, scope_fingerprint,
                    scope_json, scan_status, policy_revision, ledger_revision,
                    rules_evaluated, rules_triggered, rules_clear,
                    rules_insufficient, rules_unavailable,
                    request_sample_count, evidence_bound_findings,
                    evidence_coverage_pct, started_at, finished_at,
                    initiated_by_ref, safe_error_category
                FROM df_finops.risk_scan
                WHERE tenant_ref = ? AND scan_ref = ?""",
                tenant_ref,
                scan_ref,
            ).fetchone()
            if row is None:
                return None
            finding_rows = cursor.execute(
                """/* finops:list-risk-scan-findings */
                SELECT policy_type, rule_status, severity, rule_revision,
                    observed_value, threshold_value, unit, sample_count,
                    minimum_samples, recommendation, reason, evidence_refs_json
                FROM df_finops.risk_scan_finding
                WHERE tenant_ref = ? AND scan_ref = ?
                ORDER BY finding_index ASC""",
                tenant_ref,
                scan_ref,
            ).fetchall()
        return _decode_scan(row, finding_rows)

    def latest(
        self,
        tenant_ref: str,
        workspace_id: str,
        scope_fingerprint: str,
    ) -> FinOpsRiskScan | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:latest-risk-scan-ref */
                SELECT TOP (1) scan_ref
                FROM df_finops.risk_scan
                WHERE tenant_ref = ? AND workspace_id = ?
                  AND scope_fingerprint = ?
                ORDER BY started_at DESC, scan_ref DESC""",
                tenant_ref,
                workspace_id,
                scope_fingerprint,
            ).fetchone()
        if row is None:
            return None
        return self.get(tenant_ref, str(row[0]))

    def list(
        self,
        tenant_ref: str,
        workspace_id: str,
        limit: int,
    ) -> list[FinOpsRiskScan]:
        bounded_limit = max(1, min(int(limit), 50))
        with self._transaction() as cursor:
            rows = cursor.execute(
                f"""/* finops:list-risk-scan-refs */
                SELECT TOP ({bounded_limit}) scan_ref
                FROM df_finops.risk_scan
                WHERE tenant_ref = ? AND workspace_id = ?
                ORDER BY started_at DESC, scan_ref DESC""",
                tenant_ref,
                workspace_id,
            ).fetchall()
        return [
            scan
            for row in rows
            if (scan := self.get(tenant_ref, str(row[0]))) is not None
        ]

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
            raise FinOpsPersistenceError("FinOps risk scan SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _decode_scan(row: Any, finding_rows: list[Any]) -> FinOpsRiskScan:
    findings = [
        RiskScanFinding(
            policy_type=item[0],
            status=item[1],
            severity=item[2],
            rule_revision=item[3],
            observed_value=float(item[4]) if item[4] is not None else None,
            threshold_value=float(item[5]) if item[5] is not None else None,
            unit=item[6],
            sample_count=int(item[7]),
            minimum_samples=int(item[8]),
            recommendation=item[9],
            reason=item[10],
            evidence_refs=json.loads(str(item[11])),
        )
        for item in finding_rows
    ]
    return FinOpsRiskScan.model_validate(
        {
            "scan_ref": row[0],
            "tenant_ref": row[1],
            "scope": json.loads(str(row[4])),
            "scope_fingerprint": row[3],
            "status": row[5],
            "policy_revision": row[6],
            "ledger_revision": row[7],
            "rules_evaluated": row[8],
            "rules_triggered": row[9],
            "rules_clear": row[10],
            "rules_insufficient": row[11],
            "rules_unavailable": row[12],
            "request_sample_count": row[13],
            "evidence_bound_findings": row[14],
            "evidence_coverage_pct": float(row[15]),
            "started_at": _db_time(row[16]),
            "finished_at": _db_time(row[17]) if row[17] is not None else None,
            "initiated_by_ref": row[18],
            "safe_error_category": row[19],
            "findings": findings,
        }
    )


def _db_time(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)

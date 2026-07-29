from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .member_budget_repository import MemberBudgetConflictError
from .member_budgets import BudgetAlert, MemberBudget, NotificationSetting
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlMemberBudgetRepository:
    """Azure SQL source of truth for tenant-isolated member budget facts.

    Delivery payloads, ACS message IDs, raw Entra object IDs, and recipients are
    intentionally absent from alert rows. Notification configuration is the only
    place that holds its administrator recipient and template.
    """

    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def get_budget(self, tenant_ref: str, budget_id: str) -> MemberBudget | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-member-budget */
                SELECT budget_id, actor_ref, period_type, amount_usd, thresholds_json, enabled,
                       revision, created_by_ref, updated_by_ref, created_at, updated_at
                FROM df_finops.member_budget
                WHERE tenant_ref = ? AND budget_id = ?""",
                tenant_ref,
                budget_id,
            ).fetchone()
        return _budget_from_row(row) if row is not None else None

    def list_budgets(self, tenant_ref: str, *, include_disabled: bool = False) -> tuple[MemberBudget, ...]:
        where_enabled = "" if include_disabled else " AND enabled = 1"
        with self._transaction() as cursor:
            rows = cursor.execute(
                f"""/* finops:list-member-budgets */
                SELECT budget_id, actor_ref, period_type, amount_usd, thresholds_json, enabled,
                       revision, created_by_ref, updated_by_ref, created_at, updated_at
                FROM df_finops.member_budget
                WHERE tenant_ref = ?{where_enabled}
                ORDER BY actor_ref, created_at, budget_id""",
                tenant_ref,
            ).fetchall()
        return tuple(_budget_from_row(row) for row in rows)

    def save_budget(self, tenant_ref: str, value: MemberBudget, *, base_revision: int) -> MemberBudget:
        thresholds_json = json.dumps(value.thresholds_pct, separators=(",", ":"))
        with self._transaction() as cursor:
            current = cursor.execute(
                """/* finops:lock-member-budget-revision */
                SELECT revision FROM df_finops.member_budget WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ? AND budget_id = ?""",
                tenant_ref,
                value.budget_id,
            ).fetchone()
            current_revision = int(_value(current, 0)) if current is not None else 0
            if current_revision != base_revision or value.revision != base_revision + 1:
                raise MemberBudgetConflictError("member budget revision conflict")
            if value.enabled:
                active = cursor.execute(
                    """/* finops:lock-active-member-budget */
                    SELECT TOP 1 budget_id
                    FROM df_finops.member_budget WITH (UPDLOCK, HOLDLOCK)
                    WHERE tenant_ref = ? AND actor_ref = ? AND enabled = 1 AND budget_id <> ?""",
                    tenant_ref,
                    value.member_ref,
                    value.budget_id,
                ).fetchone()
                if active is not None:
                    raise MemberBudgetConflictError("active member budget already exists")
            cursor.execute(
                """/* finops:save-member-budget */
                MERGE df_finops.member_budget WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS budget_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.budget_id = source.budget_id
                WHEN MATCHED THEN UPDATE SET
                    actor_ref = ?, period_type = ?, amount_usd = ?, thresholds_json = ?, enabled = ?,
                    revision = ?, updated_by_ref = ?, updated_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, budget_id, actor_ref, period_type, amount_usd, thresholds_json, enabled,
                    revision, created_by_ref, updated_by_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref,
                value.budget_id,
                value.member_ref,
                value.period_type,
                value.amount_usd,
                thresholds_json,
                value.enabled,
                value.revision,
                value.updated_by_ref,
                value.updated_at,
                tenant_ref,
                value.budget_id,
                value.member_ref,
                value.period_type,
                value.amount_usd,
                thresholds_json,
                value.enabled,
                value.revision,
                value.created_by_ref,
                value.updated_by_ref,
                value.created_at,
                value.updated_at,
            )
        return value

    def disable_budget(
        self, tenant_ref: str, budget_id: str, *, base_revision: int, updated_by_ref: str
    ) -> MemberBudget:
        current = self.get_budget(tenant_ref, budget_id)
        if current is None:
            raise MemberBudgetConflictError("member budget revision conflict")
        return self.save_budget(
            tenant_ref,
            current.model_copy(
                update={
                    "enabled": False,
                    "revision": base_revision + 1,
                    "updated_by_ref": updated_by_ref,
                    "updated_at": datetime.now(timezone.utc),
                }
            ),
            base_revision=base_revision,
        )

    def get_notification_setting(self, tenant_ref: str) -> NotificationSetting | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-notification-setting */
                SELECT recipient_actor_ref, recipient_email, sender_display_name,
                       subject_template, body_template, enabled, revision,
                       created_by_ref, updated_by_ref, created_at, updated_at
                FROM df_finops.notification_setting WHERE tenant_ref = ?""",
                tenant_ref,
            ).fetchone()
        return _notification_from_row(row) if row is not None else None

    def save_notification_setting(
        self, tenant_ref: str, value: NotificationSetting, *, base_revision: int
    ) -> NotificationSetting:
        with self._transaction() as cursor:
            current = cursor.execute(
                """/* finops:lock-notification-setting-revision */
                SELECT revision FROM df_finops.notification_setting WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ?""",
                tenant_ref,
            ).fetchone()
            current_revision = int(_value(current, 0)) if current is not None else 0
            if current_revision != base_revision or value.revision != base_revision + 1:
                raise MemberBudgetConflictError("notification setting revision conflict")
            cursor.execute(
                """/* finops:save-notification-setting */
                MERGE df_finops.notification_setting WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref) AS source
                ON target.tenant_ref = source.tenant_ref
                WHEN MATCHED THEN UPDATE SET
                    recipient_actor_ref = ?, recipient_email = ?, sender_display_name = ?,
                    subject_template = ?, body_template = ?, enabled = ?, revision = ?,
                    updated_by_ref = ?, updated_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, recipient_actor_ref, recipient_email, sender_display_name,
                    subject_template, body_template, enabled, revision, created_by_ref,
                    updated_by_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref,
                value.recipient_actor_ref,
                value.recipient_email,
                value.sender_display_name,
                value.subject_template,
                value.body_template,
                value.enabled,
                value.revision,
                value.updated_by_ref,
                value.updated_at,
                tenant_ref,
                value.recipient_actor_ref,
                value.recipient_email,
                value.sender_display_name,
                value.subject_template,
                value.body_template,
                value.enabled,
                value.revision,
                value.created_by_ref,
                value.updated_by_ref,
                value.created_at,
                value.updated_at,
            )
        return value

    def claim_alert(self, value: BudgetAlert) -> bool:
        """Insert the durable threshold claim; a unique-key collision means claimed."""
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    """/* finops:claim-budget-alert */
                    INSERT INTO df_finops.budget_alert (
                        tenant_ref, alert_id, budget_id, actor_ref, period_key, threshold_pct,
                        budget_amount_usd, estimated_spend_usd, pricing_coverage_pct,
                        budget_revision, notification_revision, delivery_state,
                        safe_error_category, attempt_count, triggered_at, sent_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    value.tenant_ref,
                    value.alert_id,
                    value.budget_id,
                    value.actor_ref,
                    value.period_key,
                    value.threshold_pct,
                    value.budget_amount_usd,
                    value.estimated_spend_usd,
                    value.pricing_coverage_pct,
                    value.budget_revision,
                    value.notification_revision,
                    value.delivery_state,
                    value.safe_error_category,
                    value.attempt_count,
                    value.triggered_at,
                    value.sent_at,
                    value.updated_at,
                )
            return True
        except FinOpsPersistenceError as exc:
            cause = exc.__cause__
            text = str(cause or "").lower()
            if "2627" in text or "2601" in text or "unique" in text or "duplicate" in text:
                return False
            raise

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection: Any | None = None
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
            if isinstance(exc, FinOpsPersistenceError):
                raise
            raise FinOpsPersistenceError("Member budget SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _value(row: Any, index: int) -> Any:
    if isinstance(row, (tuple, list)):
        return row[index]
    try:
        return row[index]
    except (KeyError, TypeError):
        return tuple(row)[index]


def _budget_from_row(row: Any) -> MemberBudget:
    thresholds = _value(row, 4)
    if isinstance(thresholds, bytes):
        thresholds = thresholds.decode("utf-8")
    return MemberBudget(
        budget_id=_value(row, 0),
        member_ref=_value(row, 1),
        period_type=_value(row, 2),
        amount_usd=_value(row, 3),
        thresholds_pct=json.loads(str(thresholds)),
        enabled=bool(_value(row, 5)),
        revision=_value(row, 6),
        created_by_ref=_value(row, 7),
        updated_by_ref=_value(row, 8),
        created_at=_value(row, 9),
        updated_at=_value(row, 10),
    )


def _notification_from_row(row: Any) -> NotificationSetting:
    return NotificationSetting(
        recipient_actor_ref=_value(row, 0),
        recipient_email=_value(row, 1),
        sender_display_name=_value(row, 2),
        subject_template=_value(row, 3),
        body_template=_value(row, 4),
        enabled=bool(_value(row, 5)),
        revision=_value(row, 6),
        created_by_ref=_value(row, 7),
        updated_by_ref=_value(row, 8),
        created_at=_value(row, 9),
        updated_at=_value(row, 10),
    )


__all__ = ["SqlMemberBudgetRepository"]

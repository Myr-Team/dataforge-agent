from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .member_budget_repository import MemberBudgetConflictError
from .member_budgets import BudgetAlert, MemberBudget, MemberCostSummary, NotificationSetting
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

    def summarize_member_costs(
        self, *, tenant_ref: str, from_value: str, to_value: str, workspace_ids: tuple[str, ...]
    ) -> dict[str, MemberCostSummary]:
        """Aggregate reconciled request facts for an inclusive/exclusive UTC window."""
        authorized_workspaces = _bounded_workspace_ids(workspace_ids)
        if not authorized_workspaces:
            return {}
        placeholders = ", ".join("?" for _ in authorized_workspaces)
        parameters = (tenant_ref, *authorized_workspaces, from_value, to_value)
        with self._transaction() as cursor:
            amount_rows = cursor.execute(
                f"""/* finops:summarize-member-costs */
                SELECT actor_ref,
                       SUM(CASE WHEN cost_amount IS NOT NULL THEN cost_amount END) AS estimated_spend,
                       SUM(CASE WHEN cost_amount IS NOT NULL THEN 1 ELSE 0 END) AS priced_requests,
                       COUNT_BIG(*) AS total_requests
                FROM df_finops.request_event
                WHERE tenant_ref = ?
                  AND workspace_id IN ({placeholders})
                  AND actor_ref IS NOT NULL
                  AND occurred_at >= ?
                  AND occurred_at < ?
                GROUP BY actor_ref""",
                *parameters,
            ).fetchall()
            model_rows = cursor.execute(
                f"""/* finops:summarize-member-primary-model */
                WITH model_counts AS (
                    SELECT actor_ref, model_deployment, COUNT_BIG(*) AS request_count
                    FROM df_finops.request_event
                    WHERE tenant_ref = ?
                      AND workspace_id IN ({placeholders})
                      AND actor_ref IS NOT NULL
                      AND occurred_at >= ?
                      AND occurred_at < ?
                      AND model_deployment IS NOT NULL
                    GROUP BY actor_ref, model_deployment
                ), ranked_models AS (
                    SELECT actor_ref, model_deployment,
                           ROW_NUMBER() OVER (PARTITION BY actor_ref ORDER BY request_count DESC, model_deployment) AS model_rank
                    FROM model_counts
                )
                SELECT actor_ref, model_deployment
                FROM ranked_models WHERE model_rank = 1""",
                *parameters,
            ).fetchall()
        primary_models = {str(_value(row, 0)): _value(row, 1) for row in model_rows}
        return {
            str(_value(row, 0)): MemberCostSummary(
                actor_ref=str(_value(row, 0)),
                estimated_spend_usd=_value(row, 1),
                priced_requests=int(_value(row, 2) or 0),
                total_requests=int(_value(row, 3) or 0),
                primary_model=primary_models.get(str(_value(row, 0))),
            )
            for row in amount_rows
        }

    def save_budget(self, tenant_ref: str, value: MemberBudget, *, base_revision: int) -> MemberBudget:
        thresholds_json = json.dumps(value.thresholds_pct, separators=(",", ":"))
        try:
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
        except FinOpsPersistenceError as exc:
            if _is_unique_violation(exc):
                raise MemberBudgetConflictError("member budget revision conflict") from None
            raise
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
        try:
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
        except FinOpsPersistenceError as exc:
            if _is_unique_violation(exc):
                raise MemberBudgetConflictError("notification setting revision conflict") from None
            raise
        return value

    def list_alerts(self, tenant_ref: str, *, budget_id: str | None = None, offset: int = 0, limit: int = 100) -> tuple[BudgetAlert, ...]:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 101:
            raise ValueError("invalid budget alert page")
        budget_filter = "" if budget_id is None else " AND budget_id = ?"
        parameters: tuple[Any, ...] = (tenant_ref, offset, limit) if budget_id is None else (tenant_ref, budget_id, offset, limit)
        with self._transaction() as cursor:
            rows = cursor.execute(
                f"""/* finops:list-budget-alerts */
                SELECT alert_id, budget_id, actor_ref, period_key, threshold_pct,
                       budget_amount_usd, estimated_spend_usd, pricing_coverage_pct,
                       budget_revision, notification_revision, delivery_state,
                       safe_error_category, attempt_count, triggered_at, sent_at, updated_at
                FROM df_finops.budget_alert
                WHERE tenant_ref = ?{budget_filter}
                ORDER BY triggered_at, alert_id
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
                *parameters,
            ).fetchall()
        return tuple(_alert_from_row(tenant_ref, row) for row in rows)

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
            if not _is_unique_violation(exc):
                raise
            if self._alert_threshold_exists(value):
                return False
            raise MemberBudgetConflictError("budget alert id conflict") from None

    def _alert_threshold_exists(self, value: BudgetAlert) -> bool:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-budget-alert-threshold */
                SELECT TOP 1 alert_id FROM df_finops.budget_alert
                WHERE tenant_ref = ? AND budget_id = ? AND period_key = ? AND threshold_pct = ?""",
                value.tenant_ref,
                value.budget_id,
                value.period_key,
                value.threshold_pct,
            ).fetchone()
        return row is not None

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


def _bounded_workspace_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))
    if len(unique) > 100:
        raise ValueError("authorized workspace scope exceeds limit")
    return unique


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


def _alert_from_row(tenant_ref: str, row: Any) -> BudgetAlert:
    return BudgetAlert(
        alert_id=_value(row, 0),
        tenant_ref=tenant_ref,
        budget_id=_value(row, 1),
        actor_ref=_value(row, 2),
        period_key=_value(row, 3),
        threshold_pct=_value(row, 4),
        budget_amount_usd=_value(row, 5),
        estimated_spend_usd=_value(row, 6),
        pricing_coverage_pct=_value(row, 7),
        budget_revision=_value(row, 8),
        notification_revision=_value(row, 9),
        delivery_state=_value(row, 10),
        safe_error_category=_value(row, 11),
        attempt_count=_value(row, 12),
        triggered_at=_value(row, 13),
        sent_at=_value(row, 14),
        updated_at=_value(row, 15),
    )


def _is_unique_violation(exc: BaseException) -> bool:
    """Recognize only native SQL Server duplicate-key diagnostics.

    pyodbc can nest the SQLSTATE/native-code message in ``args`` or an exception
    cause/context chain, so inspect those values without treating broad prose
    such as "duplicate" as a persistence conflict.
    """
    values: list[object] = [exc]
    seen: set[int] = set()
    while values:
        current = values.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseException):
            values.extend((current.__cause__, current.__context__, *current.args))
            continue
        if isinstance(current, (tuple, list)):
            values.extend(current)
            continue
        if re.search(r"(?<!\d)(?:2601|2627)(?!\d)", str(current)):
            return True
    return False


__all__ = ["SqlMemberBudgetRepository"]

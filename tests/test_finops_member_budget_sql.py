from __future__ import annotations

from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "backend" / "sql" / "finops_schema.sql"


def test_member_budget_schema_is_tenant_scoped_replay_safe_and_deduplicated() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for table in ("member_budget", "notification_setting", "budget_alert"):
        assert f"if object_id(n'df_finops.{table}', n'u') is null" in schema
        start = schema.index(f"create table df_finops.{table}")
        assert "tenant_ref" in schema[start : start + 1800]

    assert "uq_finops_member_budget_active" in schema
    assert "on df_finops.member_budget (tenant_ref, actor_ref)" in schema
    assert "where enabled = 1" in schema
    assert "uq_finops_budget_alert_threshold unique" in schema
    assert "tenant_ref, budget_id, period_key, threshold_pct" in schema


def test_member_budget_sql_limits_delivery_state_and_does_not_persist_delivery_secrets() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    alert_start = schema.index("create table df_finops.budget_alert")
    alert = schema[alert_start : alert_start + 2400]

    for state in ("pending", "sending", "sent", "failed", "suppressed"):
        assert f"n'{state}'" in alert
    assert "ck_finops_budget_alert_state" in alert
    assert "acs_message" not in alert
    assert "email_body" not in alert
    assert "entra_object" not in alert
    assert "credential" not in alert


def test_member_budget_sql_uses_json_thresholds_and_request_actor_window_index() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    assert "thresholds_json nvarchar(256) not null" in schema
    assert "isjson(thresholds_json) = 1" in schema
    assert "ix_finops_request_actor_window" in schema
    assert "on df_finops.request_event (tenant_ref, actor_ref, occurred_at)" in schema
    assert "include (cost_amount, evidence_state)" in schema


def test_member_budget_sql_rejects_non_calendar_month_period_keys() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    assert "ck_finops_budget_alert_period" in schema
    assert "period_key like '[0-9][0-9][0-9][0-9]-[0-1][0-9]'" in schema
    assert "substring(period_key, 6, 2) between '01' and '12'" in schema


def test_member_budget_period_constraint_is_added_for_existing_alert_tables() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    table_end = schema.index("end;", schema.index("create table df_finops.budget_alert"))
    guarded_upgrade = schema[table_end + len("end;") :]

    assert "if not exists" in guarded_upgrade
    assert "from sys.check_constraints" in guarded_upgrade
    assert "ck_finops_budget_alert_period" in guarded_upgrade
    assert "alter table df_finops.budget_alert" in guarded_upgrade
    assert "add constraint ck_finops_budget_alert_period" in guarded_upgrade


def test_member_budget_actor_cost_query_is_tenant_scoped_and_uses_reconciled_request_rows() -> None:
    source = (SCHEMA_PATH.parents[2] / "backend" / "finops" / "sql_member_budgets.py").read_text(encoding="utf-8").lower()

    assert "finops:summarize-member-costs" in source
    assert "from df_finops.request_event" in source
    assert "where tenant_ref = ?" in source
    assert "actor_ref is not null" in source
    assert "occurred_at >= ?" in source
    assert "occurred_at < ?" in source
    assert "row_number() over (partition by actor_ref" in source


def test_budget_alert_schema_adds_replay_safe_exclusive_lease_and_due_index() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    for column in ("lease_token", "lease_expires_at", "next_attempt_at"):
        assert f"col_length(n'df_finops.budget_alert', n'{column}')" in schema
        assert f"alter table df_finops.budget_alert add {column}" in schema
    assert "ix_finops_budget_alert_due" in schema
    assert "ck_finops_budget_alert_lease" in schema
    assert "from sys.check_constraints" in schema


def test_sql_alert_acquire_is_atomic_due_only_token_owned_and_keyset_ordered() -> None:
    source = (
        SCHEMA_PATH.parents[2] / "backend" / "finops" / "sql_member_budgets.py"
    ).read_text(encoding="utf-8").lower()

    assert "finops:acquire-due-budget-alert" in source
    assert "with (updlock, readpast, rowlock)" in source
    assert "top (1)" in source
    assert "attempt_count < 3" in source
    assert "next_attempt_at <= ?" in source
    assert "lease_expires_at <= ?" in source
    assert "order by" in source
    assert "output inserted.alert_id" in source
    assert "lease_token = ?" in source
    assert "finops:finalize-budget-alert" in source

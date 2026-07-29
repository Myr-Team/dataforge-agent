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

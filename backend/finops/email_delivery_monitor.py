"""Recipient-level ACS delivery confirmation with bounded, redacted output."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class EmailDeliveryEvidence(BaseModel):
    state: Literal["accepted", "delivered", "bounced", "failed", "pending", "unavailable"]
    observed_at: datetime | None = None
    safe_error_category: str | None = None


class EmailDeliveryMonitor:
    def __init__(self, *, client: Any, logs_workspace_id: str) -> None:
        try:
            self._logs_workspace_id = str(UUID(logs_workspace_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid logs workspace id") from exc
        self._client = client

    def lookup(self, provider_message_id: str) -> EmailDeliveryEvidence:
        try:
            message_id = str(UUID(provider_message_id))
        except (ValueError, TypeError, AttributeError):
            return EmailDeliveryEvidence(state="unavailable", safe_error_category="service_unavailable")
        query = f"""
ACSEmailStatusUpdateOperational
| where CorrelationId == '{message_id}'
| where isnotempty(RecipientId)
| project TimeGenerated, DeliveryStatus
| top 1 by TimeGenerated desc
""".strip()
        try:
            result = self._client.query_workspace(
                self._logs_workspace_id,
                query,
                timespan=timedelta(days=7),
                server_timeout=10,
            )
            row, indexes = _latest_row(result)
        except Exception:
            return EmailDeliveryEvidence(state="unavailable", safe_error_category="service_unavailable")
        if row is None:
            return EmailDeliveryEvidence(state="pending")
        provider_state = str(row[indexes["deliverystatus"]] or "").strip().lower()
        observed_at = _utc_datetime(row[indexes["timegenerated"]])
        if provider_state == "delivered":
            state = "delivered"
        elif provider_state == "bounced":
            state = "bounced"
        elif provider_state in {"failed", "quarantined", "filteredspam", "suppressed"}:
            state = "failed"
        else:
            state = "pending"
        return EmailDeliveryEvidence(state=state, observed_at=observed_at)


def email_delivery_monitor_from_environment() -> EmailDeliveryMonitor | None:
    workspace_id = str(os.environ.get("DF_AZURE_MONITOR_LOGS_WORKSPACE_ID") or "").strip()
    if not workspace_id:
        return None
    try:
        from azure.identity import ManagedIdentityCredential
        from azure.monitor.query import LogsQueryClient

        return EmailDeliveryMonitor(
            client=LogsQueryClient(ManagedIdentityCredential()),
            logs_workspace_id=workspace_id,
        )
    except Exception:
        return None


def _latest_row(result: Any) -> tuple[Any | None, dict[str, int]]:
    tables = getattr(result, "tables", None)
    if not tables:
        return None, {}
    table = tables[0]
    columns = getattr(table, "columns", ())
    indexes = {str(getattr(column, "name", "")).strip().lower(): index for index, column in enumerate(columns)}
    if not {"timegenerated", "deliverystatus"}.issubset(indexes):
        raise ValueError("delivery columns unavailable")
    rows = getattr(table, "rows", None)
    return (rows[0] if rows else None), indexes


def _utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


__all__ = ["EmailDeliveryEvidence", "EmailDeliveryMonitor", "email_delivery_monitor_from_environment"]

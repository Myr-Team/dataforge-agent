from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.finops.email_delivery_monitor import EmailDeliveryMonitor


class _Client:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows
        self.query = ""

    def query_workspace(self, _workspace_id: str, query: str, **_kwargs: object):
        self.query = query
        columns = [SimpleNamespace(name="TimeGenerated"), SimpleNamespace(name="DeliveryStatus")]
        return SimpleNamespace(tables=[SimpleNamespace(columns=columns, rows=self.rows)])


def test_monitor_confirms_recipient_delivery_without_exposing_provider_id() -> None:
    client = _Client([[datetime.fromisoformat("2026-08-10T01:00:00+00:00"), "Delivered"]])
    monitor = EmailDeliveryMonitor(client=client, logs_workspace_id="11111111-1111-5111-8111-111111111111")

    evidence = monitor.lookup("22222222-2222-5222-8222-222222222222")

    assert evidence.state == "delivered"
    assert set(evidence.model_dump(mode="json")) == {"state", "observed_at", "safe_error_category"}
    assert "22222222-2222-5222-8222-222222222222" in client.query
    assert "RecipientId" in client.query


def test_monitor_accepts_string_columns_returned_by_current_azure_sdk() -> None:
    class _StringColumnClient(_Client):
        def query_workspace(self, _workspace_id: str, query: str, **_kwargs: object):
            self.query = query
            return SimpleNamespace(
                tables=[
                    SimpleNamespace(
                        columns=["TimeGenerated", "DeliveryStatus"],
                        rows=[
                            [
                                datetime.fromisoformat("2026-08-10T01:00:00+00:00"),
                                "Delivered",
                            ]
                        ],
                    )
                ]
            )

    evidence = EmailDeliveryMonitor(
        client=_StringColumnClient([]),
        logs_workspace_id="11111111-1111-5111-8111-111111111111",
    ).lookup("22222222-2222-5222-8222-222222222222")

    assert evidence.state == "delivered"


@pytest.mark.parametrize(
    ("provider_state", "expected"),
    [
        ("Bounced", "bounced"),
        ("Failed", "failed"),
        ("Quarantined", "failed"),
        ("FilteredSpam", "failed"),
        ("Suppressed", "failed"),
    ],
)
def test_monitor_maps_terminal_delivery_states(provider_state: str, expected: str) -> None:
    client = _Client([[datetime.fromisoformat("2026-08-10T01:00:00+00:00"), provider_state]])
    evidence = EmailDeliveryMonitor(
        client=client,
        logs_workspace_id="11111111-1111-5111-8111-111111111111",
    ).lookup("22222222-2222-5222-8222-222222222222")
    assert evidence.state == expected


def test_monitor_rejects_invalid_message_id_and_redacts_query_failure() -> None:
    class _FailingClient:
        def query_workspace(self, *_args: object, **_kwargs: object):
            raise RuntimeError("provider body must not escape")

    monitor = EmailDeliveryMonitor(
        client=_FailingClient(),
        logs_workspace_id="11111111-1111-5111-8111-111111111111",
    )
    assert monitor.lookup("not-an-id").model_dump(mode="json") == {
        "state": "unavailable",
        "observed_at": None,
        "safe_error_category": "service_unavailable",
    }
    assert monitor.lookup("22222222-2222-5222-8222-222222222222").safe_error_category == "service_unavailable"

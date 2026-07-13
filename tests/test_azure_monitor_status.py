from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util

import pytest

import backend.azure_monitor_client as monitor
import backend.control_plane as control_plane
import backend.tracing as tracing
from fastapi import HTTPException


def test_azure_monitor_delivery_adapter_is_declared() -> None:
    assert importlib.util.find_spec("backend.azure_monitor_client") is not None


def _build_status(**kwargs):
    build = getattr(monitor, "build_trace_status", None)
    assert callable(build)
    return build(**kwargs)


def test_configured_exporter_without_observed_trace_is_partial() -> None:
    status = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_trace=None,
        correlation_id="a" * 32,
        exporter_state="unknown",
    )

    assert status.state == "partial"
    assert status.last_export_confirmed_at is None
    assert status.exporter_state == "unknown"


def test_remote_trace_proves_connected_without_exposing_actor_or_raw_result() -> None:
    actor_email = "person@example.com"
    remote_result = {"raw": f"token=private {actor_email}", "observed_at": datetime(2026, 7, 13, tzinfo=timezone.utc)}
    status = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_trace=remote_result,
        correlation_id="b" * 32,
        exporter_state="succeeded",
    )

    assert status.state == "connected"
    payload = status.model_dump_json()
    assert actor_email not in payload
    assert "private" not in payload
    assert "raw" not in payload


def test_not_configured_and_unavailable_are_distinct_from_partial() -> None:
    not_configured = _build_status(configured=False, local_emit_at=None, remote_trace=None, correlation_id=None, exporter_state="unknown")
    unavailable = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_trace=None,
        correlation_id="c" * 32,
        exporter_state="unknown",
        query_error_type="ClientAuthenticationError",
    )

    assert not_configured.state == "not_configured"
    assert unavailable.state == "unavailable"


class _Column:
    def __init__(self, name: str) -> None:
        self.name = name


class _QueryResult:
    def __init__(self, row: list[object]) -> None:
        self.tables = [
            type(
                "Table",
                (),
                {
                    "columns": [
                        _Column("timestamp"),
                        _Column("operation_Id"),
                        _Column("appId"),
                        _Column("_ResourceId"),
                        _Column("run_hash"),
                        _Column("correlation_hash"),
                    ],
                    "rows": [row],
                },
            )()
        ]


class _LogsClient:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def query_workspace(self, *args: object, **kwargs: object) -> object:
        self.calls.append((*args, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _monitor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=not-a-secret")
    monkeypatch.setenv("DF_AZURE_MONITOR_LOGS_WORKSPACE_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("DF_APP_INSIGHTS_APPLICATION_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv(
        "DF_APP_INSIGHTS_RESOURCE_ID",
        "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg/providers/microsoft.insights/components/dataforge",
    )


def test_query_is_bounded_and_matches_only_hashed_workspace_run_and_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    workspace_id = "workspace-private"
    run_id = "run-private"
    correlation_id = "d" * 32
    workspace_hash = monitor.hash_trace_identifier(workspace_id)
    run_hash = monitor.hash_trace_identifier(run_id)
    correlation_hash = monitor.hash_trace_identifier(correlation_id)
    client = _LogsClient(
        _QueryResult(
            [
                "2026-07-13T00:00:00Z",
                correlation_id,
                "22222222-2222-2222-2222-222222222222",
                "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg/providers/microsoft.insights/components/dataforge",
                run_hash,
                correlation_hash,
            ]
        )
    )

    remote = monitor.query_trace_delivery(workspace_id, run_id, correlation_id, client=client)

    assert remote is not None
    assert remote["correlation_id"] == correlation_id
    args = client.calls[0]
    query = args[1]
    assert isinstance(query, str)
    assert workspace_id not in query
    assert run_id not in query
    assert correlation_id not in query
    assert workspace_hash in query
    assert run_hash in query
    assert correlation_hash in query
    assert "take 1" in query
    assert args[2]["timespan"].total_seconds() <= 15 * 60
    assert args[2]["server_timeout"] <= 10


def test_success_confirmation_cache_is_scoped_and_does_not_cache_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    reset = getattr(monitor, "clear_trace_delivery_cache", None)
    assert callable(reset)
    reset()
    calls: list[tuple[str, str, str]] = []

    def confirmed(workspace_id: str, run_id: str, correlation_id: str, **_kwargs: object) -> dict[str, object]:
        calls.append((workspace_id, run_id, correlation_id))
        return {"observed_at": datetime(2026, 7, 13, tzinfo=timezone.utc)}

    monkeypatch.setattr(monitor, "query_trace_delivery", confirmed)
    first = monitor.get_trace_delivery_status("ws-a", "run-a", "e" * 32)
    second = monitor.get_trace_delivery_status("ws-a", "run-a", "e" * 32)
    other_workspace = monitor.get_trace_delivery_status("ws-b", "run-a", "e" * 32)

    assert first.state == second.state == other_workspace.state == "connected"
    assert calls == [("ws-a", "run-a", "e" * 32), ("ws-b", "run-a", "e" * 32)]

    reset()
    partial_calls: list[tuple[str, str, str]] = []

    def not_confirmed(workspace_id: str, run_id: str, correlation_id: str, **_kwargs: object) -> None:
        partial_calls.append((workspace_id, run_id, correlation_id))
        return None

    monkeypatch.setattr(monitor, "query_trace_delivery", not_confirmed)
    monitor.get_trace_delivery_status("ws-a", "run-a", "f" * 32)
    monitor.get_trace_delivery_status("ws-a", "run-a", "f" * 32)
    assert partial_calls == [("ws-a", "run-a", "f" * 32), ("ws-a", "run-a", "f" * 32)]


def test_query_exception_is_reported_by_type_without_secret_or_actor_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _monitor_env(monkeypatch)
    client = _LogsClient(RuntimeError("token=private actor@example.com object-id-123"))

    with pytest.raises(monitor.TraceDeliveryQueryError) as error:
        monitor.query_trace_delivery("ws-a", "run-a", "a" * 32, client=client)

    assert error.value.error_type == "RuntimeError"
    assert "private" not in str(error.value)
    assert "actor@example.com" not in caplog.text
    assert "object-id-123" not in caplog.text


def test_transaction_link_requires_verified_resource_application_and_correlation() -> None:
    build_link = getattr(monitor, "build_transaction_link", None)
    assert callable(build_link)
    resource_id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg/providers/microsoft.insights/components/dataforge"
    safe_link = build_link(resource_id, "22222222-2222-2222-2222-222222222222", "a" * 32)

    assert safe_link is not None
    assert "portal.azure.com" in safe_link
    assert build_link(resource_id + "?next=https://evil.invalid", "22222222-2222-2222-2222-222222222222", "a" * 32) is None
    assert build_link(resource_id, "bad/app", "a" * 32) is None
    assert build_link(resource_id, "22222222-2222-2222-2222-222222222222", "a" * 31 + "/") is None


def test_trace_status_endpoint_requires_workspace_read_and_verifies_run_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_actions: list[tuple[str, str]] = []
    monkeypatch.setattr(
        control_plane,
        "require_workspace_permission",
        lambda workspace_id, _actor, action: requested_actions.append((workspace_id, action)) or "viewer",
    )
    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: {"workspace_id": "ws-a"})
    monkeypatch.setattr(
        control_plane,
        "get_trace_delivery_status",
        lambda workspace_id, run_id, correlation_id=None: _build_status(
            configured=False,
            local_emit_at=None,
            remote_trace=None,
            correlation_id=correlation_id,
            exporter_state="unknown",
        ),
    )

    payload = asyncio.run(control_plane.workspace_trace_status("ws-a", None, run_id="run-a", correlation_id="a" * 32))

    assert payload["state"] == "not_configured"
    assert requested_actions == [("ws-a", "workspace.read")]

    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: {"workspace_id": "ws-other"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(control_plane.workspace_trace_status("ws-a", None, run_id="run-a"))
    assert error.value.status_code == 404

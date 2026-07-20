from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import os

import pytest

import backend.azure_monitor_client as monitor
import backend.control_plane as control_plane
import backend.tracing as tracing
from fastapi import HTTPException


def test_azure_monitor_delivery_adapter_is_declared() -> None:
    assert importlib.util.find_spec("backend.azure_monitor_client") is not None


_RESOURCE_ID = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg/providers/microsoft.insights/components/dataforge"
_APPLICATION_ID = "22222222-2222-2222-2222-222222222222"


def _build_status(**kwargs):
    build = getattr(monitor, "build_trace_status", None)
    assert callable(build)
    return build(**kwargs)


def _expectation(workspace_id: str = "workspace-private", run_id: str = "run-private", correlation_id: str = "b" * 32):
    expectation = getattr(monitor, "TraceDeliveryExpectation", None)
    assert callable(expectation)
    return expectation(
        workspace_hash=monitor.hash_trace_identifier(workspace_id),
        run_hash=monitor.hash_trace_identifier(run_id),
        correlation_hash=monitor.hash_trace_identifier(correlation_id),
        resource_id=_RESOURCE_ID,
        application_id=_APPLICATION_ID,
        correlation_id=correlation_id,
    )


def _proof(
    workspace_id: str = "workspace-private",
    run_id: str = "run-private",
    correlation_id: str = "b" * 32,
    *,
    resource_id: str = _RESOURCE_ID,
    application_id: str = _APPLICATION_ID,
):
    proof = getattr(monitor, "RemoteTraceProof", None)
    assert callable(proof)
    return proof(
        observed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        trace_id=correlation_id,
        workspace_hash=monitor.hash_trace_identifier(workspace_id),
        run_hash=monitor.hash_trace_identifier(run_id),
        correlation_hash=monitor.hash_trace_identifier(correlation_id),
        resource_id=resource_id,
        application_id=application_id,
        source_table="requests",
    )


def test_configured_exporter_without_observed_trace_is_partial() -> None:
    status = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_proof=None,
        expected=_expectation(correlation_id="a" * 32),
        correlation_id="a" * 32,
        exporter_state="unknown",
    )

    assert status.state == "partial"
    assert status.last_export_confirmed_at is None
    assert status.exporter_state == "unknown"


def test_only_validated_matching_span_proof_connects_without_exposing_actor_or_raw_result() -> None:
    actor_email = "person@example.com"
    status = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_proof=_proof(correlation_id="b" * 32),
        expected=_expectation(correlation_id="b" * 32),
        correlation_id="b" * 32,
        exporter_state="succeeded",
    )

    assert status.state == "connected"
    payload = status.model_dump_json()
    assert actor_email not in payload
    assert "workspace-private" not in payload


def test_untyped_or_mismatched_remote_proof_cannot_connect() -> None:
    expected = _expectation()
    with pytest.raises(TypeError, match="RemoteTraceProof"):
        _build_status(
            configured=True,
            local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            remote_proof={"observed_at": datetime(2026, 7, 13, tzinfo=timezone.utc)},
            expected=expected,
            correlation_id="b" * 32,
        )
    mismatched = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_proof=_proof(workspace_id="other-workspace"),
        expected=expected,
        correlation_id="b" * 32,
    )

    assert mismatched.state == "partial"


def test_remote_proof_binds_correlation_hash_to_its_trace_id() -> None:
    proof = getattr(monitor, "RemoteTraceProof")
    with pytest.raises(ValueError, match="correlation hash"):
        proof(
            observed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            trace_id="b" * 32,
            workspace_hash=monitor.hash_trace_identifier("workspace-private"),
            run_hash=monitor.hash_trace_identifier("run-private"),
            correlation_hash=monitor.hash_trace_identifier("a" * 32),
            resource_id=_RESOURCE_ID,
            application_id=_APPLICATION_ID,
            source_table="requests",
        )


def test_not_configured_and_unavailable_are_distinct_from_partial() -> None:
    not_configured = _build_status(configured=False, local_emit_at=None, remote_proof=None, expected=None, correlation_id=None, exporter_state="unknown")
    unavailable = _build_status(
        configured=True,
        local_emit_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        remote_proof=None,
        expected=_expectation(correlation_id="c" * 32),
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
    def __init__(self, row: list[object], *, source_table: str = "requests") -> None:
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
                        _Column("source_table"),
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


class _MetricQueryResult:
    def __init__(self, row: list[object]) -> None:
        self.tables = [
            type(
                "Table",
                (),
                {
                    "columns": [
                        _Column("record_count"),
                        _Column("request_count"),
                        _Column("dependency_count"),
                        _Column("trace_event_count"),
                        _Column("error_count"),
                        _Column("first_observed_at"),
                        _Column("last_observed_at"),
                        _Column("appId"),
                        _Column("_ResourceId"),
                    ],
                    "rows": [row],
                },
            )()
        ]


class _PartialResult:
    def __init__(self, partial_error: object, partial_data: object) -> None:
        self.partial_error = partial_error
        self.partial_data = partial_data


def _monitor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=not-a-secret")
    monkeypatch.setenv("DF_AZURE_MONITOR_LOGS_WORKSPACE_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("DF_APP_INSIGHTS_APPLICATION_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv(
        "DF_APP_INSIGHTS_RESOURCE_ID",
        _RESOURCE_ID,
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
                _APPLICATION_ID,
                _RESOURCE_ID,
                "requests",
                run_hash,
                correlation_hash,
            ]
        )
    )

    remote = monitor.query_trace_delivery(workspace_id, run_id, correlation_id, client_factory=lambda: client)

    assert remote is not None
    assert remote.trace_id == correlation_id
    assert remote.source_table == "requests"
    args = client.calls[0]
    query = args[1]
    assert isinstance(query, str)
    assert workspace_id not in query
    assert run_id not in query
    assert correlation_id not in query
    assert workspace_hash in query
    assert run_hash in query
    assert correlation_hash in query
    assert query.startswith("union isfuzzy=true withsource=source_table requests, dependencies, traces")
    assert "traces" in query
    assert "customDimensions" in query
    assert "take 1" in query
    assert args[2]["timespan"].total_seconds() <= 15 * 60
    assert args[2]["server_timeout"] <= 10


def test_query_accepts_foundry_root_span_from_traces_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    workspace_id = "workspace-private"
    run_id = "run-private"
    correlation_id = "e" * 32
    client = _LogsClient(
        _QueryResult(
            [
                "2026-07-13T00:00:00Z",
                correlation_id,
                _APPLICATION_ID,
                _RESOURCE_ID,
                "traces",
                monitor.hash_trace_identifier(run_id),
                monitor.hash_trace_identifier(correlation_id),
            ]
        )
    )

    remote = monitor.query_trace_delivery(workspace_id, run_id, correlation_id, client_factory=lambda: client)

    assert remote is not None
    assert remote.source_table == "traces"
    assert "requests, dependencies, traces" in client.calls[0][1]


def test_telemetry_metrics_are_aggregate_only_and_bound_to_hashed_run_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    workspace_id = "workspace-private"
    run_id = "run-private"
    correlation_id = "f" * 32
    client = _LogsClient(
        _MetricQueryResult(
            [
                12,
                2,
                8,
                2,
                1,
                "2026-07-20T01:00:00Z",
                "2026-07-20T01:01:30Z",
                _APPLICATION_ID,
                _RESOURCE_ID,
            ]
        )
    )
    query = getattr(monitor, "query_trace_telemetry_metrics", None)
    assert callable(query)

    metrics = query(workspace_id, run_id, correlation_id, client_factory=lambda: client)

    assert metrics.state == "connected"
    assert metrics.record_count == 12
    assert metrics.request_count == 2
    assert metrics.dependency_count == 8
    assert metrics.trace_event_count == 2
    assert metrics.error_count == 1
    assert metrics.first_observed_at == datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
    assert metrics.last_observed_at == datetime(2026, 7, 20, 1, 1, 30, tzinfo=timezone.utc)
    payload = metrics.model_dump_json()
    assert workspace_id not in payload
    assert run_id not in payload
    assert _RESOURCE_ID not in payload
    assert _APPLICATION_ID not in payload
    recorded_query = client.calls[0][1]
    assert workspace_id not in recorded_query
    assert run_id not in recorded_query
    assert correlation_id not in recorded_query
    assert monitor.hash_trace_identifier(workspace_id) in recorded_query
    assert monitor.hash_trace_identifier(run_id) in recorded_query
    assert monitor.hash_trace_identifier(correlation_id) in recorded_query
    assert "summarize" in recorded_query


def test_partial_logs_query_is_unavailable_and_never_uses_partial_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    partial_data = _QueryResult(
        [
            "2026-07-13T00:00:00Z",
            "a" * 32,
            _APPLICATION_ID,
            _RESOURCE_ID,
            "requests",
            monitor.hash_trace_identifier("run-a"),
            monitor.hash_trace_identifier("a" * 32),
        ]
    )
    client = _LogsClient(_PartialResult({"status": 429, "code": "ThrottledError", "message": "token=private"}, partial_data))

    with pytest.raises(monitor.TraceDeliveryQueryError) as error:
        monitor.query_trace_delivery("ws-a", "run-a", "a" * 32, client_factory=lambda: client)

    assert error.value.error_type == "LogsQueryPartialResult"
    assert error.value.error_status == 429


def test_partial_logs_query_without_status_is_still_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    client = _LogsClient(_PartialResult({"message": "untrusted partial payload"}, _QueryResult([])))

    with pytest.raises(monitor.TraceDeliveryQueryError) as error:
        monitor.query_trace_delivery("ws-a", "run-a", "a" * 32, client_factory=lambda: client)

    assert error.value.error_type == "LogsQueryPartialResult"
    assert error.value.error_status is None


def test_query_client_is_injected_for_tests_without_default_credential_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    client = _LogsClient(type("Empty", (), {"tables": []})())

    assert monitor.query_trace_delivery("ws-a", "run-a", "a" * 32, client_factory=lambda: client) is None
    assert "DefaultAzureCredential" not in open(monitor.__file__, encoding="utf-8").read()


def test_success_confirmation_cache_is_scoped_and_does_not_cache_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    _monitor_env(monkeypatch)
    reset = getattr(monitor, "clear_trace_delivery_cache", None)
    assert callable(reset)
    reset()
    calls: list[tuple[str, str, str]] = []

    def confirmed(workspace_id: str, run_id: str, correlation_id: str, **_kwargs: object):
        calls.append((workspace_id, run_id, correlation_id))
        return _proof(workspace_id, run_id, correlation_id, resource_id=os.environ["DF_APP_INSIGHTS_RESOURCE_ID"], application_id=os.environ["DF_APP_INSIGHTS_APPLICATION_ID"])

    monkeypatch.setattr(monitor, "query_trace_delivery", confirmed)
    first = monitor.get_trace_delivery_status("ws-a", "run-a", "e" * 32)
    second = monitor.get_trace_delivery_status("ws-a", "run-a", "e" * 32)
    other_workspace = monitor.get_trace_delivery_status("ws-b", "run-a", "e" * 32)

    assert first.state == second.state == other_workspace.state == "connected"
    assert calls == [("ws-a", "run-a", "e" * 32), ("ws-b", "run-a", "e" * 32)]

    monkeypatch.setenv("DF_APP_INSIGHTS_APPLICATION_ID", "44444444-4444-4444-4444-444444444444")
    changed_application = monitor.get_trace_delivery_status("ws-a", "run-a", "e" * 32)
    assert changed_application.state == "connected"
    assert calls[-1] == ("ws-a", "run-a", "e" * 32)

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
        monitor.query_trace_delivery("ws-a", "run-a", "a" * 32, client_factory=lambda: client)

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


def test_trace_status_endpoint_authorizes_before_run_lookup_and_verifies_run_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(control_plane, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(
        control_plane,
        "workspace_role",
        lambda _workspace_id, _actor: "viewer",
    )
    monkeypatch.setattr(control_plane, "authorize", lambda _role, action: events.append(action) or True)
    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: events.append("get_run") or {"workspace_id": "ws-a"})
    monkeypatch.setattr(
        control_plane,
        "get_trace_delivery_status",
        lambda workspace_id, run_id, correlation_id=None: _build_status(
            configured=False,
            local_emit_at=None,
            remote_proof=None,
            expected=None,
            correlation_id=correlation_id,
            exporter_state="unknown",
        ),
    )

    payload = asyncio.run(control_plane.workspace_trace_status("ws-a", None, run_id="run-a", correlation_id="a" * 32))

    assert payload["state"] == "not_configured"
    assert events == ["run.read", "get_run"]

    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: {"workspace_id": "ws-other"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(control_plane.workspace_trace_status("ws-a", None, run_id="run-a"))
    assert error.value.status_code == 404

    monkeypatch.setattr(
        control_plane,
        "authorize",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: pytest.fail("run lookup must not run before authorization"))
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(control_plane.workspace_trace_status("ws-a", None, run_id="run-a"))
    assert forbidden.value.status_code == 403


def test_trace_metrics_endpoint_authorizes_before_run_lookup_and_returns_only_aggregate_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    telemetry = getattr(monitor, "TraceTelemetryMetrics", None)
    assert callable(telemetry)
    monkeypatch.setattr(control_plane, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(control_plane, "workspace_role", lambda _workspace_id, _actor: "viewer")
    monkeypatch.setattr(control_plane, "authorize", lambda _role, action: events.append(action) or True)
    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: events.append("get_run") or {"workspace_id": "ws-a"})
    monkeypatch.setattr(
        control_plane,
        "get_trace_telemetry_metrics",
        lambda _workspace_id, _run_id, correlation_id=None: telemetry(
            state="connected",
            correlation_id=correlation_id,
            record_count=6,
            request_count=1,
            dependency_count=4,
            trace_event_count=1,
            error_count=0,
        ),
    )

    payload = asyncio.run(control_plane.workspace_trace_metrics("ws-a", None, run_id="run-a", correlation_id="a" * 32))

    assert payload == {
        "state": "connected",
        "correlation_id": "a" * 32,
        "record_count": 6,
        "request_count": 1,
        "dependency_count": 4,
        "trace_event_count": 1,
        "error_count": 0,
        "first_observed_at": None,
        "last_observed_at": None,
        "error_type": None,
        "error_status": None,
    }
    assert events == ["run.read", "get_run"]

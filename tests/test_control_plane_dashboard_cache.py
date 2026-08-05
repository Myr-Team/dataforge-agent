from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import control_plane


def _payload(workspace_id: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "workspace": {"workspace_id": workspace_id, "name": workspace_id},
        "workspaces": [],
        "runs": [],
        "conversations": [],
        "health": {"ok": True, "dependencies": {}},
        "dependency_details": {},
        "dashboard_meta": {"duration_ms": 1, "loaded_at": "now"},
    }


def _stub_dashboard_sources(monkeypatch: pytest.MonkeyPatch, workspace_getter) -> None:
    monkeypatch.setattr(control_plane, "get_workspace_detail", workspace_getter)
    monkeypatch.setattr(control_plane, "list_workspaces", lambda: [])
    monkeypatch.setattr(control_plane, "list_runs", lambda _workspace_id: [])
    monkeypatch.setattr(control_plane, "list_conversations", lambda _workspace_id: [])
    monkeypatch.setattr(
        control_plane,
        "_cached_health",
        lambda: {"dependencies": {}, "dependency_details": {}},
    )


@pytest.fixture(autouse=True)
def _reset_dashboard_cache(monkeypatch: pytest.MonkeyPatch):
    cache = getattr(control_plane, "_DASHBOARD_CACHE", None)
    if cache is not None:
        cache.clear()
    states = getattr(control_plane, "_DASHBOARD_BUILD_STATES", None)
    if states is not None:
        states.clear()
    monkeypatch.setattr(control_plane, "DASHBOARD_CACHE_SECONDS", 2.0, raising=False)
    monkeypatch.setattr(control_plane, "DASHBOARD_CACHE_MAX_ENTRIES", 128, raising=False)
    yield
    if cache is not None:
        cache.clear()
    if states is not None:
        states.clear()


def test_dashboard_reuses_cached_payload_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def workspace_getter(workspace_id: str):
        calls.append(workspace_id)
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    first = control_plane.build_workspace_dashboard("ws-a")
    second = control_plane.build_workspace_dashboard("ws-a")

    assert calls == ["ws-a"]
    assert first == second


def test_slow_build_gets_full_ttl_after_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    clock = [10.0]
    monkeypatch.setattr(control_plane.time, "monotonic", lambda: clock[0])

    def workspace_getter(workspace_id: str):
        calls.append(workspace_id)
        clock[0] = 13.0
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    control_plane.build_workspace_dashboard("ws-a")
    clock[0] = 14.5
    control_plane.build_workspace_dashboard("ws-a")

    assert calls == ["ws-a"]


def test_same_workspace_concurrent_requests_build_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    calls_lock = threading.Lock()

    def workspace_getter(workspace_id: str):
        with calls_lock:
            calls.append(workspace_id)
        time.sleep(0.08)
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(control_plane.build_workspace_dashboard, ["ws-a", "ws-a"]))

    assert [item["workspace_id"] for item in results] == ["ws-a", "ws-a"]
    assert calls == ["ws-a"]


def test_different_workspaces_build_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2)

    def workspace_getter(workspace_id: str):
        barrier.wait(timeout=1)
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(control_plane.build_workspace_dashboard, ["ws-a", "ws-b"]))

    assert {item["workspace_id"] for item in results} == {"ws-a", "ws-b"}


def test_nested_mutation_does_not_change_cached_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dashboard_sources(
        monkeypatch,
        lambda workspace_id: _payload(workspace_id)["workspace"],
    )

    first = control_plane.build_workspace_dashboard("ws-a")
    first["runs"].append({"run_id": "caller-change"})

    assert control_plane.build_workspace_dashboard("ws-a")["runs"] == []


def test_dashboard_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def workspace_getter(workspace_id: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("read unavailable")
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    with pytest.raises(RuntimeError, match="read unavailable"):
        control_plane.build_workspace_dashboard("ws-a")

    assert control_plane.build_workspace_dashboard("ws-a")["workspace_id"] == "ws-a"
    assert calls == 2


def test_dashboard_cache_evicts_oldest_entry_above_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(control_plane, "DASHBOARD_CACHE_MAX_ENTRIES", 2, raising=False)

    def workspace_getter(workspace_id: str):
        calls.append(workspace_id)
        return _payload(workspace_id)["workspace"]

    _stub_dashboard_sources(monkeypatch, workspace_getter)

    for workspace_id in ("ws-a", "ws-b", "ws-c"):
        control_plane.build_workspace_dashboard(workspace_id)

    assert set(getattr(control_plane, "_DASHBOARD_CACHE", {})) == {"ws-b", "ws-c"}
    control_plane.build_workspace_dashboard("ws-a")
    assert calls == ["ws-a", "ws-b", "ws-c", "ws-a"]

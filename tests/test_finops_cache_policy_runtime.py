from __future__ import annotations

import backend.orchestrator as orchestrator


def test_workspace_finops_cache_policy_controls_runtime_and_ttl(monkeypatch) -> None:
    monkeypatch.delenv("DF_DISABLE_REDIS_CACHE", raising=False)
    monkeypatch.setattr(
        orchestrator,
        "load_workspace_finops_cache_policy",
        lambda _workspace_id: {"version": 2, "enabled": False, "ttl_seconds": 600},
    )
    assert orchestrator._workspace_finops_cache_settings("ws-a") == (False, 600)

    monkeypatch.setattr(
        orchestrator,
        "load_workspace_finops_cache_policy",
        lambda _workspace_id: {"version": 3, "enabled": True, "ttl_seconds": 900},
    )
    assert orchestrator._workspace_finops_cache_settings("ws-a") == (True, 900)

    monkeypatch.setenv("DF_DISABLE_REDIS_CACHE", "1")
    assert orchestrator._workspace_finops_cache_settings("ws-a") == (False, 900)


def test_workspace_finops_cache_policy_fails_safe_when_configuration_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("DF_DISABLE_REDIS_CACHE", raising=False)
    monkeypatch.setattr(
        orchestrator,
        "load_workspace_finops_cache_policy",
        lambda _workspace_id: (_ for _ in ()).throw(RuntimeError("blob unavailable")),
    )
    assert orchestrator._workspace_finops_cache_settings("ws-a") == (True, 3600)

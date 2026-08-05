from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import time

import backend.entra_membership as membership
import backend.graph_client as graph_client
from backend.identity import actor_from_headers


class _Cache:
    def __init__(self) -> None:
        self.value = None
        self.last_ttl = None

    def get_json(self, _key: str):
        return self.value, {
            "provider": "redis",
            "status": "hit" if self.value else "miss",
        }

    def set_json(self, _key: str, value: dict[str, object], **kwargs):
        self.value = value
        self.last_ttl = kwargs.get("ttl_seconds")
        return {"provider": "redis", "status": "stored"}


def test_direct_group_claims_are_hashed_without_graph(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "membership-secret")
    called = []
    result = membership.resolve_actor_group_membership(
        {
            "actor_id": "actor-a",
            "tenant_id": "tenant-a",
            "groups": ["raw-group-a", "raw-group-b"],
            "group_overage": False,
        },
        request=None,
        graph_loader=lambda _request: called.append(True) or [],
        cache=_Cache(),
    )

    assert result["state"] == "observed"
    assert len(result["group_refs"]) == 2
    assert all(item.startswith("group_") for item in result["group_refs"])
    assert "raw-group-a" not in str(result)
    assert called == []


def test_overage_uses_fixed_graph_membership_and_safe_cache(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "membership-secret")
    cache = _Cache()
    calls = []
    actor = {
        "actor_id": "actor-a",
        "tenant_id": "tenant-a",
        "groups": [],
        "group_overage": True,
    }

    first = membership.resolve_actor_group_membership(
        actor,
        request=object(),
        graph_loader=lambda _request: calls.append(True)
        or [{"id": "raw-group-a", "display_name": "Finance"}],
        cache=cache,
    )
    second = membership.resolve_actor_group_membership(
        actor,
        request=object(),
        graph_loader=lambda _request: (_ for _ in ()).throw(
            AssertionError("cache should be used")
        ),
        cache=cache,
    )

    assert first["state"] == "observed"
    assert second["state"] == "observed"
    assert first["group_refs"] == second["group_refs"]
    assert calls == [True]
    assert "raw-group-a" not in str(cache.value)


def test_graph_failure_drops_group_grants_without_stale_elevation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "membership-secret")

    result = membership.resolve_actor_group_membership(
        {
            "actor_id": "actor-a",
            "tenant_id": "tenant-a",
            "group_overage": True,
        },
        request=object(),
        graph_loader=lambda _request: (_ for _ in ()).throw(
            RuntimeError("graph unavailable")
        ),
        cache=_Cache(),
    )

    assert result == {
        "state": "unavailable",
        "group_refs": [],
        "source": "microsoft_graph",
        "permission_state": "unavailable",
    }


def test_overage_unavailable_result_is_short_cached(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "membership-secret")
    cache = _Cache()
    calls = 0

    def unavailable(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("graph unavailable")

    actor = {
        "actor_id": "actor-a",
        "tenant_id": "tenant-a",
        "group_overage": True,
    }
    first = membership.resolve_actor_group_membership(
        actor,
        request=object(),
        graph_loader=unavailable,
        cache=cache,
    )
    second = membership.resolve_actor_group_membership(
        actor,
        request=object(),
        graph_loader=unavailable,
        cache=cache,
    )

    assert first["state"] == second["state"] == "unavailable"
    assert calls == 1
    assert cache.last_ttl == 30


def test_concurrent_overage_resolution_uses_one_graph_loader(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "membership-secret")
    cache = _Cache()
    calls = 0

    def slow_loader(_request):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return [{"id": "raw-group-a"}]

    actor = {
        "actor_id": "actor-concurrent",
        "tenant_id": "tenant-a",
        "group_overage": True,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: membership.resolve_actor_group_membership(
                    actor,
                    request=object(),
                    graph_loader=slow_loader,
                    cache=cache,
                ),
                range(2),
            )
        )

    assert calls == 1
    assert results[0]["group_refs"] == results[1]["group_refs"]


def test_easy_auth_hasgroups_claim_marks_overage(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    principal = {
        "claims": [
            {"typ": "oid", "val": "actor-a"},
            {"typ": "tid", "val": "tenant-a"},
            {"typ": "hasgroups", "val": "true"},
        ]
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(principal).encode("utf-8")
    ).decode("ascii")

    actor = actor_from_headers(
        {
            "x-ms-client-principal": encoded,
            "x-dataforge-proxy-secret": "test-proxy-secret",
        },
        fallback=False,
    )

    assert actor["group_overage"] is True
    assert actor.get("groups", []) == []


def test_graph_overage_loader_uses_only_signed_in_fixed_path(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        graph_client,
        "graph_request",
        lambda method, path, token, **kwargs: calls.append(
            (method, path, token, kwargs)
        )
        or {"value": [{"id": "group-a", "displayName": "Finance"}]},
    )
    request = type(
        "Request",
        (),
        {"headers": {"x-ms-token-aad-access-token": "delegated-token"}},
    )()

    groups = graph_client.list_signed_in_transitive_groups(request)

    assert groups == [{"id": "group-a", "display_name": "Finance"}]
    assert calls[0][0:3] == (
        "GET",
        "/me/transitiveMemberOf/microsoft.graph.group",
        "delegated-token",
    )

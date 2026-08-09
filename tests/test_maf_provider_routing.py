from __future__ import annotations

import asyncio

import pytest

import backend.maf_agents as maf_agents
from backend.model_policy import ModelRoute, SelectedTextRoute


def _selected(provider_type: str) -> SelectedTextRoute:
    return SelectedTextRoute(
        route=ModelRoute(
            "analysis",
            "deepseek-v4-pro" if provider_type == "deepseek" else "gpt-5.1",
            "Analysis",
            frozenset({"analysis", "chat"}),
            provider_id="provider-deepseek" if provider_type == "deepseek" else None,
            provider_type=provider_type,
            model_id="deepseek-v4-pro" if provider_type == "deepseek" else "gpt-5.1",
        ),
        execution_kind="full_analysis",
    )


def test_external_maf_route_is_rejected_while_runtime_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "0")

    with pytest.raises(RuntimeError, match="external provider routing is disabled"):
        maf_agents._create_maf_chat_client(_selected("deepseek"))


def test_external_maf_route_uses_the_governed_direct_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("DF_PROVIDER_APIM_ENABLED", "0")
    observed: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(maf_agents, "OpenAIChatClient", _Client)
    monkeypatch.setattr(
        maf_agents,
        "current_provider_connection",
        lambda provider_id: {
            "tenant_ref": "tenant-safe",
            "provider_id": provider_id,
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "secret_ref": "kv:provider-deepseek",
        },
        raising=False,
    )
    monkeypatch.setattr(
        maf_agents,
        "runtime_provider_secret",
        lambda _connection: "secret-marker",
        raising=False,
    )

    client = maf_agents._create_maf_chat_client(_selected("deepseek"))

    assert isinstance(client, _Client)
    assert observed == {
        "model": "deepseek-v4-pro",
        "api_key": "secret-marker",
        "base_url": "https://api.deepseek.com",
    }


def test_external_maf_route_rejects_missing_runtime_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "0")
    monkeypatch.setattr(
        maf_agents,
        "current_provider_connection",
        lambda _provider_id: None,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="connection is unavailable"):
        maf_agents._create_maf_chat_client(_selected("deepseek"))


def test_bounded_maf_fallback_retries_transient_pre_output_failure_once() -> None:
    calls: list[str] = []

    class _Response:
        def __init__(self) -> None:
            self.additional_properties: dict[str, object] = {}

    class _RateLimited(RuntimeError):
        status_code = 429

    class _Client:
        def __init__(self, name: str, error: Exception | None = None) -> None:
            self.name = name
            self.error = error

        def get_response(self, *_args: object, **_kwargs: object):
            async def respond():
                calls.append(self.name)
                if self.error:
                    raise self.error
                return _Response()

            return respond()

    client = maf_agents._BoundedFallbackChatClient(
        _Client("deepseek", _RateLimited("limited")),
        _Client("azure"),
        fallback_route=ModelRoute(
            "azure",
            "gpt-5.1",
            "Azure",
            frozenset({"chat"}),
        ),
    )

    response = asyncio.run(client.get_response(["hello"]))
    assert response.additional_properties == {
        "model_route": "azure",
        "provider_type": "azure_foundry",
        "provider_id": None,
        "model_id": "gpt-5.1",
        "fallback_reason": "rate_limited",
    }
    assert calls == ["deepseek", "azure"]


def test_bounded_maf_fallback_does_not_hide_authentication_failure() -> None:
    calls: list[str] = []

    class _Unauthorized(RuntimeError):
        status_code = 401

    class _Client:
        def __init__(self, name: str, error: Exception | None = None) -> None:
            self.name = name
            self.error = error

        def get_response(self, *_args: object, **_kwargs: object):
            async def respond():
                calls.append(self.name)
                if self.error:
                    raise self.error
                return "unexpected"

            return respond()

    client = maf_agents._BoundedFallbackChatClient(
        _Client("deepseek", _Unauthorized("bad key")),
        _Client("azure"),
        fallback_route=ModelRoute(
            "azure",
            "gpt-5.1",
            "Azure",
            frozenset({"chat"}),
        ),
    )

    with pytest.raises(_Unauthorized):
        asyncio.run(client.get_response(["hello"]))
    assert calls == ["deepseek"]

from __future__ import annotations

import backend.foundry_client as foundry_client


def test_apim_client_uses_container_managed_identity_not_an_openai_key(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    provider_calls: list[tuple[object, str]] = []

    class Credential:
        pass

    class AzureClient:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "1")
    monkeypatch.setenv("DF_APIM_GATEWAY_URL", "https://dfmonapim721.azure-api.net/")
    monkeypatch.setenv("DF_APIM_AUDIENCE", "api://gateway-app-id")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    monkeypatch.setattr(foundry_client, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(
        foundry_client,
        "_gateway_request_headers",
        lambda: {
            "x-dataforge-workspace-hash": "w" * 64,
            "x-dataforge-run-hash": "r" * 64,
            "x-dataforge-actor-hash": "a" * 64,
            "x-dataforge-correlation-id": "c" * 32,
            "x-dataforge-model-route": "default",
        },
    )
    monkeypatch.setattr(
        foundry_client,
        "get_bearer_token_provider",
        lambda credential, scope: provider_calls.append((credential, scope)) or "provider",
    )
    monkeypatch.setattr(foundry_client, "AzureOpenAI", AzureClient)

    client = foundry_client._configured_apim_openai_client()

    assert isinstance(client, AzureClient)
    assert len(provider_calls) == 1
    assert provider_calls[0][1] == "api://gateway-app-id/.default"
    assert isinstance(provider_calls[0][0], Credential)
    assert calls == [
        {
            "azure_endpoint": "https://dfmonapim721.azure-api.net",
            "azure_ad_token_provider": "provider",
            "api_version": "2025-04-01-preview",
            "max_retries": 0,
            "default_headers": {
                "x-dataforge-workspace-hash": "w" * 64,
                "x-dataforge-run-hash": "r" * 64,
                "x-dataforge-actor-hash": "a" * 64,
                "x-dataforge-correlation-id": "c" * 32,
                "x-dataforge-model-route": "default",
            },
        }
    ]


def test_openai_client_prefers_enabled_apim_over_direct_api_key(monkeypatch) -> None:
    apim_client = object()

    monkeypatch.setattr(foundry_client, "_configured_apim_openai_client", lambda: apim_client)
    monkeypatch.setattr(
        foundry_client,
        "_configured_azure_openai_client",
        lambda: (_ for _ in ()).throw(AssertionError("direct API key path must not be selected")),
    )

    assert foundry_client._openai_client() is apim_client


def test_enabled_apim_without_audience_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DF_APIM_GATEWAY_URL", "https://dfmonapim721.azure-api.net")
    monkeypatch.delenv("DF_APIM_AUDIENCE", raising=False)

    try:
        foundry_client._configured_apim_openai_client()
    except RuntimeError as exc:
        assert "DF_APIM_AUDIENCE" in str(exc)
    else:
        raise AssertionError("gateway configuration must fail closed")

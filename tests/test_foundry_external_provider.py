from __future__ import annotations

from backend import foundry_client
from backend.model_policy import ModelRoute, SelectedTextRoute, model_route_scope
from backend.model_provider_runtime import provider_runtime_scope
from backend.provider_client import ProviderResult
from backend.provider_usage import ProviderUsage


def _deepseek_selection() -> SelectedTextRoute:
    return SelectedTextRoute(
        route=ModelRoute(
            route_id="deepseek-provider-flash",
            deployment="deepseek-v4-flash",
            label="DeepSeek V4 Flash",
            capabilities=frozenset({"chat", "analysis"}),
            provider_id="deepseek-provider",
            provider_type="deepseek",
            model_id="deepseek-v4-flash",
        ),
        execution_kind="direct_reply",
        selection="workspace_default",
        policy_revision=3,
        price_card_revision=4,
    )


def test_run_agent_uses_scoped_deepseek_connection_without_azure_client(monkeypatch):
    observed: dict[str, object] = {}

    class _Provider:
        def __init__(self, *, transport, timeout_seconds):
            observed["transport"] = transport
            observed["timeout_seconds"] = timeout_seconds

        def invoke(self, invocation, *, api_key, base_url):
            observed["invocation"] = invocation
            observed["api_key"] = api_key
            observed["base_url"] = base_url
            return ProviderResult(
                text='{"conclusion":"已通过 DeepSeek 分析"}',
                usage=ProviderUsage(
                    input_tokens=120,
                    output_tokens=40,
                    provider_cache_hit_tokens=80,
                    provider_cache_miss_tokens=40,
                    total_tokens=160,
                ),
                latency_ms=345,
                output_started=True,
            )

    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    monkeypatch.setenv("DF_PROVIDER_APIM_ENABLED", "0")
    monkeypatch.setattr(foundry_client, "DeepSeekProvider", _Provider, raising=False)
    monkeypatch.setattr(
        foundry_client,
        "RequestsProviderTransport",
        lambda: "transport-marker",
        raising=False,
    )
    monkeypatch.setattr(
        foundry_client,
        "runtime_provider_secret",
        lambda _connection: "secret-marker",
        raising=False,
    )
    monkeypatch.setattr(
        foundry_client,
        "_project_client",
        lambda: (_ for _ in ()).throw(AssertionError("Azure client must not be used")),
    )

    connection = {
        "tenant_ref": "tenant-safe",
        "provider_id": "deepseek-provider",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "secret_ref": "kv:deepseek-provider",
    }
    with provider_runtime_scope([connection]):
        with model_route_scope(route=_deepseek_selection()):
            result = foundry_client.run_agent(
                "df-finops-analyst",
                '{"question":"分析当前成本"}',
                response_schema={
                    "type": "object",
                    "properties": {"conclusion": {"type": "string"}},
                },
                max_output_tokens=650,
                request_timeout_seconds=8,
                retry_limit=0,
                thinking="disabled",
            )

    invocation = observed["invocation"]
    assert invocation.model_id == "deepseek-v4-flash"
    assert invocation.response_format == {"type": "json_object"}
    assert invocation.thinking == "disabled"
    assert observed["api_key"] == "secret-marker"
    assert observed["base_url"] == "https://api.deepseek.com"
    assert result["structured"] == {"conclusion": "已通过 DeepSeek 分析"}
    assert result["provider_type"] == "deepseek"
    assert result["model_id"] == "deepseek-v4-flash"
    assert result["gateway_coverage"] == "app_observed"
    assert result["usage"] == {
        "input_tokens": 120,
        "output_tokens": 40,
        "reasoning_tokens": None,
        "provider_cache_hit_tokens": 80,
        "provider_cache_miss_tokens": 40,
        "total_tokens": 160,
    }
    assert result["provider_cache"] == {
        "state": "partial_hit",
        "hit_tokens": 80,
        "miss_tokens": 40,
        "hit_rate_pct": 66.67,
        "evidence_state": "observed",
    }

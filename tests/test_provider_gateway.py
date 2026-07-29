from __future__ import annotations

import pytest

from backend.deepseek_provider import ProviderFailure
from backend.model_policy import ModelRoute
from backend.provider_client import ProviderInvocation, ProviderMessage, ProviderResult
from backend.provider_gateway import ProviderGateway
from backend.provider_usage import ProviderUsage


def _route(route_id: str, provider_type: str) -> ModelRoute:
    model_id = "deepseek-v4-pro" if provider_type == "deepseek" else "gpt-5.1"
    return ModelRoute(
        route_id,
        model_id,
        route_id,
        frozenset({"chat"}),
        provider_id="provider-deepseek" if provider_type == "deepseek" else None,
        provider_type=provider_type,
        model_id=model_id,
    )


def _invocation() -> ProviderInvocation:
    return ProviderInvocation(
        request_ref="request-opaque",
        correlation_ref="correlation-opaque",
        workspace_id="workspace-1",
        execution_kind="direct_reply",
        model_id="deepseek-v4-pro",
        messages=[ProviderMessage(role="user", content="hello")],
    )


def test_gateway_records_failed_primary_and_successful_fallback() -> None:
    attempts: list[dict[str, object]] = []

    def invoke(route: ModelRoute, invocation: ProviderInvocation) -> ProviderResult:
        assert invocation.model_id == route.model_id
        if route.route_id == "external":
            raise ProviderFailure(
                "rate_limited",
                retryable=True,
                status_code=429,
            )
        return ProviderResult(
            text="ok",
            usage=ProviderUsage(input_tokens=2, output_tokens=1, total_tokens=3),
            output_started=True,
        )

    result = ProviderGateway(invoke_route=invoke, record_attempt=attempts.append).invoke(
        invocation=_invocation(),
        primary=_route("external", "deepseek"),
        fallback=_route("azure", "azure_foundry"),
    )

    assert result.route.route_id == "azure"
    assert result.result.text == "ok"
    assert [item["state"] for item in attempts] == ["failed", "succeeded"]
    assert attempts[0]["safe_error_category"] == "rate_limited"
    assert "request_ref" not in attempts[0]
    assert "correlation_ref" not in attempts[0]


def test_gateway_does_not_fallback_after_output_or_side_effect() -> None:
    def invoke(_route: ModelRoute, _invocation: ProviderInvocation) -> ProviderResult:
        raise ProviderFailure(
            "provider_unavailable",
            retryable=True,
            status_code=503,
            output_started=True,
        )

    gateway = ProviderGateway(invoke_route=invoke)

    with pytest.raises(ProviderFailure):
        gateway.invoke(
            invocation=_invocation(),
            primary=_route("external", "deepseek"),
            fallback=_route("azure", "azure_foundry"),
        )

    with pytest.raises(ProviderFailure):
        gateway.invoke(
            invocation=_invocation(),
            primary=_route("external", "deepseek"),
            fallback=_route("azure", "azure_foundry"),
            side_effect_started=True,
        )

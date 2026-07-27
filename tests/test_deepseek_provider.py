from __future__ import annotations

import json

import pytest

from backend.deepseek_provider import (
    DeepSeekProvider,
    ProviderFailure,
    ProviderHttpResponse,
    parse_deepseek_sse,
)
from backend.provider_client import ProviderInvocation, ProviderMessage


class _Transport:
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(self, **kwargs: object) -> ProviderHttpResponse:
        self.calls.append(kwargs)
        return self.response


def _invocation(*, stream: bool = False) -> ProviderInvocation:
    return ProviderInvocation(
        request_ref="req_safe",
        correlation_ref="corr_safe",
        workspace_id="ws-safe",
        agent_id="opportunity-agent",
        execution_kind="analysis",
        model_id="deepseek-v4-flash",
        messages=[
            ProviderMessage(role="system", content="Be concise."),
            ProviderMessage(role="user", content="Analyze the evidence."),
        ],
        stream=stream,
    )


def test_deepseek_adapter_normalizes_text_tools_and_usage() -> None:
    transport = _Transport(
        ProviderHttpResponse(
            status_code=200,
            headers={},
            json_body={
                "choices": [
                    {
                        "message": {
                            "content": "Result",
                            "tool_calls": [
                                {
                                    "id": "tool-safe",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": "{\"id\":\"safe\"}",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "prompt_cache_hit_tokens": 8,
                    "prompt_cache_miss_tokens": 4,
                },
            },
        )
    )
    provider = DeepSeekProvider(transport=transport, clock=lambda: 10.0)

    result = provider.invoke(
        _invocation(),
        api_key="secret-marker",
        base_url="https://api.deepseek.com",
    )

    assert result.text == "Result"
    assert result.tool_calls[0].name == "lookup"
    assert result.usage.provider_cache_hit_tokens == 8
    assert result.output_started is True
    assert transport.calls[0]["path"] == "/chat/completions"
    assert transport.calls[0]["api_key"] == "secret-marker"


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (400, "invalid_request", False),
        (401, "authentication_failed", False),
        (402, "insufficient_balance", False),
        (422, "invalid_parameters", False),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
        (503, "provider_unavailable", True),
    ],
)
def test_deepseek_adapter_maps_errors_without_returning_provider_body(
    status: int,
    category: str,
    retryable: bool,
) -> None:
    transport = _Transport(
        ProviderHttpResponse(
            status_code=status,
            headers={},
            json_body={"error": {"message": "secret provider body"}},
        )
    )

    with pytest.raises(ProviderFailure) as captured:
        DeepSeekProvider(transport=transport).invoke(
            _invocation(),
            api_key="secret-marker",
            base_url="https://api.deepseek.com",
        )

    assert captured.value.category == category
    assert captured.value.retryable is retryable
    assert "secret provider body" not in str(captured.value)
    assert "secret-marker" not in repr(captured.value)


def test_deepseek_sse_ignores_comments_and_keepalive_lines() -> None:
    lines = [
        ": keep-alive",
        "",
        "data: "
        + json.dumps(
            {"choices": [{"delta": {"content": "Hello"}}]},
            separators=(",", ":"),
        ),
        "data: "
        + json.dumps(
            {"choices": [{"delta": {"content": " world"}}]},
            separators=(",", ":"),
        ),
        "data: [DONE]",
    ]

    result = parse_deepseek_sse(lines)

    assert result.text == "Hello world"
    assert result.output_started is True

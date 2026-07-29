from __future__ import annotations

import json
import time
from typing import Any, Iterable, Mapping, Protocol

from .provider_client import (
    NormalizedToolCall,
    ProviderHttpResponse,
    ProviderInvocation,
    ProviderResult,
    ProviderTransportError,
)
from .provider_usage import ProviderUsage, normalize_deepseek_usage


class ProviderTransport(Protocol):
    def post_json(
        self,
        *,
        provider_type: str,
        base_url: str,
        path: str,
        api_key: str,
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> ProviderHttpResponse: ...


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        output_started: bool = False,
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.output_started = output_started
        super().__init__(category)


class DeepSeekProvider:
    def __init__(
        self,
        *,
        transport: ProviderTransport,
        clock=time.monotonic,
        timeout_seconds: float = 30,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))

    def invoke(
        self,
        invocation: ProviderInvocation,
        *,
        api_key: str,
        base_url: str,
    ) -> ProviderResult:
        started = self._clock()
        try:
            response = self._transport.post_json(
                provider_type="deepseek",
                base_url=base_url,
                path="/chat/completions",
                api_key=api_key,
                payload=_request_payload(invocation),
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderTransportError as exc:
            if exc.code == "provider_timeout":
                raise ProviderFailure("provider_timeout", retryable=True) from None
            if exc.code == "provider_transport_unavailable":
                raise ProviderFailure("provider_unavailable", retryable=True) from None
            raise ProviderFailure(exc.code, retryable=False) from None
        if response.status_code != 200:
            category, retryable = _error_category(response.status_code)
            raise ProviderFailure(
                category,
                retryable=retryable,
                status_code=response.status_code,
            )
        body = response.json_body if isinstance(response.json_body, Mapping) else {}
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, Mapping) else {}
        if not isinstance(message, Mapping):
            message = {}
        text = message.get("content")
        normalized_text = str(text) if text is not None else None
        tool_calls = _tool_calls(message.get("tool_calls"))
        usage = normalize_deepseek_usage(body.get("usage"))
        elapsed = max(0, int((self._clock() - started) * 1000))
        return ProviderResult(
            text=normalized_text,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=elapsed,
            output_started=bool(normalized_text or tool_calls),
        )


def parse_deepseek_sse(lines: Iterable[str]) -> ProviderResult:
    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    usage = ProviderUsage()
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            break
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            raise ProviderFailure("provider_stream_invalid", retryable=False) from None
        if not isinstance(value, Mapping):
            continue
        if isinstance(value.get("usage"), Mapping):
            usage = normalize_deepseek_usage(value["usage"])
        choices = value.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        delta = first.get("delta") if isinstance(first, Mapping) else {}
        if not isinstance(delta, Mapping):
            continue
        content = delta.get("content")
        if content is not None:
            text_parts.append(str(content))
        tool_calls.extend(_tool_calls(delta.get("tool_calls")))
    text = "".join(text_parts) or None
    return ProviderResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        output_started=bool(text or tool_calls),
    )


def _request_payload(invocation: ProviderInvocation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": invocation.model_id,
        "messages": [
            item.model_dump(mode="json", exclude_none=True)
            for item in invocation.messages
        ],
        "stream": invocation.stream,
    }
    if invocation.tools:
        payload["tools"] = invocation.tools
    if invocation.response_format:
        payload["response_format"] = invocation.response_format
    if invocation.temperature is not None:
        payload["temperature"] = invocation.temperature
    if invocation.max_tokens is not None:
        payload["max_tokens"] = invocation.max_tokens
    return payload


def _tool_calls(value: object) -> list[NormalizedToolCall]:
    if not isinstance(value, list):
        return []
    result: list[NormalizedToolCall] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function")
        if not isinstance(function, Mapping):
            continue
        call_id = str(item.get("id") or "").strip()
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments")
        if not call_id or not name:
            continue
        result.append(
            NormalizedToolCall(
                call_id=call_id,
                name=name,
                arguments=str(arguments or ""),
            )
        )
    return result


def _error_category(status_code: int) -> tuple[str, bool]:
    return {
        400: ("invalid_request", False),
        401: ("authentication_failed", False),
        402: ("insufficient_balance", False),
        422: ("invalid_parameters", False),
        429: ("rate_limited", True),
        500: ("provider_unavailable", True),
        503: ("provider_unavailable", True),
    }.get(status_code, ("provider_unavailable", status_code >= 500))


__all__ = [
    "DeepSeekProvider",
    "ProviderFailure",
    "ProviderHttpResponse",
    "ProviderTransport",
    "parse_deepseek_sse",
]

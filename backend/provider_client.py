from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ConfigDict, Field

from .provider_endpoint import validate_provider_endpoint
from .provider_usage import ProviderUsage


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(max_length=1_000_000)
    tool_call_id: str | None = Field(default=None, max_length=160)


class ProviderInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_ref: str = Field(min_length=1, max_length=160)
    correlation_ref: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    agent_id: str | None = Field(default=None, max_length=160)
    execution_kind: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=160)
    messages: list[ProviderMessage] = Field(min_length=1, max_length=256)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    response_format: dict[str, Any] | None = None
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=384_000)


class NormalizedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    arguments: str = Field(max_length=1_000_000)


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    tool_calls: list[NormalizedToolCall] = Field(default_factory=list)
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    latency_ms: int = Field(default=0, ge=0)
    output_started: bool = False
    safe_error_category: str | None = Field(default=None, max_length=64)


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    json_body: object


class ProviderTransportError(RuntimeError):
    code = "provider_transport_unavailable"

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(self.code)


class RequestsProviderTransport:
    def __init__(
        self,
        *,
        session: Any | None = None,
        resolver=socket.getaddrinfo,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self._session = session or requests.Session()
        self._resolver = resolver
        self._max_response_bytes = max(64, min(int(max_response_bytes), 8_000_000))

    def post_json(
        self,
        *,
        provider_type: str,
        base_url: str,
        path: str,
        api_key: str,
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        origin = validate_provider_endpoint(
            provider_type,
            base_url,
            resolver=self._resolver,
        )
        normalized_path = _request_path(path)
        url = f"{origin}{normalized_path}"
        try:
            response = self._session.request(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=dict(payload),
                timeout=max(1.0, min(float(timeout_seconds), 120.0)),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise ProviderTransportError("provider_timeout") from None
        except requests.RequestException:
            raise ProviderTransportError() from None
        if 300 <= int(response.status_code) < 400:
            raise ProviderTransportError("provider_redirect_rejected")
        content = bytes(getattr(response, "content", b"") or b"")
        if len(content) > self._max_response_bytes:
            raise ProviderTransportError("provider_response_too_large")
        try:
            body = response.json()
        except Exception:
            raise ProviderTransportError("provider_response_invalid") from None
        return ProviderHttpResponse(
            status_code=int(response.status_code),
            headers={
                str(key): str(value)
                for key, value in dict(getattr(response, "headers", {}) or {}).items()
            },
            json_body=body,
        )


def _request_path(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if (
        not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderTransportError("provider_path_invalid")
    return parsed.path


__all__ = [
    "NormalizedToolCall",
    "ProviderHttpResponse",
    "ProviderInvocation",
    "ProviderMessage",
    "ProviderResult",
    "ProviderTransportError",
    "RequestsProviderTransport",
]

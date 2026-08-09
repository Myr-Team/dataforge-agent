from __future__ import annotations

import socket
import ssl
import time
import uuid
from collections.abc import Callable
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from .deepseek_provider import (
    DeepSeekProvider,
    ProviderFailure,
    ProviderTransport,
)
from .model_providers import ProviderModel
from .provider_client import (
    ProviderHttpResponse,
    ProviderInvocation,
    ProviderMessage,
    ProviderTransportError,
)
from .provider_endpoint import ProviderEndpointError, validate_provider_endpoint


PROBE_STAGES = (
    "secret_read",
    "endpoint_resolution",
    "tls_connect",
    "provider_auth",
    "minimal_inference",
    "model_discovery",
)


class ConnectionProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_stage: str = Field(min_length=1, max_length=64)
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    safe_error_category: str | None = Field(default=None, max_length=64)
    models: list[ProviderModel] = Field(default_factory=list)


class _ProbeFailure(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class DeepSeekConnectionProbe:
    def __init__(
        self,
        *,
        transport: ProviderTransport,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        tls_probe: Callable[[str, int, float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._resolver = resolver
        self._tls_probe = tls_probe or _default_tls_probe
        self._clock = clock

    def run(
        self,
        *,
        api_key: str,
        base_url: str,
        secret_read_ms: int,
    ) -> ConnectionProbeResult:
        durations: dict[str, int] = {"secret_read": max(0, int(secret_read_ms))}
        current_stage = "endpoint_resolution"
        models: list[ProviderModel] = []
        try:
            origin = self._stage(
                durations,
                current_stage,
                lambda: validate_provider_endpoint(
                    "deepseek", base_url, resolver=self._resolver
                ),
            )
            parsed = urlparse(origin)
            current_stage = "tls_connect"
            self._stage(
                durations,
                current_stage,
                lambda: self._tls_probe(parsed.hostname or "", 443, 3.0),
            )
            current_stage = "provider_auth"
            balance = self._stage(
                durations,
                current_stage,
                lambda: self._transport.get_json(
                    provider_type="deepseek",
                    base_url=origin,
                    path="/user/balance",
                    api_key=api_key,
                    timeout_seconds=5.0,
                ),
            )
            _require_success(balance)
            balance_body = balance.json_body if isinstance(balance.json_body, Mapping) else {}
            if balance_body.get("is_available") is not True:
                raise _ProbeFailure("insufficient_balance")

            current_stage = "minimal_inference"
            self._stage(
                durations,
                current_stage,
                lambda: DeepSeekProvider(
                    transport=self._transport,
                    timeout_seconds=5.0,
                ).invoke(
                    _connection_test_invocation(),
                    api_key=api_key,
                    base_url=origin,
                ),
            )

            current_stage = "model_discovery"
            catalog = self._stage(
                durations,
                current_stage,
                lambda: self._transport.get_json(
                    provider_type="deepseek",
                    base_url=origin,
                    path="/models",
                    api_key=api_key,
                    timeout_seconds=5.0,
                ),
            )
            _require_success(catalog)
            models = _supported_models(catalog.json_body)
        except ProviderEndpointError as exc:
            return _failed(current_stage, durations, _endpoint_category(exc.code))
        except ProviderTransportError as exc:
            return _failed(current_stage, durations, _transport_category(exc.code))
        except ProviderFailure as exc:
            return _failed(current_stage, durations, exc.category)
        except _ProbeFailure as exc:
            return _failed(current_stage, durations, exc.category)
        except (OSError, ssl.SSLError, TimeoutError):
            return _failed(current_stage, durations, "provider_unavailable")
        except Exception:
            return _failed(current_stage, durations, "provider_unavailable")

        return ConnectionProbeResult(
            connection_stage="completed",
            stage_durations_ms=durations,
            models=models,
        )

    def _stage(
        self,
        durations: dict[str, int],
        stage: str,
        operation: Callable[[], Any],
    ) -> Any:
        started = self._clock()
        try:
            return operation()
        finally:
            durations[stage] = max(0, int((self._clock() - started) * 1000))


def _default_tls_probe(host: str, port: int, timeout_seconds: float) -> None:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout_seconds) as raw:
        with context.wrap_socket(raw, server_hostname=host):
            return None


def _connection_test_invocation() -> ProviderInvocation:
    return ProviderInvocation(
        request_ref=f"test_{uuid.uuid4().hex[:24]}",
        correlation_ref=f"test_{uuid.uuid4().hex[:24]}",
        workspace_id="provider-connection-test",
        agent_id=None,
        execution_kind="connection_test",
        model_id="deepseek-v4-flash",
        messages=[ProviderMessage(role="user", content="Reply with OK.")],
        max_tokens=1,
        thinking="disabled",
    )


def _require_success(response: ProviderHttpResponse) -> None:
    if response.status_code == 200:
        return
    category = {
        400: "invalid_request",
        401: "authentication_failed",
        402: "insufficient_balance",
        422: "invalid_parameters",
        429: "rate_limited",
    }.get(response.status_code, "provider_unavailable")
    raise _ProbeFailure(category)


def _supported_models(value: object) -> list[ProviderModel]:
    body = value if isinstance(value, Mapping) else {}
    raw_items = body.get("data")
    ids = {
        str(item.get("id") or "").strip()
        for item in raw_items if isinstance(item, Mapping)
    } if isinstance(raw_items, list) else set()
    capabilities = ["chat", "analysis", "tools", "json", "thinking"]
    known = (
        ("deepseek-v4-flash", "DeepSeek V4 Flash"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro"),
    )
    return [
        ProviderModel(
            model_id=model_id,
            display_name=display_name,
            capabilities=capabilities,
            support_state="supported",
            price_key=f"deepseek:{model_id}:official",
        )
        for model_id, display_name in known
        if model_id in ids
    ]


def _failed(
    stage: str,
    durations: dict[str, int],
    category: str,
) -> ConnectionProbeResult:
    return ConnectionProbeResult(
        connection_stage=stage,
        stage_durations_ms=durations,
        safe_error_category=category,
    )


def _endpoint_category(code: str) -> str:
    if code == "provider_endpoint_invalid":
        return "configuration_conflict"
    return "provider_unavailable"


def _transport_category(code: str) -> str:
    if code == "provider_timeout":
        return "provider_timeout"
    if code == "provider_transport_unavailable":
        return "provider_unavailable"
    return code


__all__ = [
    "ConnectionProbeResult",
    "DeepSeekConnectionProbe",
    "PROBE_STAGES",
]

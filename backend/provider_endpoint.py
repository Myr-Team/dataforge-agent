from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse


PROVIDER_HOSTS = {
    "deepseek": frozenset({"api.deepseek.com"}),
}


class ProviderEndpointError(ValueError):
    code = "provider_endpoint_invalid"

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(self.code)


def validate_provider_endpoint(
    provider_type: str,
    value: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    allowed_hosts = PROVIDER_HOSTS.get(str(provider_type or "").strip().lower())
    if not allowed_hosts:
        raise ProviderEndpointError("provider_type_unsupported")

    parsed = urlparse(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ProviderEndpointError()

    try:
        addresses = resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except Exception:
        raise ProviderEndpointError("provider_endpoint_dns_unavailable") from None
    if not addresses:
        raise ProviderEndpointError("provider_endpoint_dns_unavailable")

    for item in addresses:
        try:
            address = ipaddress.ip_address(item[4][0])
        except (IndexError, TypeError, ValueError):
            raise ProviderEndpointError("provider_endpoint_dns_invalid") from None
        if not address.is_global:
            raise ProviderEndpointError("provider_endpoint_private_address")

    return f"https://{parsed.hostname}"


__all__ = [
    "PROVIDER_HOSTS",
    "ProviderEndpointError",
    "validate_provider_endpoint",
]

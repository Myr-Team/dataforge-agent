from __future__ import annotations

import socket

import pytest

from backend.provider_endpoint import ProviderEndpointError, validate_provider_endpoint


def _public_resolver(host: str, port: int, **_: object):
    assert host == "api.deepseek.com"
    assert port == 443
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _private_resolver(host: str, port: int, **_: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port))]


def test_deepseek_endpoint_accepts_only_official_https_origin() -> None:
    assert (
        validate_provider_endpoint(
            "deepseek",
            "https://api.deepseek.com/",
            resolver=_public_resolver,
        )
        == "https://api.deepseek.com"
    )

    for value in (
        "http://api.deepseek.com",
        "https://user:pass@api.deepseek.com",
        "https://api.deepseek.com:8443",
        "https://api.deepseek.com?key=secret",
        "https://api.deepseek.com/#fragment",
        "https://api.deepseek.com.evil.example",
        "https://127.0.0.1",
        "https://169.254.169.254",
    ):
        with pytest.raises(ProviderEndpointError):
            validate_provider_endpoint("deepseek", value, resolver=_public_resolver)


def test_deepseek_endpoint_rejects_private_dns_resolution() -> None:
    with pytest.raises(ProviderEndpointError) as captured:
        validate_provider_endpoint(
            "deepseek",
            "https://api.deepseek.com",
            resolver=_private_resolver,
        )

    assert captured.value.code == "provider_endpoint_private_address"


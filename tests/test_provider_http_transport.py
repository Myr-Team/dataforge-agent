from __future__ import annotations

import socket

import pytest

from backend.provider_client import (
    ProviderTransportError,
    RequestsProviderTransport,
)


def _public_resolver(host: str, port: int, **_: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


class _Response:
    def __init__(
        self,
        status_code: int,
        body: object,
        *,
        headers: dict[str, str] | None = None,
        content: bytes = b"{}",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._body = body

    def json(self) -> object:
        return self._body


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def test_transport_sends_key_only_to_validated_official_origin() -> None:
    session = _Session(_Response(200, {"ok": True}))
    transport = RequestsProviderTransport(
        session=session,
        resolver=_public_resolver,
    )

    response = transport.post_json(
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        path="/chat/completions",
        api_key="secret-marker",
        payload={"model": "deepseek-v4-flash"},
        timeout_seconds=10,
    )

    assert response.status_code == 200
    call = session.calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["allow_redirects"] is False
    assert call["headers"]["Authorization"] == "Bearer secret-marker"


def test_transport_get_reuses_official_origin_and_redirect_guards() -> None:
    session = _Session(_Response(200, {"data": []}))
    transport = RequestsProviderTransport(
        session=session,
        resolver=_public_resolver,
    )

    response = transport.get_json(
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        path="/models",
        api_key="secret-marker",
        timeout_seconds=5,
    )

    assert response.status_code == 200
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.deepseek.com/models"
    assert call["allow_redirects"] is False
    assert call["headers"]["Authorization"] == "Bearer secret-marker"


def test_transport_rejects_redirects_without_forwarding_credentials() -> None:
    session = _Session(
        _Response(
            302,
            {},
            headers={"Location": "https://evil.example/collect"},
        )
    )
    transport = RequestsProviderTransport(
        session=session,
        resolver=_public_resolver,
    )

    with pytest.raises(ProviderTransportError) as captured:
        transport.post_json(
            provider_type="deepseek",
            base_url="https://api.deepseek.com",
            path="/chat/completions",
            api_key="secret-marker",
            payload={"model": "deepseek-v4-flash"},
            timeout_seconds=10,
        )

    assert captured.value.code == "provider_redirect_rejected"
    assert len(session.calls) == 1
    assert "evil.example" not in str(captured.value)
    assert "secret-marker" not in repr(captured.value)


def test_transport_rejects_oversized_provider_responses() -> None:
    session = _Session(
        _Response(
            200,
            {"ok": True},
            content=b"x" * 65,
        )
    )
    transport = RequestsProviderTransport(
        session=session,
        resolver=_public_resolver,
        max_response_bytes=64,
    )

    with pytest.raises(ProviderTransportError) as captured:
        transport.post_json(
            provider_type="deepseek",
            base_url="https://api.deepseek.com",
            path="/chat/completions",
            api_key="secret-marker",
            payload={"model": "deepseek-v4-flash"},
            timeout_seconds=10,
        )

    assert captured.value.code == "provider_response_too_large"

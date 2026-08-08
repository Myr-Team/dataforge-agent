from __future__ import annotations

import socket

import pytest

from backend.deepseek_provider import ProviderHttpResponse
from backend.provider_connection_probe import DeepSeekConnectionProbe


def _public_resolver(host: str, port: int, **_: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


class _Transport:
    def __init__(self, *, auth_status: int = 200) -> None:
        self.auth_status = auth_status
        self.calls: list[dict[str, object]] = []

    def get_json(self, **values: object) -> ProviderHttpResponse:
        self.calls.append(dict(values))
        path = values["path"]
        if path == "/user/balance":
            return ProviderHttpResponse(
                status_code=self.auth_status,
                headers={},
                json_body={"is_available": self.auth_status == 200},
            )
        return ProviderHttpResponse(
            status_code=200,
            headers={},
            json_body={
                "data": [
                    {"id": "deepseek-v4-flash"},
                    {"id": "not-priced-and-not-supported"},
                ]
            },
        )

    def post_json(self, **values: object) -> ProviderHttpResponse:
        self.calls.append(dict(values))
        return ProviderHttpResponse(
            status_code=200,
            headers={},
            json_body={"choices": [{"message": {"content": "OK"}}]},
        )


def _probe(*, auth_status: int = 200) -> tuple[DeepSeekConnectionProbe, _Transport]:
    transport = _Transport(auth_status=auth_status)
    probe = DeepSeekConnectionProbe(
        transport=transport,
        resolver=_public_resolver,
        tls_probe=lambda _host, _port, _timeout: None,
    )
    return probe, transport


def test_probe_returns_only_safe_stage_metadata_and_supported_models() -> None:
    probe, transport = _probe()

    result = probe.run(
        api_key="secret-value",
        base_url="https://api.deepseek.com",
        secret_read_ms=2,
    )

    assert result.connection_stage == "completed"
    assert list(result.stage_durations_ms) == [
        "secret_read",
        "endpoint_resolution",
        "tls_connect",
        "provider_auth",
        "minimal_inference",
        "model_discovery",
    ]
    assert [item.model_id for item in result.models] == ["deepseek-v4-flash"]
    assert "secret-value" not in result.model_dump_json()
    inference = next(call for call in transport.calls if call.get("path") == "/chat/completions")
    assert inference["payload"]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize(
    ("status", "category"),
    [(401, "authentication_failed"), (402, "insufficient_balance"), (429, "rate_limited")],
)
def test_probe_maps_provider_status_without_returning_body(
    status: int,
    category: str,
) -> None:
    probe, _transport = _probe(auth_status=status)

    result = probe.run(
        api_key="secret-value",
        base_url="https://api.deepseek.com",
        secret_read_ms=2,
    )

    assert result.connection_stage == "provider_auth"
    assert result.safe_error_category == category
    assert "secret-value" not in result.model_dump_json()

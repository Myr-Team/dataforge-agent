from backend import control_plane


def test_public_trace_projection_redacts_provider_credentials() -> None:
    value = control_plane._public_detail_projection(
        {
            "api_key": "secret-marker",
            "Authorization": "Bearer secret-marker",
            "access_token": "secret-marker",
            "model": "deepseek-chat",
        },
        depth=0,
    )

    assert value["api_key"] == "[redacted]"
    assert value["Authorization"] == "[redacted]"
    assert value["access_token"] == "[redacted]"
    assert value["model"] == "deepseek-chat"
    assert "secret-marker" not in str(value)

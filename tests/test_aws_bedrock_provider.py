from __future__ import annotations

import pytest

from backend.aws_bedrock_provider import (
    AwsBedrockCredential,
    bedrock_control_endpoint,
)


def test_bedrock_region_builds_server_owned_control_endpoint() -> None:
    assert bedrock_control_endpoint("ap-southeast-1") == (
        "https://bedrock.ap-southeast-1.amazonaws.com"
    )
    with pytest.raises(ValueError, match="bedrock_region_unsupported"):
        bedrock_control_endpoint("https://evil.example")


def test_bedrock_credential_serialization_is_write_only() -> None:
    value = AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker-123",
        session_token=None,
    )

    encoded = value.to_secret_value()

    assert AwsBedrockCredential.from_secret_value(encoded) == value
    assert "secret-marker" not in repr(value)

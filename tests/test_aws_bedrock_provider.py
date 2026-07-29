from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from backend.aws_bedrock_provider import (
    AwsBedrockCredential,
    BedrockConnectionFailure,
    Boto3BedrockControlPlane,
    bedrock_control_endpoint,
)


class _BedrockClient:
    def list_foundation_models(self):
        return {
            "modelSummaries": [{
                "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
                "modelName": "Claude Sonnet 4",
                "providerName": "Anthropic",
                "inputModalities": ["TEXT"],
                "outputModalities": ["TEXT"],
                "responseStreamingSupported": True,
            }]
        }


class _FailingBedrockClient:
    def __init__(self, error):
        self.error = error

    def list_foundation_models(self):
        raise self.error


def credential() -> AwsBedrockCredential:
    return AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker-value",
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


def test_bedrock_list_models_returns_non_routable_discovery() -> None:
    adapter = Boto3BedrockControlPlane(
        client_factory=lambda *_, **__: _BedrockClient(),
    )

    models = adapter.list_models("us-east-1", credential())

    assert models[0].model_id == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert models[0].capabilities == ["text", "streaming"]
    assert models[0].support_state == "unsupported"
    assert models[0].price_key is None


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("UnrecognizedClientException", "authentication_failed"),
        ("AccessDeniedException", "access_denied"),
        ("ThrottlingException", "throttled"),
        ("InternalServerException", "provider_unavailable"),
    ],
)
def test_bedrock_errors_are_mapped_without_raw_body(code, category) -> None:
    error = ClientError(
        {"Error": {"Code": code, "Message": "secret-marker raw provider body"}},
        "ListFoundationModels",
    )
    adapter = Boto3BedrockControlPlane(
        client_factory=lambda *_, **__: _FailingBedrockClient(error),
    )

    with pytest.raises(BedrockConnectionFailure) as exc:
        adapter.list_models("us-east-1", credential())

    assert exc.value.category == category
    assert "secret-marker" not in str(exc.value)

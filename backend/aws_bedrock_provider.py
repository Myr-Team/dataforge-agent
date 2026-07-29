from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .model_providers import ProviderModel


SUPPORTED_BEDROCK_REGIONS = frozenset({
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "eu-central-1",
    "eu-west-1",
    "us-east-1",
    "us-east-2",
    "us-west-2",
})


class AwsBedrockCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_key_id: str = Field(min_length=8, max_length=128, repr=False)
    secret_access_key: str = Field(min_length=16, max_length=256, repr=False)
    session_token: str | None = Field(default=None, max_length=4096, repr=False)

    def to_secret_value(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_secret_value(cls, value: str) -> "AwsBedrockCredential":
        return cls.model_validate_json(value)


def bedrock_control_endpoint(region: str) -> str:
    normalized = str(region or "").strip().lower()
    if normalized not in SUPPORTED_BEDROCK_REGIONS:
        raise ValueError("bedrock_region_unsupported")
    return f"https://bedrock.{normalized}.amazonaws.com"


class BedrockConnectionFailure(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _safe_bedrock_category(exc: Exception) -> str:
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)):
        return "timeout"
    code = ""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "")
    return {
        "UnrecognizedClientException": "authentication_failed",
        "InvalidSignatureException": "authentication_failed",
        "AccessDeniedException": "access_denied",
        "ValidationException": "configuration_conflict",
        "ThrottlingException": "throttled",
        "InternalServerException": "provider_unavailable",
        "ServiceUnavailableException": "provider_unavailable",
    }.get(code, "provider_unavailable")


def _bedrock_capabilities(item: Mapping[str, object]) -> list[str]:
    values = [
        str(value).strip().lower()
        for value in [
            *(item.get("inputModalities") or []),
            *(item.get("outputModalities") or []),
        ]
        if str(value).strip()
    ]
    if item.get("responseStreamingSupported") is True:
        values.append("streaming")
    return list(dict.fromkeys(values))


class AwsBedrockControlPlane(Protocol):
    def list_models(
        self,
        region: str,
        credential: AwsBedrockCredential,
    ) -> list["ProviderModel"]:
        raise NotImplementedError


class Boto3BedrockControlPlane:
    def __init__(self, client_factory: Callable[..., object] | None = None) -> None:
        self._client_factory = client_factory or _boto3_client

    def list_models(
        self,
        region: str,
        credential: AwsBedrockCredential,
    ) -> list["ProviderModel"]:
        from .model_providers import ProviderModel

        try:
            client = self._client_factory(
                "bedrock",
                region_name=region,
                aws_access_key_id=credential.access_key_id,
                aws_secret_access_key=credential.secret_access_key,
                aws_session_token=credential.session_token,
                config=Config(
                    connect_timeout=5,
                    read_timeout=10,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
            summaries = client.list_foundation_models().get("modelSummaries") or []
        except (ClientError, BotoCoreError) as exc:
            raise BedrockConnectionFailure(_safe_bedrock_category(exc)) from None
        return [
            ProviderModel(
                model_id=str(item["modelId"]),
                display_name=str(item.get("modelName") or item["modelId"]),
                capabilities=_bedrock_capabilities(item),
                support_state="unsupported",
                price_key=None,
            )
            for item in summaries[:64]
            if isinstance(item, dict) and str(item.get("modelId") or "").strip()
        ]


def _boto3_client(*args: object, **kwargs: object) -> object:
    return boto3.client(*args, **kwargs)


__all__ = [
    "AwsBedrockControlPlane",
    "AwsBedrockCredential",
    "BedrockConnectionFailure",
    "Boto3BedrockControlPlane",
    "SUPPORTED_BEDROCK_REGIONS",
    "bedrock_control_endpoint",
]

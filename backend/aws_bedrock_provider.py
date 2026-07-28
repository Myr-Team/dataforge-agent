from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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

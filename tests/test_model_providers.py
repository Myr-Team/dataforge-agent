from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.model_providers import (
    ModelProviderRecord,
    ProviderModel,
    ProviderPatch,
)


def _record() -> ModelProviderRecord:
    return ModelProviderRecord(
        provider_id="provider_01",
        tenant_ref="tenant-safe",
        provider_type="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        secret_ref="kv:must-never-leave-the-service",
        connection_state="connected",
        governance_state="pending",
        available_models=[
            ProviderModel(
                model_id="deepseek-v4-flash",
                display_name="DeepSeek V4 Flash",
                capabilities=["chat", "analysis", "tools", "json"],
                support_state="supported",
                price_key="deepseek:deepseek-v4-flash:official",
            )
        ],
        last_tested_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        last_success_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        revision=1,
        created_by_ref="actor-safe",
        updated_by_ref="actor-safe",
        created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )


def _bedrock_payload(**updates: object) -> dict[str, object]:
    payload = dict(_record().__dict__)
    payload.update(
        {
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "base_url": "https://bedrock.ap-southeast-1.amazonaws.com",
            "region": "ap-southeast-1",
        }
    )
    payload.update(updates)
    return payload


def test_provider_public_serialization_masks_internal_identity_and_secret() -> None:
    record = _record()

    public = record.public_payload()
    serialized = str(public)

    assert public["provider_id"] == "provider_01"
    assert public["secret_status"] == "stored"
    assert "tenant_ref" not in public
    assert "secret_ref" not in public
    assert "must-never-leave" not in serialized
    assert "must-never-leave" not in repr(record)


def test_provider_type_and_endpoint_are_server_bounded() -> None:
    payload = dict(_record().__dict__)
    payload["provider_type"] = "arbitrary"
    with pytest.raises(ValidationError):
        ModelProviderRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        ProviderPatch(base_revision=1, base_url="http://api.deepseek.com")


def test_provider_accepts_bedrock_type_and_optional_region() -> None:
    record = ModelProviderRecord.model_validate(_bedrock_payload())

    assert record.provider_type == "aws_bedrock"
    assert record.public_payload()["region"] == "ap-southeast-1"
    assert (
        ProviderPatch(
            base_revision=1,
            region="ap-southeast-1",
            base_url="https://bedrock.ap-southeast-1.amazonaws.com",
        ).region
        == "ap-southeast-1"
    )


def test_bedrock_record_rejects_untrusted_or_mismatched_control_endpoint() -> None:
    with pytest.raises(ValidationError, match="bedrock_control_endpoint_mismatch"):
        ModelProviderRecord.model_validate(
            _bedrock_payload(base_url="https://evil.example")
        )

    with pytest.raises(ValidationError, match="bedrock_region_unsupported"):
        ModelProviderRecord.model_validate(
            _bedrock_payload(
                region="moon-1",
                base_url="https://bedrock.moon-1.amazonaws.com",
            )
        )

    with pytest.raises(ValidationError, match="bedrock_control_endpoint_mismatch"):
        ModelProviderRecord.model_validate(
            _bedrock_payload(base_url="https://bedrock.us-west-2.amazonaws.com")
        )

    with pytest.raises(ValidationError, match="bedrock_control_endpoint_mismatch"):
        ProviderPatch(
            base_revision=1,
            region="ap-southeast-1",
            base_url="https://bedrock.us-west-2.amazonaws.com",
        )


def test_deepseek_record_remains_unaffected_by_bedrock_control_validation() -> None:
    record = ModelProviderRecord.model_validate(dict(_record().__dict__))

    assert record.provider_type == "deepseek"
    assert record.base_url == "https://api.deepseek.com"


def test_provider_patch_requires_a_positive_base_revision() -> None:
    with pytest.raises(ValidationError):
        ProviderPatch(base_revision=0, display_name="New name")

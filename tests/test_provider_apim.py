from __future__ import annotations

import pytest

from backend.provider_apim import (
    ApimCandidateVerification,
    ApimProviderCandidate,
    ProviderApimError,
    build_candidate_contract,
    candidate_is_activatable,
)


def _candidate(**overrides: object) -> ApimProviderCandidate:
    payload: dict[str, object] = {
        "provider_id": "provider-deepseek",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model_ids": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "revision": 4,
    }
    payload.update(overrides)
    return ApimProviderCandidate.model_validate(payload)


def test_candidate_contract_contains_only_server_owned_typed_values() -> None:
    contract = build_candidate_contract(_candidate())

    assert contract["provider_type"] == "deepseek"
    assert contract["backend_origin"] == "https://api.deepseek.com"
    assert contract["model_ids"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert contract["policy_hash"]
    assert "xml" not in contract
    assert "resource_id" not in contract
    assert "api_key" not in contract


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("provider_id", "/subscriptions/secret/resource"),
        ("base_url", "https://api.deepseek.com?policy=<xml/>"),
        ("model_ids", ["deepseek-v4-pro", "<set-backend-service />"]),
    ],
)
def test_candidate_rejects_resource_ids_xml_and_untrusted_endpoint(
    field_name: str,
    field_value: object,
) -> None:
    with pytest.raises((ValueError, ProviderApimError)):
        _candidate(**{field_name: field_value})


def test_activation_requires_full_candidate_verification_and_enabled_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = build_candidate_contract(_candidate())
    verification = ApimCandidateVerification(
        revision_created=True,
        managed_identity_status=200,
        anonymous_status=401,
        expected_policy_hash=contract["policy_hash"],
        observed_policy_hash=contract["policy_hash"],
        expected_etag='"revision-4"',
        observed_etag='"revision-4"',
        correlation_preserved=True,
        usage_preserved=True,
    )
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED", "0")
    assert not candidate_is_activatable(verification)

    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED", "1")
    assert candidate_is_activatable(verification)

    assert not candidate_is_activatable(
        verification.model_copy(update={"anonymous_status": 200})
    )

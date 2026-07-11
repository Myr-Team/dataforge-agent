from types import SimpleNamespace

import pytest

import backend.foundry_client as foundry_client
import backend.feasibility_rubric as feasibility_rubric
import backend.orchestrator as orchestrator


class _TransientError(RuntimeError):
    status_code = 429


class _BadRequestError(RuntimeError):
    status_code = 400


def test_non_streaming_foundry_call_retries_transient_error(monkeypatch) -> None:
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise _TransientError("rate limited")
            return SimpleNamespace()

    monkeypatch.setattr(foundry_client.time, "sleep", lambda delay: None)

    response = foundry_client._responses_create_with_retry(SimpleNamespace(responses=Responses()), model="test")

    assert response._dataforge_retry_attempts == 1
    assert len(calls) == 2


def test_foundry_call_does_not_retry_business_4xx(monkeypatch) -> None:
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise _BadRequestError("invalid request")

    monkeypatch.setattr(foundry_client.time, "sleep", lambda delay: None)

    with pytest.raises(_BadRequestError):
        foundry_client._responses_create_with_retry(SimpleNamespace(responses=Responses()), model="test")

    assert len(calls) == 1


def test_openai_client_prefers_configured_azure_openai_key(monkeypatch) -> None:
    calls = []

    class AzureClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(foundry_client, "AzureOpenAI", AzureClient, raising=False)
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    monkeypatch.setattr(foundry_client, "_project_client", lambda: (_ for _ in ()).throw(AssertionError("project client should not be used")))

    client = foundry_client._openai_client()

    assert isinstance(client, AzureClient)
    assert calls == [
        {
            "azure_endpoint": "https://example.openai.azure.com/",
            "api_key": "test-key",
            "api_version": "2025-04-01-preview",
            "max_retries": 0,
        }
    ]


def test_project_client_adapter_uses_direct_openai_client_when_key_is_available(monkeypatch) -> None:
    direct = object()

    monkeypatch.setattr(foundry_client, "_configured_azure_openai_client", lambda: direct)

    project_client = foundry_client._project_client()

    assert project_client.get_openai_client() is direct


def test_producer_keeps_pdf_when_concept_image_fails(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "workspace_reference_images", lambda workspace_id: [])
    monkeypatch.setattr(
        orchestrator,
        "_proposal_payload",
        lambda artifact: {"opportunity_id": "test-opportunity", "title": "Test opportunity"},
    )
    monkeypatch.setattr(orchestrator, "_image_prompt_from_proposal", lambda proposal: "test image")
    monkeypatch.setattr(orchestrator, "_proposal_image_kind", lambda proposal: ("service", "test"))
    monkeypatch.setattr(orchestrator, "_reference_image_urls", lambda references: [])
    monkeypatch.setattr(orchestrator, "render_pdf_report", lambda proposal, template: {"artifact_url": "/api/artifacts/test.pdf"})

    def image_failure(*args, **kwargs):
        raise RuntimeError("image generation failed")

    monkeypatch.setattr(orchestrator, "generate_image", image_failure)

    result = orchestrator._run_producer({"workspace_id": "ws-resilience"}, ["pdf", "concept_image"])

    assert result["artifact_urls"]["pdf"] == "/api/artifacts/test.pdf"
    assert result["degraded"] is True
    assert result["warnings"][0]["kind"] == "concept_image"
    assert result["warnings"][0]["message"] == "\u6982\u5ff5\u56fe\u751f\u6210\u5931\u8d25\uff0c\u5efa\u8bae\u4e66\u5df2\u751f\u6210\u3002"


def test_dimension_only_audit_correction_is_exposed_as_downgrade_event() -> None:
    artifact = {
        "_blind_feasibility": {
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "dimensions": [{"name": "asset_data", "score": 3.0}],
        },
        "feasibility": {
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "dimensions": [{"name": "asset_data", "score": 1.0}],
        },
    }

    contract = feasibility_rubric.finalize_verdict_contract(
        artifact,
        {"verdict": "revise", "issues": ["Evidence quality requires a conservative data-sufficiency score."]},
    )

    assert contract["downgrade"]["kind"] == "dimension"
    assert contract["downgrade"]["dimension"]
    assert contract["downgrade"]["score_before"] == 3.0
    assert contract["downgrade"]["score_after"] == 1.0
    assert artifact["verdict_downgrade"] == contract["downgrade"]

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend import evidence_bundle
from backend.evidence_bundle import (
    MAX_BUNDLE_GAPS,
    MAX_CAPABILITY_PACK_IDS,
    BundleLimits,
    EvidenceBundle,
    build_evidence_bundle,
    bundle_for_agent,
)
from backend.maf_team_runtime import AuthoritativeCorpus
from backend.schemas import Evidence


def corpus_with_duplicates() -> AuthoritativeCorpus:
    return AuthoritativeCorpus.model_validate(
        {
            "hits": [
                {
                    "id": "workspace:retention:1",
                    "source_file": "retention.csv",
                    "chunk_id": "row-1",
                    "content": "Retention improved after the intervention. " * 12,
                },
                {
                    "id": "workspace:retention:1",
                    "source_file": "retention.csv",
                    "chunk_id": "row-1",
                    "content": "Duplicate retrieval hit. " * 20,
                },
            ],
            "profile": {
                "workspace_id": "workspace-1",
                "asset_evidence": [
                    {
                        "source_type": "corpus",
                        "ref": "workspace:retention:1",
                        "quote": "Retention improved after the intervention. " * 12,
                    }
                ],
                "summary": "The workspace contains observed operational evidence.",
            },
        }
    )


def route() -> dict[str, str]:
    return {"workspace_id": "workspace-1", "intent": "feasibility_analysis"}


def test_bundle_deduplicates_refs_and_bounds_quote_bytes() -> None:
    bundle = build_evidence_bundle(
        corpus_with_duplicates(),
        route(),
        [],
        BundleLimits(max_items=8, max_quote_chars=240),
    )

    assert len({item.ref for item in bundle.evidence}) == len(bundle.evidence)
    assert all(len(item.quote or "") <= 240 for item in bundle.evidence)
    assert bundle.workspace_id == "workspace-1"
    assert bundle.fingerprint


def test_agent_view_is_bounded_and_persisted_metadata_excludes_quotes() -> None:
    bundle = build_evidence_bundle(
        corpus_with_duplicates(),
        route(),
        [{"id": "market-lookup"}],
        BundleLimits(max_items=1, max_quote_chars=80, max_profile_facts=1),
    )

    view = bundle_for_agent(bundle, "df-feasibility-analyst")
    metadata = bundle.persisted_metadata()

    assert len(view["evidence"]) == 1
    assert view["fingerprint"] == bundle.fingerprint
    assert metadata == {
        "fingerprint": bundle.fingerprint,
        "evidence_count": 1,
        "profile_fact_count": 1,
        "gap_count": 0,
            "capability_pack_ids": [],
    }
    assert "quote" not in repr(metadata)


def test_bundle_caps_gaps_and_capability_pack_ids() -> None:
    corpus = AuthoritativeCorpus.model_validate(
        {
            "profile": {
                "gaps_observed": [f"gap-{index}" for index in range(MAX_BUNDLE_GAPS + 8)],
            }
        }
    )
    packs = [{"id": f"pack-{index}"} for index in range(MAX_CAPABILITY_PACK_IDS + 8)]

    bundle = build_evidence_bundle(corpus, route(), packs)

    assert len(bundle.gaps) == MAX_BUNDLE_GAPS
    assert bundle.capability_pack_ids == []
    assert bundle.persisted_metadata()["capability_pack_ids"] == []
    with pytest.raises(ValidationError):
        EvidenceBundle(
            workspace_id="workspace-1",
            fingerprint="f" * 64,
            evidence=[],
            profile_facts=[],
            gaps=[f"gap-{index}" for index in range(MAX_BUNDLE_GAPS + 1)],
            capability_pack_ids=[],
        )


def test_bundle_for_agent_applies_role_allowlists() -> None:
    bundle = EvidenceBundle(
        workspace_id="workspace-1",
        fingerprint="f" * 64,
        evidence=[
            Evidence(source_type="corpus", ref="corpus:1", quote="internal evidence"),
            Evidence(source_type="market", ref="market:1", quote="external evidence"),
            Evidence(source_type="computed", ref="computed:1", quote="derived evidence"),
        ],
        profile_facts=["profile: confidential operating fact"],
        gaps=["gap: verify coverage"],
        capability_pack_ids=["corpus-search", "market-lookup"],
    )

    corpus_view = bundle_for_agent(bundle, "df-corpus-analyst")
    market_view = bundle_for_agent(bundle, "df-market-researcher")
    producer_view = bundle_for_agent(bundle, "df-producer")

    assert {item["source_type"] for item in corpus_view["evidence"]} == {"corpus", "computed"}
    assert corpus_view["profile_facts"] == ["profile: confidential operating fact"]
    assert market_view["profile_facts"] == []
    assert {item["source_type"] for item in market_view["evidence"]} == {"corpus", "computed"}
    assert producer_view["evidence"] == [
        {"source_type": "computed", "ref": "computed:1", "quote": "derived evidence"}
    ]
    assert producer_view["profile_facts"] == []
    assert producer_view["capability_pack_ids"] == []
    with pytest.raises(ValueError, match="unsupported agent_id"):
        bundle_for_agent(bundle, "df-unknown")


def test_bundle_fingerprint_is_stable_for_equivalent_input_ordering() -> None:
    first = AuthoritativeCorpus.model_validate(
        {
            "hits": [
                {"id": "workspace:b", "content": "second"},
                {"id": "workspace:a", "content": "first"},
            ],
            "profile": {
                "summary": "summary",
                "owner": "owner",
                "gaps_observed": ["gap-b", "gap-a"],
            },
        }
    )
    second = AuthoritativeCorpus.model_validate(
        {
            "hits": [
                {"id": "workspace:a", "content": "first"},
                {"id": "workspace:b", "content": "second"},
            ],
            "profile": {
                "gaps_observed": ["gap-a", "gap-b"],
                "owner": "owner",
                "summary": "summary",
            },
        }
    )

    first_bundle = build_evidence_bundle(first, route(), [{"id": "pack-b"}, {"id": "pack-a"}])
    second_bundle = build_evidence_bundle(second, route(), [{"id": "pack-a"}, {"id": "pack-b"}])

    assert first_bundle.fingerprint == second_bundle.fingerprint
    assert first_bundle.model_dump() == second_bundle.model_dump()


def test_public_event_projection_drops_unknown_nested_conversation_payloads() -> None:
    payload = {
        "opaque": {"anything": {"prompt": "secret", "token": "Bearer hidden"}},
        "conversation_route": {"mode": "followup", "reason": "raw rationale"},
    }

    assert evidence_bundle.public_conversation_event("followup", payload, "conv-1") == {
        "conversation_route": {
            "mode": "followup",
            "reason": "Follow-up",
            "evidence_required": False,
        }
    }


def test_public_event_projection_preserves_only_typed_user_facing_fields() -> None:
    marker = "raw-provider-secret"
    final = evidence_bundle.public_conversation_event(
        "final",
        {
            "text": "Customer-facing answer",
            "artifact": {
                "workspace_id": "workspace-1",
                "answer": {
                    "text": "Customer-facing answer",
                    "markdown": "## Answer",
                    "_llm": {"mode": "safe", "rationale": marker},
                },
                "opaque_container": {
                    "anything": {
                        "prompt": marker,
                        "claims": {"email": "person@example.com"},
                    }
                },
            },
            "provider_error": marker,
        },
        "conv-1",
    )

    assert final["text"] == "Customer-facing answer"
    assert final["artifact"]["answer"] == {
        "text": "Customer-facing answer",
        "markdown": "## Answer",
        "_llm": {"mode": "safe"},
    }
    assert marker not in repr(final)
    assert "opaque_container" not in final["artifact"]
    assert "provider_error" not in final


def test_sanitize_conversation_metadata_drops_unknown_nested_values() -> None:
    value = {
        "conversation_route": {"mode": "followup", "reason": "private rationale"},
        "opaque_container": {
            "other_name": {
                "prompt_text": "raw user prompt",
                "hidden": "Bearer opaque-credential-value",
            }
        },
        "unknown_scalar": "model rationale",
        "feasibility": {
            "dimensions": [
                {
                    "name": "market",
                    "rationale": "private chain-of-thought",
                    "evidence": [{"id": "e-1", "quote": "public evidence"}],
                }
            ],
            "gap_list": ["private missing-information rationale"],
        },
    }

    assert evidence_bundle.sanitize_conversation_metadata(value) == {
        "conversation_route": {
            "mode": "followup",
            "reason": "Follow-up",
            "evidence_required": False,
        },
        "feasibility": {
            "dimensions": [
                {
                    "name": "market",
                    "evidence": [{"id": "e-1", "quote": "public evidence"}],
                }
            ]
        },
    }


def test_public_artifact_projection_keeps_only_explicit_artifact_contract() -> None:
    artifact = {
        "workspace_id": "ws",
        "answer": {"markdown": "public answer", "raw_prompt": "secret"},
        "opaque": {"email": "private@example.com"},
    }

    projected = evidence_bundle.public_artifact_projection(artifact, {"workspace_id": "ws"})

    assert projected["answer"] == {"markdown": "public answer"}
    assert "opaque" not in projected


def test_public_artifact_projection_drops_claims_gaps_and_rationale() -> None:
    marker = "private analysis"
    artifact = {
        "workspace_id": "ws",
        "feasibility": {
            "verdict": "conditional",
            "rationale": marker,
            "gap_list": [marker],
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 3,
                    "confidence": "data_confirmed",
                    "rationale": marker,
                    "evidence": [
                        {
                            "id": "e-1",
                            "quote": "observed metric",
                            "claim": marker,
                            "gaps": [marker],
                        }
                    ],
                }
            ],
        },
        "corpus": {
            "hits": [{"id": "h-1", "quote": "observed row", "claim": marker}],
            "gaps_observed": [marker],
        },
        "market": {
            "evidence": [{"id": "m-1", "quote": "public market source", "claim": marker}],
            "gaps": [marker],
        },
        "rationale": marker,
    }

    projected = evidence_bundle.public_artifact_projection(artifact, {"workspace_id": "ws"})

    assert marker not in repr(projected)
    assert projected["feasibility"]["dimensions"][0] == {
        "name": "asset_data",
        "score": 3,
        "confidence": "data_confirmed",
        "evidence": [{"id": "e-1", "quote": "observed metric"}],
    }


def test_public_artifact_projection_preserves_verified_capability_pack_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_CAPABILITY_PACK_SIGNING_KEY", "task-1a-test-signing-key")
    scope = {"workspace_id": "ws", "scope_id": "run-1"}
    records, provenance = evidence_bundle.internally_selected_capability_pack_contract(
        {
            "goal": "choose channels for demand coverage",
            "schema_profile": {
                "schema_roles": ["location", "candidate", "demand", "time"],
                "metric_families": ["footfall", "conversion", "cost"],
                "temporal_coverage": {"available": True, "periods": 8},
                "entity_relationships": ["location_to_demand"],
            },
            "quality": {"completeness": 0.94, "duplicate_rate": 0.01},
        },
        scope,
    )
    assert records
    persisted = evidence_bundle.sanitize_conversation_metadata(
        {
            "workspace_id": "ws",
            "capability_packs": records,
            "capability_pack_provenance": provenance,
        }
    )

    projected = evidence_bundle.public_artifact_projection(
        persisted,
        scope,
    )

    assert projected["capability_packs"] == records
    assert projected["capability_pack_ids"] == ["site_channel_selection"]
    assert projected["capability_pack_integrity"] == {
        "status": "verified",
        "source": "normalized_goal_schema_profile_quality",
        "version": "2",
    }
    assert "capability_pack_provenance" not in projected


def test_public_artifact_projection_keeps_final_verdict_and_feasibility_contract() -> None:
    artifact = {
        "workspace_id": "ws",
        "verdict": "insufficient_evidence",
        "feasibility": {
            "verdict": "insufficient_evidence",
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 2,
                    "confidence": "data_confirmed",
                    "evidence": [{"ref": "asset.csv#row-1", "quote": "observed row"}],
                    "rationale": "private",
                }
            ],
        },
    }

    projected = evidence_bundle.public_artifact_projection(artifact, {"workspace_id": "ws"})

    assert projected == {
        "workspace_id": "ws",
        "verdict": "insufficient_evidence",
        "feasibility": {
            "verdict": "insufficient_evidence",
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 2,
                    "confidence": "data_confirmed",
                    "evidence": [{"ref": "asset.csv#row-1", "quote": "observed row"}],
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/report",
        "https://user:password@example.com/report",
        "https://example.com/report?api_key=secret",
        "https://example.com/report?Signature=secret",
        "https://example.com/report?credential_hint=secret",
    ],
)
def test_public_artifact_projection_drops_unsafe_urls(url: str) -> None:
    projected = evidence_bundle.public_artifact_projection(
        {"proposal": {"artifact_urls": {"pdf": url}}}
    )

    assert projected == {"proposal": {"artifact_urls": {}}}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sanitize_conversation_metadata_drops_non_finite_usage_without_raising(value: float) -> None:
    projected = evidence_bundle.sanitize_conversation_metadata(
        {"answer": {"markdown": "public", "_llm": {"usage": {"input_tokens": value}}}}
    )

    assert projected == {"answer": {"markdown": "public", "_llm": {}}}

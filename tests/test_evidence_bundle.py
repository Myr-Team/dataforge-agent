from __future__ import annotations

import pytest
from pydantic import ValidationError

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

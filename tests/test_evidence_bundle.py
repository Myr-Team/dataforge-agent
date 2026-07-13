from __future__ import annotations

from backend.evidence_bundle import BundleLimits, build_evidence_bundle, bundle_for_agent
from backend.maf_team_runtime import AuthoritativeCorpus


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
        "capability_pack_ids": ["market-lookup"],
    }
    assert "quote" not in repr(metadata)

from __future__ import annotations

import json

import pytest

from backend.capability_packs import load_capability_packs, select_capability_packs


def quality(*, completeness: float = 0.94, duplicates: float = 0.01) -> dict[str, float]:
    return {"completeness": completeness, "duplicate_rate": duplicates}


@pytest.mark.parametrize(
    ("goal", "schema_profile", "expected_pack"),
    [
        (
            "reduce churn and improve renewal",
            {
                "schema_roles": ["customer", "account", "event", "time"],
                "metric_families": ["retention", "engagement", "revenue"],
                "temporal_coverage": {"available": True, "periods": 12},
                "entity_relationships": ["customer_to_event"],
            },
            "growth_retention",
        ),
        (
            "test packaging and price sensitivity",
            {
                "schema_roles": ["product", "customer", "transaction", "time"],
                "metric_families": ["price", "conversion", "revenue"],
                "temporal_coverage": {"available": True, "periods": 6},
                "entity_relationships": ["customer_to_transaction"],
            },
            "pricing_productization",
        ),
        (
            "choose channels for demand coverage",
            {
                "schema_roles": ["location", "candidate", "demand", "time"],
                "metric_families": ["footfall", "conversion", "cost"],
                "temporal_coverage": {"available": True, "periods": 8},
                "entity_relationships": ["location_to_demand"],
            },
            "site_channel_selection",
        ),
        (
            "reduce process delays and utilization loss",
            {
                "schema_roles": ["process", "resource", "event", "time"],
                "metric_families": ["cycle_time", "throughput", "utilization"],
                "temporal_coverage": {"available": True, "periods": 20},
                "entity_relationships": ["process_to_resource"],
            },
            "operations",
        ),
        (
            "validate campaign response and service adoption",
            {
                "schema_roles": ["audience", "interaction", "campaign", "time"],
                "metric_families": ["reach", "engagement", "response"],
                "temporal_coverage": {"available": True, "periods": 4},
                "entity_relationships": ["audience_to_interaction"],
            },
            "campaign_service",
        ),
        (
            "assess controls and data readiness",
            {
                "schema_roles": ["record", "policy", "event", "time"],
                "metric_families": ["coverage", "exception", "quality"],
                "temporal_coverage": {"available": True, "periods": 3},
                "entity_relationships": ["record_to_policy"],
            },
            "risk_data_readiness",
        ),
    ],
)
def test_semantic_schema_shapes_select_the_matching_generic_pack(
    goal: str, schema_profile: dict[str, object], expected_pack: str
) -> None:
    selections = select_capability_packs(goal, schema_profile, quality())

    assert selections[0].pack_id == expected_pack
    assert 0 < selections[0].confidence <= 1
    assert selections[0].reasons


def test_renamed_workspace_and_file_metadata_do_not_change_pack_selection() -> None:
    schema_profile = {
        "schema_roles": ["location", "candidate", "demand", "time"],
        "metric_families": ["footfall", "conversion", "cost"],
        "temporal_coverage": {"available": True, "periods": 8},
        "entity_relationships": ["location_to_demand"],
        "workspace_name": "alpha",
        "file_name": "raw-input.csv",
        "dataset_name": "first-upload",
    }
    renamed_profile = {
        **schema_profile,
        "workspace_name": "unrelated-renamed-workspace",
        "file_name": "different-label.xlsx",
        "dataset_name": "not-a-domain-signal",
    }

    assert select_capability_packs("choose channels", schema_profile, quality()) == select_capability_packs(
        "choose channels", renamed_profile, quality()
    )


def test_unknown_or_weak_evidence_returns_data_readiness_without_an_opportunity_pack() -> None:
    selections = select_capability_packs(
        "explore a possible decision",
        {
            "schema_roles": ["record"],
            "metric_families": [],
            "temporal_coverage": {"available": False, "periods": 0},
            "entity_relationships": [],
        },
        quality(completeness=0.35, duplicates=0.5),
    )

    assert [selection.pack_id for selection in selections] == ["risk_data_readiness"]
    assert selections[0].missing_evidence


@pytest.mark.parametrize(
    "invalid_quality",
    [
        "not-a-mapping",
        {"completeness": 0.94, "missing_pct": "unknown", "duplicate_pct": 0},
        {"missing_pct": 6, "duplicate_pct": "unknown"},
        {"missing_pct": 6, "duplicate_pct": 0, "duplicate_rate": float("nan")},
    ],
)
def test_malformed_quality_degrades_to_data_readiness_without_raising(
    invalid_quality: object,
) -> None:
    selections = select_capability_packs("choose channels", _site_profile(), invalid_quality)

    assert [selection.pack_id for selection in selections] == ["risk_data_readiness"]
    assert "data quality" in selections[0].missing_evidence


@pytest.mark.parametrize(
    "invalid_temporal_coverage",
    [
        "available",
        {"available": "true", "periods": 8},
        {"available": True, "periods": "8"},
        {"available": False, "periods": 8},
        {"available": True, "periods": 0},
        {"available": True, "evidence": [{"start": "raw-file-name", "end": "other-file-name"}]},
        {"available": True, "evidence": [{"start": True, "end": 2}]},
        {"available": True, "evidence": [{"start": "2026-01-01T00:00:00", "end": "2026-03-31T00:00:00Z"}]},
    ],
)
def test_malformed_temporal_coverage_degrades_to_data_readiness_without_raising(
    invalid_temporal_coverage: object,
) -> None:
    profile = _site_profile()
    profile["temporal_coverage"] = invalid_temporal_coverage

    selections = select_capability_packs("choose channels", profile, quality())

    assert [selection.pack_id for selection in selections] == ["risk_data_readiness"]
    assert "time coverage" in selections[0].missing_evidence


def test_conflicting_period_aliases_degrade_to_data_readiness() -> None:
    profile = _site_profile()
    profile["temporal_coverage"] = {"available": True, "periods": 2, "count": 8}

    selections = select_capability_packs("choose channels", profile, quality())

    assert [selection.pack_id for selection in selections] == ["risk_data_readiness"]
    assert "time coverage" in selections[0].missing_evidence


def test_valid_temporal_evidence_can_supply_coverage_without_a_period_count() -> None:
    profile = _site_profile()
    profile["temporal_coverage"] = {
        "available": True,
        "evidence": [{"start": "2026-01-01", "end": "2026-03-31"}],
    }

    assert select_capability_packs("choose channels", profile, quality())[0].pack_id == "site_channel_selection"


def test_raw_columns_names_types_and_families_cannot_supply_semantic_profile_fields() -> None:
    adversarial_profile = {
        "schema_roles": [{"name": "location", "type": "candidate"}],
        "metric_families": [{"family": "footfall", "name": "conversion"}],
        "entity_relationships": [{"type": "location_to_demand"}],
        "temporal_coverage": {"available": True, "periods": 8},
        "columns": [{"name": "location", "type": "demand", "value": "footfall"}],
        "dataset_name": "site-channel-selection",
        "file_name": "location_candidates.csv",
    }

    selections = select_capability_packs("choose channels", adversarial_profile, quality())

    assert [selection.pack_id for selection in selections] == ["risk_data_readiness"]


def test_entity_relationships_contribute_to_the_matching_pack_confidence() -> None:
    base_profile = {
        "schema_roles": ["location", "candidate", "demand", "time"],
        "metric_families": ["footfall", "conversion", "cost"],
        "temporal_coverage": {"available": True, "periods": 8},
        "entity_relationships": [],
    }
    linked_profile = {**base_profile, "entity_relationships": ["location_to_demand"]}

    base = select_capability_packs("choose channels", base_profile, quality())[0]
    linked = select_capability_packs("choose channels", linked_profile, quality())[0]

    assert linked.pack_id == base.pack_id == "site_channel_selection"
    assert linked.confidence > base.confidence


def test_chinese_business_goal_concepts_use_the_same_semantic_selection() -> None:
    selections = select_capability_packs(
        "\u6839\u636e\u5019\u9009\u70b9\u4f4d\u548c\u9700\u6c42\u8986\u76d6\u505a\u9009\u5740\u5224\u65ad",
        {
            "schema_roles": ["location", "candidate", "demand", "time"],
            "metric_families": ["footfall", "conversion", "cost"],
            "temporal_coverage": {"available": True, "periods": 8},
            "entity_relationships": ["location_to_demand"],
        },
        quality(),
    )

    assert selections[0].pack_id == "site_channel_selection"
    assert any("business-goal concepts" in reason for reason in selections[0].reasons)


def test_pack_files_are_data_only_and_contain_no_scores_or_named_winners() -> None:
    packs = load_capability_packs()

    assert len(packs) == 6
    for pack in packs:
        text = json.dumps(pack.model_dump(), ensure_ascii=False).lower()
        assert "weighted_score" not in text
        assert "recommended_winner" not in text
        assert "winner" not in text
        assert "industry" not in text
        assert "score" not in text


def _site_profile() -> dict[str, object]:
    return {
        "schema_roles": ["location", "candidate", "demand", "time"],
        "metric_families": ["footfall", "conversion", "cost"],
        "temporal_coverage": {"available": True, "periods": 8},
        "entity_relationships": ["location_to_demand"],
    }

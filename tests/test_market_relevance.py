from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend import orchestrator
from backend.market_relevance import accepted_market_sources, assess_market_comparison, public_market_comparison
from backend.schemas import GatedMarketComparison, MarketQueryPlan, MarketSourceAssessment


def market_comparison(name: str, positioning: str, url: str, **extra: str) -> dict:
    return {
        "opportunity_id": "retail-location-intelligence",
        "competitors": [
            {
                "name": name,
                "positioning": positioning,
                "url": url,
                **extra,
            }
        ],
        "positioning_note": "Compare only source-backed direct competitors.",
    }


@pytest.mark.parametrize(
    ("name", "positioning", "url", "title", "snippet"),
    [
        ("Strava", "athlete route and workout analytics", "https://strava.com", "Strava | Run, Bike, Hike", "Track workouts and athlete routes."),
        ("TrainingPeaks", "training plans for endurance athletes", "https://trainingpeaks.com", "TrainingPeaks", "Plan and analyze athlete workouts."),
        ("Garmin Connect", "fitness activity and wearable insights", "https://connect.garmin.com", "Garmin Connect", "Track health and fitness activities."),
        ("Nix Biosensors", "hydration biosensors for athletes", "https://nixbiosensors.com", "Nix Hydration Biosensor", "Personal hydration data for athletes."),
    ],
)
def test_unrelated_fitness_products_cannot_enter_location_intelligence_competitors(
    name: str,
    positioning: str,
    url: str,
    title: str,
    snippet: str,
) -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(name, positioning, url, title=title, snippet=snippet),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["reasons"]
    assert result["market_evidence_status"] == "unavailable"
    assert accepted_market_sources(result) == []


def test_direct_site_selection_vendor_is_accepted_with_lineage() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(
            "Vendor",
            "retail site selection and footfall analytics",
            "https://vendor.example",
            title="Vendor retail site selection platform",
            snippet="Compare sites with footfall and dwell-time intelligence.",
            retrieval_query="retail location intelligence direct competitors",
        ),
    )

    relevance = result["competitors"][0]["relevance"]
    assert relevance["verdict"] == "accepted"
    assert relevance["query_purpose"] == "direct_competitor"
    assert relevance["matched_terms"]
    assert result["market_evidence_status"] == "available"


def test_generated_positioning_cannot_override_unrelated_source_owned_evidence() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(
            "Strava",
            "A retail location intelligence competitor for footfall and dwell-time decisions.",
            "https://strava.com",
            title="Strava | Run, Bike, Hike",
            snippet="Track workouts, athlete routes, and fitness activity.",
        ),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["verdict"] == "rejected"


def test_stamped_retrieval_query_cannot_make_unrelated_strava_source_relevant() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(
            "Strava",
            "athlete route and workout analytics",
            "https://strava.com",
            title="Strava | Run, Bike, Hike",
            snippet="Track workouts, athlete routes, and fitness activity.",
            retrieval_query="retail location intelligence footfall dwell time competitors",
        ),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["verdict"] == "rejected"
    assert result["rejected_sources"][0]["relevance"]["matched_terms"] == []


def test_forged_name_and_positioning_without_source_evidence_fail_closed() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(
            "Retail Location Intelligence Footfall Platform",
            "Direct competitor platform for retail site selection, footfall, and dwell-time analytics.",
            "https://example.invalid",
        ),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["verdict"] == "rejected"
    assert result["rejected_sources"][0]["relevance"]["matched_terms"] == []


def test_chinese_source_requires_opportunity_overlap_and_generic_directness() -> None:
    accepted = assess_market_comparison(
        opportunity="门店选址客流停留时长分析",
        evidence_digest="候选门店客流和停留时长数据",
        comparison=market_comparison(
            "选址平台",
            "门店选址客流分析解决方案",
            "https://example.cn/site-selection",
            title="门店选址客流分析平台",
            snippet="提供客流和停留时长分析服务。",
        ),
    )
    rejected = assess_market_comparison(
        opportunity="门店选址客流停留时长分析",
        evidence_digest="候选门店客流和停留时长数据",
        comparison=market_comparison(
            "运动记录",
            "跑步训练记录服务",
            "https://example.cn/fitness",
            title="运动训练平台",
            snippet="记录跑步和骑行活动。",
            retrieval_query="门店选址客流停留时长分析竞争对手",
        ),
    )

    assert accepted["competitors"][0]["relevance"]["verdict"] == "accepted"
    assert rejected["competitors"] == []
    assert rejected["rejected_sources"][0]["relevance"]["verdict"] == "rejected"


def test_chinese_non_competitor_source_is_rejected_even_when_it_mentions_a_platform() -> None:
    result = assess_market_comparison(
        opportunity="门店选址客流停留时长分析",
        evidence_digest="候选门店客流和停留时长数据",
        comparison=market_comparison(
            "行业文章",
            "门店选址客流分析说明",
            "https://example.cn/article",
            title="不是竞争对手的门店选址分析平台说明",
            snippet="并非竞争对手，只提供客流和停留时长分析服务介绍。",
        ),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["verdict"] == "rejected"


@pytest.mark.parametrize(
    ("opportunity", "evidence_digest", "title", "snippet"),
    [
        (
            "retail location intelligence using footfall and dwell time",
            "site candidates, rent, transit, footfall, dwell time",
            "Consumer-only retail site selection platform",
            "A consumer-facing platform for personal footfall and dwell-time decisions.",
        ),
        (
            "门店选址客流停留时长分析",
            "候选门店客流和停留时长数据",
            "仅面向消费者的门店选址客流平台",
            "面向个人的消费者端服务，提供客流和停留时长分析。",
        ),
        (
            "门店选址客流停留时长分析",
            "候选门店客流和停留时长数据",
            "面向消费者的门店选址客流平台",
            "面向消费者提供门店选址、客流和停留时长分析服务。",
        ),
    ],
)
def test_consumer_only_source_is_rejected_despite_overlap_and_platform_directness(
    opportunity: str,
    evidence_digest: str,
    title: str,
    snippet: str,
) -> None:
    result = assess_market_comparison(
        opportunity=opportunity,
        evidence_digest=evidence_digest,
        comparison=market_comparison(
            "Consumer product",
            "generated positioning must not decide relevance",
            "https://example.invalid/consumer-platform",
            title=title,
            snippet=snippet,
        ),
    )

    assert result["competitors"] == []
    assert result["rejected_sources"][0]["relevance"]["verdict"] == "rejected"


def test_relevant_web_finding_cannot_make_zero_competitor_result_available() -> None:
    comparison = market_comparison(
        "Strava",
        "athlete route and workout analytics",
        "https://strava.com",
        title="Strava | Run, Bike, Hike",
        snippet="Track workouts and athlete routes.",
    )
    comparison["external_findings"] = [
        {
            "claim": "Retail site selection uses footfall and dwell-time intelligence.",
            "source_title": "Retail site selection and footfall analytics platform",
            "source_url": "https://guide.example/location-planning",
        }
    ]

    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=comparison,
    )

    assert result["competitors"] == []
    assert result["external_findings"] == []
    assert result["market_evidence_status"] == "unavailable"
    assert result["adjacent_sources"]


def test_explicit_non_competitor_language_cannot_be_accepted() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison(
            "Pattern Library",
            "Not a competitor; an adjacent pattern for retail site selection and footfall analytics.",
            "https://patterns.example",
            title="Retail site selection and footfall pattern library",
            snippet="Not a competitor; an adjacent pattern for retail site selection and footfall analytics.",
        ),
    )

    assert result["competitors"] == []
    assert result["adjacent_sources"][0]["relevance"]["verdict"] == "adjacent"


def test_gated_contract_allows_zero_competitors_only_with_unavailable_trace_metadata() -> None:
    valid = GatedMarketComparison.model_validate(
        {
            "opportunity_id": "retail-location-intelligence",
            "competitors": [],
            "positioning_note": "No relevant external competitor evidence was accepted.",
            "market_evidence_status": "unavailable",
            "rejected_sources": [
                {
                    "name": "Strava",
                    "positioning": "athlete workout analytics",
                    "url": "https://strava.com",
                    "relevance": MarketSourceAssessment(
                        verdict="rejected",
                        query_purpose="direct_competitor",
                        opportunity_terms=["retail", "footfall"],
                        matched_terms=[],
                        deterministic_score=0,
                        reasons=["no meaningful opportunity overlap"],
                    ),
                }
            ],
        }
    )
    assert valid.competitors == []

    with pytest.raises(ValidationError):
        GatedMarketComparison.model_validate(
            {
                "opportunity_id": "retail-location-intelligence",
                "competitors": [],
                "positioning_note": "No evidence.",
                "market_evidence_status": "unavailable",
            }
        )


def test_public_market_contract_preserves_unavailable_status_without_trace_or_untyped_fields() -> None:
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison={
            **market_comparison(
                "Strava",
                "athlete route and workout analytics",
                "https://strava.com",
                title="Strava | Run, Bike, Hike",
                snippet="Track workouts and athlete routes.",
            ),
            "signals": [{"id": "private-runtime-field"}],
            "_llm": {
                "mode": "market_test",
                "response_id": "resp-market-1",
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                "cache": {"hit": True},
                "verification": {"sources": ["https://strava.com"]},
                "tool_calls": [{"name": "market_lookup", "output": "https://strava.com"}],
                "mcp": {"sources": ["https://strava.com"]},
            },
        },
    )

    public = public_market_comparison(result)

    assert public["competitors"] == []
    assert public["market_evidence_status"] == "unavailable"
    assert "external_market_evidence_unavailable" in public["gaps"]
    assert "rejected_sources" not in public
    assert "adjacent_sources" not in public
    assert "signals" not in public
    assert public["_llm"] == {
        "mode": "market_test",
        "response_id": "resp-market-1",
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        "cache": {"hit": True},
    }


def test_public_market_projection_removes_rejected_urls_and_nested_tool_output() -> None:
    rejected_url = "https://strava.example/private-tool-output"
    accepted_url = "https://vendor.example/site-selection"
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison={
            "opportunity_id": "retail-location-intelligence",
            "competitors": [
                {
                    "name": "raw tool output accepted vendor",
                    "positioning": f"raw tool output {rejected_url}",
                    "url": accepted_url,
                    "title": "Retail site selection and footfall analytics platform",
                    "snippet": "Compare sites with footfall and dwell-time intelligence.",
                },
                {
                    "name": "Strava",
                    "positioning": "athlete route analytics",
                    "url": rejected_url,
                    "title": "Strava running platform",
                    "snippet": "Track athlete workouts and routes.",
                },
            ],
            "positioning_note": f"Generated note embeds {rejected_url}",
            "errors": {"market_lookup": f"raw tool output {rejected_url}"},
            "tool_provenance": {
                "market_lookup": {
                    "tool_name": "market_lookup",
                    "sources": [{"title": "accepted", "url": accepted_url}, {"title": "rejected", "url": rejected_url}],
                    "citations": [accepted_url, rejected_url],
                    "verification": {"sources": [rejected_url]},
                    "raw_output": {"url": rejected_url},
                    "error": f"tool error {rejected_url}",
                }
            },
        },
    )

    public = public_market_comparison(result)

    assert public["competitors"][0]["url"] == accepted_url
    assert rejected_url not in repr(public)
    assert "raw tool output" not in repr(public)
    assert "verification" not in repr(public)


def test_query_plan_is_derived_from_current_opportunity_not_an_allowlist() -> None:
    plan = MarketQueryPlan.from_context(
        opportunity="warehouse robotics maintenance forecasting",
        evidence_digest="motor failures, repair intervals, spare parts",
    )

    assert plan.query_purpose == "direct_competitor"
    assert "warehouse" in plan.opportunity_terms
    assert "robotics" in plan.retrieval_query


def test_legacy_market_researcher_gates_sources_before_returning_public_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator._MARKET_CACHE.clear()
    monkeypatch.setattr(
        orchestrator,
        "run_market_mcp_research",
        lambda _payload: {
            "competitors": [
                {
                    "name": "Strava",
                    "positioning": "athlete route and workout analytics",
                    "url": "https://strava.com",
                    "title": "Strava | Run, Bike, Hike",
                    "snippet": "Track workouts and athlete routes.",
                }
            ],
            "sources": ["https://strava.com"],
            "_llm": {"mode": "test"},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "run_market_web_research",
        lambda _payload: {
            "external_findings": [
                {
                    "claim": "Training plans help endurance athletes.",
                    "source_title": "TrainingPeaks",
                    "source_url": "https://trainingpeaks.com",
                }
            ],
            "sources": [{"title": "TrainingPeaks", "url": "https://trainingpeaks.com"}],
            "positioning_note": "Generated market note.",
            "_llm": {"mode": "test"},
        },
    )
    artifact = {
        "workspace_id": "workspace-1",
        "feasibility": {"opportunity_id": "retail-location-intelligence"},
        "corpus": {
            "opportunities": [
                {
                    "id": "retail-location-intelligence",
                    "title": "Retail location intelligence using footfall and dwell time",
                    "description": "Compare site candidates using rent and transit evidence.",
                }
            ],
            "hits": [
                {
                    "source_file": "sites.csv",
                    "chunk_id": "row-1",
                    "content": "Site candidates include rent, transit, footfall, and dwell time.",
                }
            ],
        },
    }

    result = orchestrator._run_market_researcher(artifact)

    assert result["competitors"] == []
    assert result["external_findings"] == []
    assert result["sources"] == []
    assert result["market_evidence_status"] == "unavailable"
    assert "rejected_sources" not in result
    assert result["_market_relevance_trace"]["rejected_sources"]


def test_legacy_market_researcher_does_not_weaken_empty_raw_provider_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator._MARKET_CACHE.clear()
    monkeypatch.setattr(
        orchestrator,
        "run_market_mcp_research",
        lambda _payload: {"competitors": [], "sources": [], "_llm": {"mode": "test"}},
    )
    monkeypatch.setattr(
        orchestrator,
        "run_market_web_research",
        lambda _payload: {
            "external_findings": [],
            "sources": [],
            "positioning_note": "No raw competitors.",
            "_llm": {"mode": "test"},
        },
    )

    with pytest.raises(ValidationError):
        orchestrator._run_market_researcher(
            {
                "workspace_id": "workspace-1",
                "feasibility": {"opportunity_id": "retail-location-intelligence"},
                "corpus": {"opportunities": [], "hits": []},
            }
        )

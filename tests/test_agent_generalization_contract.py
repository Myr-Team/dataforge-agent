from pathlib import Path


FOUNDRY_CLIENT = Path(__file__).resolve().parents[1] / "backend" / "foundry_client.py"


def test_grounded_chat_prompt_is_not_bound_to_demo_schema_or_activity_template() -> None:
    source = FOUNDRY_CLIENT.read_text(encoding="utf-8")

    for demo_field in (
        "surrounding_env",
        "nearby_business_types",
        "competitor_count",
        "nearest_transit",
        "population_density",
        "avg_rent_index",
    ):
        assert demo_field not in source

    assert "活动机制（主线玩法）" not in source
    assert "曝光 → 报名 → 到店" not in source
    assert "根据任务选择最有用的章节" in source
    assert "不要因为行业不同而强套固定章节" in source

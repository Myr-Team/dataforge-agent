from __future__ import annotations

import pytest

from backend.result_cache_policy import (
    ResultCacheContext,
    evaluate_result_cache,
)


def _context(**overrides: object) -> ResultCacheContext:
    payload: dict[str, object] = {
        "tenant_ref": "tenant-sensitive",
        "workspace_id": "workspace-sensitive",
        "data_revision": "data-revision-1",
        "execution_kind": "full_analysis",
        "agent_id": "df-feasibility-analyst",
        "provider_type": "deepseek",
        "provider_id": "provider-deepseek",
        "model_id": "deepseek-v4-pro",
        "route_revision": 3,
        "prompt_revision": "feasibility-v4",
        "tool_schema_revision": "maf-tools-v2",
        "generation_parameters": {
            "max_output_tokens": 2800,
            "response_schema_revision": "feasibility-schema-v2",
        },
        "policy_revision": 5,
    }
    payload.update(overrides)
    return ResultCacheContext.model_validate(payload)


def test_cache_key_is_opaque_and_binds_every_material_dimension() -> None:
    baseline = evaluate_result_cache(_context())

    assert baseline.evidence.eligible is True
    assert baseline.evidence.state == "miss"
    assert baseline.evidence.reason == "eligible"
    assert baseline.cache_key
    assert "tenant-sensitive" not in baseline.cache_key
    assert "workspace-sensitive" not in baseline.cache_key

    changes = {
        "tenant_ref": "tenant-other",
        "workspace_id": "workspace-other",
        "data_revision": "data-revision-2",
        "execution_kind": "direct_reply",
        "agent_id": "df-auditor",
        "provider_type": "azure_foundry",
        "provider_id": None,
        "model_id": "gpt-5.1",
        "route_revision": 4,
        "prompt_revision": "feasibility-v5",
        "tool_schema_revision": "maf-tools-v3",
        "generation_parameters": {
            "temperature": 0.2,
            "max_output_tokens": 2800,
            "response_schema_revision": "feasibility-schema-v2",
        },
        "policy_revision": 6,
    }
    for field_name, value in changes.items():
        candidate = evaluate_result_cache(_context(**{field_name: value}))
        assert candidate.cache_key != baseline.cache_key, field_name


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"live_data": True}, "live_data"),
        ({"side_effecting_tools": True}, "side_effecting_tools"),
        ({"conversation_stable": False}, "unstable_conversation"),
        ({"data_revision": None}, "data_revision_missing"),
    ],
)
def test_cache_policy_returns_safe_bypass_reasons(
    overrides: dict[str, object],
    reason: str,
) -> None:
    decision = evaluate_result_cache(_context(**overrides))

    assert decision.cache_key is None
    assert decision.evidence.model_dump(mode="json") == {
        "eligible": False,
        "state": "bypassed",
        "reason": reason,
        "lookup_latency_ms": None,
        "policy_revision": 5,
        "source_result_version": None,
    }

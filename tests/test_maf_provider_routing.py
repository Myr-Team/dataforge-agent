from __future__ import annotations

import pytest

import backend.maf_agents as maf_agents
from backend.model_policy import ModelRoute, SelectedTextRoute


def _selected(provider_type: str) -> SelectedTextRoute:
    return SelectedTextRoute(
        route=ModelRoute(
            "analysis",
            "deepseek-v4-pro" if provider_type == "deepseek" else "gpt-5.1",
            "Analysis",
            frozenset({"analysis", "chat"}),
            provider_id="provider-deepseek" if provider_type == "deepseek" else None,
            provider_type=provider_type,
            model_id="deepseek-v4-pro" if provider_type == "deepseek" else "gpt-5.1",
        ),
        execution_kind="full_analysis",
    )


def test_external_maf_route_is_rejected_while_runtime_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "0")

    with pytest.raises(RuntimeError, match="external provider routing is disabled"):
        maf_agents._create_maf_chat_client(_selected("deepseek"))


def test_external_maf_route_requires_apim_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "0")

    with pytest.raises(RuntimeError, match="requires APIM"):
        maf_agents._create_maf_chat_client(_selected("deepseek"))

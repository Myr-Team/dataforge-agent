from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.maf_agents import AgentSpec, create_agent_registry


@dataclass(frozen=True)
class FakeFoundryAgent:
    spec: AgentSpec


@pytest.fixture
def fake_foundry_client():
    created: list[AgentSpec] = []

    def create(spec: AgentSpec) -> FakeFoundryAgent:
        created.append(spec)
        return FakeFoundryAgent(spec)

    create.created = created
    return create


def test_registry_uses_existing_prompt_files(fake_foundry_client):
    registry = create_agent_registry(client_factory=fake_foundry_client)

    assert set(registry.ids()) == {
        "df-coordinator",
        "df-corpus-analyst",
        "df-market-researcher",
        "df-feasibility-analyst",
        "df-auditor",
        "df-producer",
    }
    assert "evidence" in registry.spec("df-auditor").instructions.lower()


def test_each_agent_receives_only_scoped_tools(fake_foundry_client):
    registry = create_agent_registry(client_factory=fake_foundry_client)

    assert registry.spec("df-coordinator").tool_names == ()
    assert registry.spec("df-corpus-analyst").tool_names == ("search_pack_context",)
    assert registry.spec("df-feasibility-analyst").tool_names == (
        "search_pack_context",
        "code_interpreter",
    )
    assert registry.spec("df-market-researcher").tool_names == (
        "market_lookup_mcp",
        "web_search_preview",
    )
    assert registry.spec("df-producer").tool_names == (
        "render_pdf_report",
        "generate_image",
        "narrate_summary",
    )
    assert registry.spec("df-auditor").tool_names == ("search_pack_context",)
    assert "generate_image" not in registry.spec("df-auditor").tool_names


def test_registry_builds_and_retrieves_one_agent_per_spec(fake_foundry_client):
    registry = create_agent_registry(client_factory=fake_foundry_client)

    assert tuple(spec.agent_id for spec in fake_foundry_client.created) == registry.ids()
    assert registry.agent("df-auditor") == FakeFoundryAgent(registry.spec("df-auditor"))


def test_registry_rejects_unknown_agent_id(fake_foundry_client):
    registry = create_agent_registry(client_factory=fake_foundry_client)

    with pytest.raises(KeyError, match="df-unknown"):
        registry.spec("df-unknown")

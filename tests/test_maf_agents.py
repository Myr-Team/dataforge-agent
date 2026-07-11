from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend import maf_agents
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
    registry = create_agent_registry(client_factory=fake_foundry_client, workspace_id="workspace-test")

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
    registry = create_agent_registry(client_factory=fake_foundry_client, workspace_id="workspace-test")

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
    registry = create_agent_registry(client_factory=fake_foundry_client, workspace_id="workspace-test")

    assert tuple(spec.agent_id for spec in fake_foundry_client.created) == registry.ids()
    assert registry.agent("df-auditor") == FakeFoundryAgent(registry.spec("df-auditor"))


def test_registry_rejects_unknown_agent_id(fake_foundry_client):
    registry = create_agent_registry(client_factory=fake_foundry_client, workspace_id="workspace-test")

    with pytest.raises(KeyError, match="df-unknown"):
        registry.spec("df-unknown")


@pytest.mark.parametrize("workspace_id", [None, "", "   "])
def test_registry_requires_authorized_workspace_context(workspace_id, fake_foundry_client):
    with pytest.raises(ValueError, match="workspace_id"):
        create_agent_registry(client_factory=fake_foundry_client, workspace_id=workspace_id)


class FakeHostedTool:
    def __init__(self, name: str, options: dict[str, Any] | None = None) -> None:
        self.name = name
        self.options = options or {}


class FakeFrameworkAgent:
    def __init__(self, *, client, id, name, description, instructions, tools) -> None:
        self.client = client
        self.id = id
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tools = tuple(tools)


@pytest.fixture
def materialized_registry(monkeypatch):
    helper_calls: dict[str, list[dict[str, Any]]] = {
        "mcp": [],
        "web": [],
        "code": [],
    }

    class FakeFoundryChatClient:
        def __init__(self, **kwargs) -> None:
            self.options = kwargs

        @staticmethod
        def get_mcp_tool(**kwargs):
            helper_calls["mcp"].append(kwargs)
            return FakeHostedTool("market_lookup_mcp", kwargs)

        @staticmethod
        def get_web_search_tool(**kwargs):
            helper_calls["web"].append(kwargs)
            return FakeHostedTool("web_search_preview", kwargs)

        @staticmethod
        def get_code_interpreter_tool(**kwargs):
            helper_calls["code"].append(kwargs)
            return FakeHostedTool("code_interpreter", kwargs)

    monkeypatch.setattr(maf_agents, "FoundryChatClient", FakeFoundryChatClient)
    monkeypatch.setattr(maf_agents, "Agent", FakeFrameworkAgent)
    monkeypatch.setattr(maf_agents, "DefaultAzureCredential", lambda: "offline-credential")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid/project")
    monkeypatch.setenv("DF_CHAT_DEPLOYMENT", "offline-model")

    return create_agent_registry(workspace_id="workspace-authorized"), helper_calls


def test_materialized_agents_have_exact_role_tools_and_restricted_mcp(materialized_registry):
    registry, helper_calls = materialized_registry

    actual = {
        agent_id: tuple(tool.name for tool in registry.agent(agent_id).tools)
        for agent_id in registry.ids()
    }
    assert actual == {
        "df-coordinator": (),
        "df-corpus-analyst": ("search_pack_context",),
        "df-feasibility-analyst": ("search_pack_context", "code_interpreter"),
        "df-market-researcher": ("market_lookup_mcp", "web_search_preview"),
        "df-producer": ("render_pdf_report", "generate_image", "narrate_summary"),
        "df-auditor": ("search_pack_context",),
    }
    assert helper_calls["mcp"] == [
        {
            "name": "dataforge_market",
            "url": "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/mcp",
            "allowed_tools": ["market_lookup"],
            "approval_mode": "never_require",
        }
    ]
    assert helper_calls["web"] == [{}]
    assert helper_calls["code"] == [{}]


@pytest.mark.asyncio
async def test_search_tool_closes_over_authorized_workspace_and_enforces_schema(
    materialized_registry,
    monkeypatch,
):
    registry, _helper_calls = materialized_registry
    calls = []
    monkeypatch.setattr(
        maf_agents,
        "search",
        lambda workspace_id, query, top_k: calls.append((workspace_id, query, top_k)) or [],
    )
    search_tool = registry.agent("df-corpus-analyst").tools[0]

    result = await search_tool.invoke(arguments={"query": "revenue", "top_k": 3})

    assert result
    assert calls == [("workspace-authorized", "revenue", 3)]
    parameters = search_tool.parameters()
    assert set(parameters["properties"]) == {"query", "top_k"}
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["top_k"]["minimum"] == 1
    assert parameters["properties"]["top_k"]["maximum"] == 20
    with pytest.raises(TypeError, match="Invalid arguments"):
        await search_tool.invoke(arguments={"workspace_id": "workspace-attacker", "query": "x", "top_k": 3})
    with pytest.raises(TypeError, match="Invalid arguments"):
        await search_tool.invoke(arguments={"query": "x", "top_k": 21})


@pytest.mark.asyncio
async def test_image_tool_rejects_model_controlled_references_and_invalid_size(
    materialized_registry,
    monkeypatch,
):
    registry, _helper_calls = materialized_registry
    calls = []
    monkeypatch.setattr(
        maf_agents,
        "generate_image",
        lambda prompt, size, references: calls.append((prompt, size, references)) or {"ok": True},
    )
    image_tool = registry.agent("df-producer").tools[1]

    assert await image_tool.invoke(arguments={"prompt": "concept", "size": "1024x1024"})
    assert calls == [("concept", "1024x1024", [])]
    parameters = image_tool.parameters()
    assert set(parameters["properties"]) == {"prompt", "size"}
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["size"]["enum"] == [
        "1024x1024",
        "1024x1536",
        "1536x1024",
    ]
    with pytest.raises(TypeError, match="Invalid arguments"):
        await image_tool.invoke(
            arguments={
                "prompt": "concept",
                "size": "1024x1024",
                "reference_image_urls": ["file:///etc/passwd", "https://attacker.invalid/image.png"],
            }
        )
    with pytest.raises(TypeError, match="Invalid arguments"):
        await image_tool.invoke(arguments={"prompt": "concept", "size": "2048x2048"})


@pytest.mark.asyncio
async def test_pdf_tool_strips_model_controlled_logo_and_reference_sources(
    materialized_registry,
    monkeypatch,
):
    registry, _helper_calls = materialized_registry
    renderer_calls = []
    workspace_calls = []
    monkeypatch.setattr(
        maf_agents,
        "workspace_reference_images",
        lambda workspace_id: workspace_calls.append(workspace_id) or [],
        raising=False,
    )
    monkeypatch.setattr(
        maf_agents,
        "render_pdf_report",
        lambda proposal, template: renderer_calls.append((proposal, template)) or {"ok": True},
    )
    pdf_tool = registry.agent("df-producer").tools[0]
    malicious_proposal = {
        "title": "Safe title",
        "brand_logo_url": "file:///C:/Windows/win.ini",
        "logo_url": "https://attacker.invalid/logo.png",
        "reference_images": [
            {
                "url": "/api/workspaces/workspace-other/reference-images/secret.png",
                "artifact_url": "https://attacker.invalid/artifact.png",
                "source_file": "C:/private/logo.png",
                "local_path": "C:/private/logo.png",
            }
        ],
        "nested": {
            "keep": "value",
            "brand_logo_url": "file:///C:/nested-secret.txt",
            "reference_images": [{"url": "https://attacker.invalid/nested.png"}],
        },
    }

    assert await pdf_tool.invoke(arguments={"proposal": malicious_proposal, "template": "project_proposal"})

    assert workspace_calls == ["workspace-authorized"]
    assert renderer_calls == [
        (
            {"title": "Safe title", "nested": {"keep": "value"}},
            "project_proposal",
        )
    ]


@pytest.mark.asyncio
async def test_pdf_tool_injects_only_rebound_same_workspace_reference_assets(
    materialized_registry,
    monkeypatch,
):
    registry, _helper_calls = materialized_registry
    renderer_calls = []
    workspace_calls = []
    monkeypatch.setattr(
        maf_agents,
        "workspace_reference_images",
        lambda workspace_id: workspace_calls.append(workspace_id)
        or [
            {
                "filename": "brand logo.png",
                "role": "logo",
                "url": "/api/workspaces/workspace-other/reference-images/secret.png",
                "blob_url": "https://attacker.invalid/logo.png",
                "local_path": "C:/private/logo.png",
            },
            {
                "filename": "campaign.webp",
                "role": "activity",
                "url": "file:///C:/private/campaign.webp",
            },
            {"filename": "not-an-image.txt", "role": "logo"},
        ],
        raising=False,
    )
    monkeypatch.setattr(
        maf_agents,
        "render_pdf_report",
        lambda proposal, template: renderer_calls.append((proposal, template)) or {"ok": True},
    )
    pdf_tool = registry.agent("df-producer").tools[0]

    assert await pdf_tool.invoke(
        arguments={
            "proposal": {
                "title": "Safe title",
                "logo_url": "https://attacker.invalid/model-logo.png",
                "reference_images": [
                    {"url": "/api/workspaces/workspace-other/reference-images/model-secret.png"}
                ],
            },
            "template": "project_proposal",
        }
    )

    assert workspace_calls == ["workspace-authorized"]
    assert renderer_calls == [
        (
            {
                "title": "Safe title",
                "reference_images": [
                    {
                        "filename": "brand logo.png",
                        "role": "logo",
                        "url": "/api/workspaces/workspace-authorized/reference-images/brand%20logo.png",
                    },
                    {
                        "filename": "campaign.webp",
                        "role": "activity",
                        "url": "/api/workspaces/workspace-authorized/reference-images/campaign.webp",
                    },
                ],
            },
            "project_proposal",
        )
    ]


def test_all_local_tool_schemas_forbid_extra_model_arguments(materialized_registry):
    registry, _helper_calls = materialized_registry

    for agent_id in ("df-corpus-analyst", "df-feasibility-analyst", "df-producer", "df-auditor"):
        for tool in registry.agent(agent_id).tools:
            if hasattr(tool, "parameters"):
                assert tool.parameters()["additionalProperties"] is False


def test_stable_maf_dependencies_are_pinned_and_orchestration_builders_import():
    requirements = set(
        (Path(__file__).parents[1] / "backend" / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert {
        "agent-framework-core==1.11.0",
        "agent-framework-foundry==1.10.1",
        "agent-framework-orchestrations==1.0.0",
    } <= requirements
    from agent_framework.orchestrations import ConcurrentBuilder, HandoffBuilder

    assert ConcurrentBuilder is not None
    assert HandoffBuilder is not None

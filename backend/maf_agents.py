"""First-class Microsoft Agent Framework agents for the DataForge roles."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from agents.build_agents import AGENTS

from .rag import search
from .tools.generate_image import generate_image
from .tools.narrate_summary import narrate_summary
from .tools.render_pdf import render_pdf_report


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agents" / "prompts"


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    prompt_file: str
    tool_names: tuple[str, ...]
    description: str
    instructions: str


class MafAgentRegistry:
    """Expose DataForge MAF agents and their immutable role specifications."""

    def __init__(self, specs: Sequence[AgentSpec], agents: dict[str, Agent]) -> None:
        self._specs = {spec.agent_id: spec for spec in specs}
        self._agents = agents

    def spec(self, agent_id: str) -> AgentSpec:
        return self._specs[agent_id]

    def agent(self, agent_id: str) -> Agent:
        return self._agents[agent_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._specs)


@tool(name="search_pack_context", approval_mode="never_require")
def search_pack_context(workspace_id: str, query: str, top_k: int) -> dict[str, Any]:
    """Search the active DataForge workspace corpus with a workspace filter."""
    return {"hits": search(workspace_id, query, top_k)}


@tool(name="render_pdf_report", approval_mode="never_require")
def render_pdf_report_tool(proposal: dict[str, Any], template: str) -> dict[str, Any]:
    """Render a structured DataForge proposal to PDF."""
    return render_pdf_report(proposal, template)


@tool(name="generate_image", approval_mode="never_require")
def generate_image_tool(
    prompt: str,
    size: str,
    reference_image_urls: list[str],
) -> dict[str, Any]:
    """Generate a concept image for an approved product opportunity."""
    return generate_image(prompt, size, reference_image_urls)


@tool(name="narrate_summary", approval_mode="never_require")
def narrate_summary_tool(text: str, voice: str) -> dict[str, Any]:
    """Generate a Chinese spoken executive summary as playable audio."""
    return narrate_summary(text, voice)


LOCAL_TOOLS = {
    "search_pack_context": search_pack_context,
    "render_pdf_report": render_pdf_report_tool,
    "generate_image": generate_image_tool,
    "narrate_summary": narrate_summary_tool,
}


def _market_mcp_url() -> str:
    url = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/mcp")
    url = url.rstrip("/")
    return url if url.endswith("/mcp") else f"{url}/mcp"


def _tools_for(spec: AgentSpec) -> list[Any]:
    tools: list[Any] = []
    for tool_name in spec.tool_names:
        if tool_name in LOCAL_TOOLS:
            tools.append(LOCAL_TOOLS[tool_name])
        elif tool_name == "code_interpreter":
            tools.append(FoundryChatClient.get_code_interpreter_tool())
        elif tool_name == "market_lookup_mcp":
            tools.append(
                FoundryChatClient.get_mcp_tool(
                    name="dataforge_market",
                    url=_market_mcp_url(),
                    allowed_tools=["market_lookup"],
                    approval_mode="never_require",
                )
            )
        elif tool_name == "web_search_preview":
            tools.append(FoundryChatClient.get_web_search_tool())
        else:
            raise ValueError(f"Unsupported DataForge MAF tool: {tool_name}")
    return tools


def _agent_specs() -> tuple[AgentSpec, ...]:
    return tuple(
        AgentSpec(
            agent_id=agent["name"],
            prompt_file=agent["prompt"],
            tool_names=tuple(agent["tools"]),
            description=f"DataForge {agent['name']} specialist.",
            instructions=(PROMPTS / agent["prompt"]).read_text(encoding="utf-8"),
        )
        for agent in AGENTS
    )


def _create_foundry_agent(spec: AgentSpec, client: FoundryChatClient) -> Agent:
    return Agent(
        client=client,
        id=spec.agent_id,
        name=spec.agent_id,
        description=spec.description,
        instructions=spec.instructions,
        tools=_tools_for(spec),
    )


def create_agent_registry(
    client_factory: Callable[[AgentSpec], Agent] | None = None,
) -> MafAgentRegistry:
    """Build all six DataForge agents without persisting their definitions to Foundry."""
    specs = _agent_specs()
    if client_factory is None:
        client = FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.environ["DF_CHAT_DEPLOYMENT"],
            credential=DefaultAzureCredential(),
        )
        client_factory = lambda spec: _create_foundry_agent(spec, client)

    return MafAgentRegistry(specs, {spec.agent_id: client_factory(spec) for spec in specs})

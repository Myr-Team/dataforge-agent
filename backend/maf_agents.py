"""First-class Microsoft Agent Framework agents for the DataForge roles."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
from pydantic import BaseModel, ConfigDict, Field

from agents.build_agents import AGENTS

from .model_policy import current_text_route, resolve_text_deployment, resolve_text_route
from .rag import search
from .schemas import AuditVerdict, FeasibilityReport
from .tracing import gateway_request_headers
from .tools.generate_image import generate_image
from .tools.narrate_summary import narrate_summary
from .tools.render_pdf import render_pdf_report
from .workspace_store import workspace_reference_images


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


class _StrictToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SearchPackContextInput(_StrictToolInput):
    query: str
    top_k: int = Field(ge=1, le=20)


class _RenderPdfReportInput(_StrictToolInput):
    proposal: dict[str, Any]
    template: str


class _GenerateImageInput(_StrictToolInput):
    prompt: str
    size: Literal["1024x1024", "1024x1536", "1536x1024"]


class _NarrateSummaryInput(_StrictToolInput):
    text: str
    voice: str


_PDF_SOURCE_KEYS = frozenset({"brand_logo_url", "logo_url", "reference_images"})
_PDF_REFERENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_PDF_REFERENCE_ROLES = frozenset({"logo", "activity", "reference"})


def _search_pack_context_tool(workspace_id: str) -> Any:
    @tool(
        name="search_pack_context",
        approval_mode="never_require",
        schema=_SearchPackContextInput,
    )
    def search_authorized_workspace(query: str, top_k: int) -> dict[str, Any]:
        """Search the authorized DataForge workspace corpus."""
        return {"hits": search(workspace_id, query, top_k)}

    return search_authorized_workspace


def _strip_pdf_source_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_pdf_source_fields(item)
            for key, item in value.items()
            if key not in _PDF_SOURCE_KEYS
        }
    if isinstance(value, list):
        return [_strip_pdf_source_fields(item) for item in value]
    return value


def _trusted_pdf_reference_images(workspace_id: str) -> list[dict[str, str]]:
    try:
        candidates = workspace_reference_images(workspace_id)
    except Exception:
        return []

    trusted: list[dict[str, str]] = []
    seen: set[str] = set()
    workspace_segment = quote(workspace_id, safe="")
    for item in candidates:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not filename or filename in {".", ".."} or Path(filename).suffix.lower() not in _PDF_REFERENCE_SUFFIXES:
            continue
        if filename in seen:
            continue
        seen.add(filename)
        role = str(item.get("role") or "reference").strip().lower()
        if role not in _PDF_REFERENCE_ROLES:
            role = "reference"
        trusted.append(
            {
                "filename": filename,
                "role": role,
                "url": f"/api/workspaces/{workspace_segment}/reference-images/{quote(filename, safe='')}",
            }
        )
        if len(trusted) == 3:
            break
    return trusted


def _render_pdf_report_tool(workspace_id: str) -> Any:
    @tool(
        name="render_pdf_report",
        approval_mode="never_require",
        schema=_RenderPdfReportInput,
    )
    def render_authorized_workspace_pdf(proposal: dict[str, Any], template: str) -> dict[str, Any]:
        """Render a proposal using only reference assets from the authorized workspace."""
        sanitized = _strip_pdf_source_fields(proposal)
        trusted_references = _trusted_pdf_reference_images(workspace_id)
        if trusted_references:
            sanitized["reference_images"] = trusted_references
        return render_pdf_report(sanitized, template, workspace_id=workspace_id)

    return render_authorized_workspace_pdf


def _generate_image_tool(workspace_id: str) -> Any:
    @tool(
        name="generate_image",
        approval_mode="never_require",
        schema=_GenerateImageInput,
    )
    def generate_authorized_workspace_image(
        prompt: str,
        size: Literal["1024x1024", "1024x1536", "1536x1024"],
    ) -> dict[str, Any]:
        """Generate a concept image for an approved product opportunity."""
        return generate_image(prompt, size, [], workspace_id=workspace_id)

    return generate_authorized_workspace_image


def _narrate_summary_tool(workspace_id: str) -> Any:
    @tool(
        name="narrate_summary",
        approval_mode="never_require",
        schema=_NarrateSummaryInput,
    )
    def narrate_authorized_workspace_summary(text: str, voice: str) -> dict[str, Any]:
        """Generate a Chinese spoken executive summary as playable audio."""
        return narrate_summary(text, voice, workspace_id=workspace_id)

    return narrate_authorized_workspace_summary


def _local_tools(workspace_id: str) -> dict[str, Any]:
    return {
        "search_pack_context": _search_pack_context_tool(workspace_id),
        "render_pdf_report": _render_pdf_report_tool(workspace_id),
        "generate_image": _generate_image_tool(workspace_id),
        "narrate_summary": _narrate_summary_tool(workspace_id),
    }


def _market_mcp_url() -> str:
    url = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/mcp")
    url = url.rstrip("/")
    return url if url.endswith("/mcp") else f"{url}/mcp"


def _tools_for(
    spec: AgentSpec,
    workspace_id: str,
    hosted_tool_client: Any = FoundryChatClient,
) -> list[Any]:
    local_tools = _local_tools(workspace_id)
    tools: list[Any] = []
    for tool_name in spec.tool_names:
        if tool_name in local_tools:
            tools.append(local_tools[tool_name])
        elif tool_name == "code_interpreter":
            tools.append(hosted_tool_client.get_code_interpreter_tool())
        elif tool_name == "market_lookup_mcp":
            tools.append(
                hosted_tool_client.get_mcp_tool(
                    name="dataforge_market",
                    url=_market_mcp_url(),
                    allowed_tools=["market_lookup"],
                    approval_mode="never_require",
                )
            )
        elif tool_name == "web_search_preview":
            tools.append(hosted_tool_client.get_web_search_tool())
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


def _create_foundry_agent(spec: AgentSpec, client: Any, workspace_id: str) -> Agent:
    response_formats: dict[str, type[BaseModel]] = {
        "df-feasibility-analyst": FeasibilityReport,
        "df-auditor": AuditVerdict,
    }
    response_format = response_formats.get(spec.agent_id)
    return Agent(
        client=client,
        id=spec.agent_id,
        name=spec.agent_id,
        description=spec.description,
        instructions=spec.instructions,
        tools=_tools_for(spec, workspace_id, type(client)),
        default_options={"response_format": response_format} if response_format is not None else {},
    )


def _create_maf_chat_client() -> Any:
    auth_mode = str(os.environ.get("DF_MAF_AUTH_MODE") or "auto").strip().lower()
    if auth_mode not in {"auto", "api_key", "managed_identity"}:
        raise ValueError("DF_MAF_AUTH_MODE must be auto, api_key, or managed_identity")

    selected = current_text_route()
    model = selected.route.deployment
    gateway_enabled = str(os.environ.get("DF_APIM_GATEWAY_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if gateway_enabled:
        gateway_url = str(os.environ.get("DF_APIM_GATEWAY_URL") or "").strip().rstrip("/")
        audience = str(os.environ.get("DF_APIM_AUDIENCE") or "").strip().rstrip("/")
        if not gateway_url or not audience:
            raise RuntimeError(
                "DF_APIM_GATEWAY_ENABLED requires both DF_APIM_GATEWAY_URL and DF_APIM_AUDIENCE"
            )
        gateway_headers = gateway_request_headers()
        gateway_headers["x-dataforge-model-route"] = selected.route.route_id
        return OpenAIChatClient(
            model=model,
            credential=get_bearer_token_provider(
                ManagedIdentityCredential(),
                f"{audience}/.default",
            ),
            azure_endpoint=gateway_url,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "preview"),
            default_headers=gateway_headers,
        )

    api_key = str(os.environ.get("AZURE_OPENAI_API_KEY") or "").strip()
    azure_endpoint = str(
        os.environ.get("OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT") or ""
    ).strip()
    use_api_key = auth_mode == "api_key" or (auth_mode == "auto" and bool(api_key and azure_endpoint))
    if use_api_key:
        if not api_key or not azure_endpoint:
            raise RuntimeError(
                "DF_MAF_AUTH_MODE=api_key requires AZURE_OPENAI_API_KEY and OPENAI_ENDPOINT"
            )
        return OpenAIChatClient(
            model=model,
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "preview"),
        )

    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=DefaultAzureCredential(),
    )


def create_agent_registry(
    client_factory: Callable[[AgentSpec], Agent] | None = None,
    *,
    workspace_id: str | None = None,
) -> MafAgentRegistry:
    """Build all six DataForge agents without persisting their definitions to Foundry."""
    authorized_workspace_id = str(workspace_id or "").strip()
    if not authorized_workspace_id:
        raise ValueError("workspace_id is required to create a DataForge agent registry")

    specs = _agent_specs()
    if client_factory is None:
        client = _create_maf_chat_client()
        client_factory = lambda spec: _create_foundry_agent(spec, client, authorized_workspace_id)

    return MafAgentRegistry(specs, {spec.agent_id: client_factory(spec) for spec in specs})

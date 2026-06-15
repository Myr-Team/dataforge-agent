from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agents" / "prompts"
SCHEMAS = ROOT / "agents" / "tool_schemas.json"
STATE = ROOT / "agents" / ".agents_state.json"


AGENTS = [
    {
        "name": "df-coordinator",
        "prompt": "coordinator.md",
        "tools": [],
        "verify_prompt": "Workspace demo-corpus: user asks what products can be built. Return routing JSON.",
    },
    {
        "name": "df-corpus-analyst",
        "prompt": "corpus_analyst.md",
        "tools": ["search_pack_context"],
        "verify_prompt": "Find evidence for outdoor analytics opportunities in workspace demo-corpus.",
    },
    {
        "name": "df-feasibility-analyst",
        "prompt": "feasibility_analyst.md",
        "tools": ["search_pack_context", "code_interpreter"],
        "verify_prompt": "Evaluate a health diagnosis product for workspace demo-corpus.",
    },
    {
        "name": "df-market-researcher",
        "prompt": "market_researcher.md",
        "tools": ["market_lookup_mcp", "web_search_preview"],
        "verify_prompt": "Compare outdoor coaching analytics competitors.",
    },
    {
        "name": "df-producer",
        "prompt": "producer.md",
        "tools": ["render_pdf_report", "generate_image", "narrate_summary"],
        "verify_prompt": "Plan deliverables for an outdoor analytics proposal.",
    },
    {
        "name": "df-auditor",
        "prompt": "auditor.md",
        "tools": ["search_pack_context"],
        "verify_prompt": "Audit whether a feasibility report with empty evidence should pass.",
    },
]


def _load_tool_defs() -> dict[str, Any]:
    return json.loads(SCHEMAS.read_text(encoding="utf-8"))


def _mcp_server_url(raw_url: str | None = None) -> str:
    url = (raw_url or os.environ.get("MCP_MARKET_URL") or "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/mcp").strip()
    url = url.rstrip("/")
    if not url.endswith("/mcp"):
        url += "/mcp"
    return url


def _materialize_tools(agent: dict[str, Any], mcp_url: str) -> list[dict[str, Any]]:
    from azure.ai.projects import models

    defs = _load_tool_defs()
    tools: list[Any] = []
    for key in agent["tools"]:
        spec = dict(defs[key])
        tool_type = spec.get("type")
        if tool_type == "function":
            tools.append(
                models.FunctionTool(
                    name=spec["name"],
                    description=spec["description"],
                    parameters=spec["parameters"],
                    strict=spec["strict"],
                )
            )
        elif tool_type == "mcp":
            tools.append(
                models.MCPTool(
                    server_label=spec["server_label"],
                    server_url=_mcp_server_url(mcp_url),
                    allowed_tools=spec["allowed_tools"],
                    require_approval="never",
                )
            )
        elif tool_type == "web_search_preview":
            bing_connection_id = os.environ.get("DF_BING_CONNECTION_ID") or os.environ.get("BING_CONNECTION_ID")
            if bing_connection_id:
                tools.append(
                    models.BingGroundingTool(
                        bing_grounding=models.BingGroundingSearchToolParameters(
                            search_configurations=[
                                models.BingGroundingSearchConfiguration(
                                    project_connection_id=bing_connection_id,
                                    market=os.environ.get("DF_WEB_MARKET", "zh-CN"),
                                    set_lang=os.environ.get("DF_WEB_LANG", "zh-Hans"),
                                    count=int(os.environ.get("DF_BING_RESULT_COUNT", "5")),
                                )
                            ]
                        )
                    )
                )
            else:
                tools.append(models.WebSearchPreviewTool())
        elif tool_type == "code_interpreter":
            tools.append(models.CodeInterpreterTool())
        else:
            raise ValueError(f"Unsupported tool type {tool_type}")
    return tools


def _prompt(agent: dict[str, Any]) -> str:
    return (PROMPTS / agent["prompt"]).read_text(encoding="utf-8")


def _project_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)


def build_agents(dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        deployment = os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1")
        mcp_url = _mcp_server_url()
        specs = [
            {
                "name": agent["name"],
                "model": deployment,
                "instructions": _prompt(agent),
                "tools": [
                    "bing_grounding" if key == "web_search_preview" and (os.environ.get("DF_BING_CONNECTION_ID") or os.environ.get("BING_CONNECTION_ID")) else key
                    for key in agent["tools"]
                ],
            }
            for agent in AGENTS
        ]
        return {"dry_run": True, "agents": specs}

    from azure.ai.projects import models

    deployment = os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1")
    mcp_url = _mcp_server_url()
    specs = [
        {
            "name": agent["name"],
            "model": deployment,
            "instructions": _prompt(agent),
            "tools": _materialize_tools(agent, mcp_url),
        }
        for agent in AGENTS
    ]

    client = _project_client()
    created: dict[str, Any] = {}
    for spec in specs:
        definition = models.PromptAgentDefinition(
            model=spec["model"],
            instructions=spec["instructions"],
            tools=spec["tools"],
        )
        version = client.agents.create_version(
            spec["name"],
            definition=definition,
            description=f"DataForge {spec['name']} prompt agent",
            metadata={"wp": "WP6", "project": "dataforge"},
        )
        agent_details = client.agents.get(spec["name"])
        created[spec["name"]] = {
            "id": getattr(agent_details, "id", spec["name"]),
            "name": spec["name"],
            "version_id": getattr(version, "id", None),
            "status": "version_created",
        }
    STATE.write_text(json.dumps(created, indent=2), encoding="utf-8")
    return {"dry_run": False, "agents": created}


def verify_agents(local_only: bool = False) -> dict[str, Any]:
    if not STATE.exists():
        raise RuntimeError("Missing .agents_state.json; run build first")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if len(state) != 6:
        raise RuntimeError(f"Expected 6 agents, got {len(state)}")
    if local_only:
        return {"verified": True, "mode": "local_state", "agents": sorted(state)}

    client = _project_client()
    verified: dict[str, Any] = {}
    for item in AGENTS:
        agent = client.agents.get(item["name"])
        verified[item["name"]] = {"id": getattr(agent, "id", item["name"]), "name": item["name"]}
    return {"verified": True, "mode": "foundry_get", "agents": verified}


def smoke_responses() -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    deployment = os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1")
    results: dict[str, Any] = {}
    for item in AGENTS:
        response = openai_client.responses.create(
            model=deployment,
            instructions=_prompt(item) + "\n\nReturn only a compact JSON object for this smoke check.",
            input=item["verify_prompt"],
        )
        text = getattr(response, "output_text", "").strip()
        if not text:
            raise RuntimeError(f"Empty smoke response from {item['name']}")
        results[item["name"]] = {"chars": len(text), "preview": text[:160]}
    return {"verified": True, "mode": "responses_smoke", "agents": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["dry-run", "build", "verify", "smoke"])
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    if args.command == "dry-run":
        result = build_agents(dry_run=True)
    elif args.command == "build":
        result = build_agents(dry_run=False)
    elif args.command == "verify":
        result = verify_agents(local_only=args.local_only)
    else:
        result = smoke_responses()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

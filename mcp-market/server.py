from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "competitors.jsonl"


app = FastAPI(title="DataForge Market MCP", version="0.4.0")


class MarketLookupRequest(BaseModel):
    category: str = Field(default="")
    keywords: list[str] = Field(default_factory=list)
    limit: int = Field(default=4, ge=1, le=10)


def _load_competitors() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def market_lookup(category: str = "", keywords: list[str] | None = None, limit: int = 4) -> list[dict[str, Any]]:
    keywords = [k.lower() for k in (keywords or []) if k]
    category_terms = [t.lower() for t in category.split() if t]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in _load_competitors():
        haystack = " ".join(
            [
                row["name"],
                row["category"],
                row["positioning"],
                " ".join(row.get("keywords", [])),
            ]
        ).lower()
        score = sum(3 for term in category_terms if term in haystack) + sum(2 for term in keywords if term in haystack)
        if score:
            scored.append((score, row))
    if not scored:
        scored = [(1, row) for row in _load_competitors()]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "dataforge-market-mcp", "competitors": len(_load_competitors())}


@app.post("/tools/market_lookup")
def market_lookup_http(req: MarketLookupRequest) -> dict[str, Any]:
    return {"results": market_lookup(req.category, req.keywords, req.limit)}


@app.post("/mcp")
def mcp_rpc(payload: dict[str, Any]) -> dict[str, Any]:
    method = payload.get("method")
    rpc_id = payload.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "dataforge-market-mcp", "version": "0.4.0"},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": [
                    {
                        "name": "market_lookup",
                        "description": "Return competitor products for a product category and keyword set.",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "category": {"type": "string"},
                                "keywords": {"type": "array", "items": {"type": "string"}},
                                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                            },
                            "required": ["category", "keywords"],
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        params = payload.get("params", {})
        if params.get("name") != "market_lookup":
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Unknown tool"}}
        args = params.get("arguments", {})
        results = market_lookup(args.get("category", ""), args.get("keywords", []), int(args.get("limit", 4)))
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": [{"type": "text", "text": json.dumps({"results": results}, ensure_ascii=False)}]},
        }
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Unsupported method {method}"}}


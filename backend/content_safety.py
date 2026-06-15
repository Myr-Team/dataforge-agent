"""Azure AI Content Safety integration (Responsible AI guardrail).

Screens user input before it reaches the agents:
  * Prompt Shield  — jailbreak / prompt-injection detection
  * text:analyze   — Hate / SelfHarm / Sexual / Violence severity

Fail-open by design: if the service is not configured or errors out, requests
are allowed through so the product keeps working; the screening is an added
safety layer, not a hard dependency.
"""
from __future__ import annotations

import os
from typing import Any

import requests

try:  # AAD fallback when no key is configured
    from azure.identity import DefaultAzureCredential
except Exception:  # pragma: no cover
    DefaultAzureCredential = None  # type: ignore

API_VERSION = "2024-09-01"
# Content Safety severity is reported on a 0..7 scale (4-level model: 0/2/4/6).
# Block at >= 4 (medium) by default.
_BLOCK_SEVERITY = int(os.environ.get("CONTENT_SAFETY_BLOCK_SEVERITY", "4"))
_TIMEOUT = float(os.environ.get("CONTENT_SAFETY_TIMEOUT", "6"))


def _endpoint() -> str:
    return (os.environ.get("CONTENT_SAFETY_ENDPOINT") or "").rstrip("/")


def configured() -> bool:
    return bool(_endpoint())


def _headers() -> dict[str, str]:
    key = os.environ.get("CONTENT_SAFETY_KEY")
    if key:
        return {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"}
    if DefaultAzureCredential is not None:
        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    raise RuntimeError("no content safety credential")


def screen_input(text: Any) -> dict[str, Any]:
    """Return {allowed, checked, jailbreak, categories, error?}. Fail-open."""
    endpoint = _endpoint()
    body = str(text or "").strip()
    if not endpoint or not body:
        return {"allowed": True, "checked": False}
    result: dict[str, Any] = {"allowed": True, "checked": True, "jailbreak": False, "categories": []}
    headers = _headers()
    sample = body[:8000]
    try:
        shield = requests.post(
            f"{endpoint}/contentsafety/text:shieldPrompt?api-version={API_VERSION}",
            headers=headers,
            json={"userPrompt": sample, "documents": []},
            timeout=_TIMEOUT,
        )
        if shield.ok:
            analysis = (shield.json() or {}).get("userPromptAnalysis") or {}
            if analysis.get("attackDetected"):
                result["allowed"] = False
                result["jailbreak"] = True
    except Exception as exc:  # fail-open
        result["error"] = str(exc)[:200]
    try:
        analyze = requests.post(
            f"{endpoint}/contentsafety/text:analyze?api-version={API_VERSION}",
            headers=headers,
            json={"text": sample},
            timeout=_TIMEOUT,
        )
        if analyze.ok:
            for item in (analyze.json() or {}).get("categoriesAnalysis") or []:
                severity = int(item.get("severity") or 0)
                if severity >= _BLOCK_SEVERITY:
                    result["allowed"] = False
                    result["categories"].append({"category": item.get("category"), "severity": severity})
    except Exception as exc:  # fail-open
        result.setdefault("error", str(exc)[:200])
    return result


def refusal_message(screen: dict[str, Any]) -> str:
    if screen.get("jailbreak"):
        return "这条请求看起来在尝试绕过系统指令，我无法执行。我们可以继续围绕你的工作区数据做正常的产品可行性分析。"
    cats = "、".join(str(c.get("category")) for c in (screen.get("categories") or []) if c.get("category"))
    base = "这条内容触发了内容安全策略" + (f"（{cats}）" if cats else "") + "，我不能就此作答。"
    return base + "请把问题聚焦在工作区数据与产品可行性上，我很乐意帮你分析。"


def health() -> dict[str, Any]:
    endpoint = _endpoint()
    if not endpoint:
        return {"ok": False, "state": "not_configured"}
    try:
        resp = requests.post(
            f"{endpoint}/contentsafety/text:analyze?api-version={API_VERSION}",
            headers=_headers(),
            json={"text": "hello"},
            timeout=_TIMEOUT,
        )
        return {"ok": resp.ok, "state": "ok" if resp.ok else f"http_{resp.status_code}", "endpoint": endpoint}
    except Exception as exc:
        return {"ok": False, "state": "error", "error": str(exc)[:160]}

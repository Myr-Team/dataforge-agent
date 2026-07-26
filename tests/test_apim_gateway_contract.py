from __future__ import annotations

import json
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "infra" / "apim" / "dataforge-text-api.json"
TELEMETRY_TEMPLATE = Path(__file__).resolve().parents[1] / "infra" / "apim" / "dataforge-telemetry.json"


def test_text_gateway_policy_requires_entra_and_returns_only_safe_correlation() -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert template["parameters"]["foundryEndpoint"]["defaultValue"].rstrip("/").endswith("/openai")
    policy = next(
        item["properties"]["value"]
        for item in template["resources"]
        if item["type"] == "Microsoft.ApiManagement/service/apis/policies"
    )

    assert "validate-jwt" in policy
    assert "/.well-known/openid-configuration" in policy
    assert "/v2.0/.well-known/openid-configuration" not in policy
    assert '<claim name="roles" match="any">' in policy
    assert template["parameters"]["requiredApplicationRole"]["defaultValue"] == "invoke_as_application"
    assert "parameters('requiredApplicationRole')" in policy
    assert 'name=\"Workspace Hash\"' in policy
    assert "x-dataforge-workspace-hash" in policy
    assert "authentication-managed-identity" in policy
    assert "llm-emit-token-metric" in policy
    assert "x-dataforge-gateway-correlation" in policy
    assert "x-dataforge-correlation-id" in policy
    assert "prompt" not in policy.lower()
    assert "completion" not in policy.lower()


def test_gateway_diagnostic_enables_application_insights_custom_metrics() -> None:
    template = json.loads(TELEMETRY_TEMPLATE.read_text(encoding="utf-8"))
    diagnostic = next(
        item
        for item in template["resources"]
        if item["type"] == "Microsoft.ApiManagement/service/apis/diagnostics"
    )

    assert diagnostic["properties"]["metrics"] is True
    for pipeline in ("frontend", "backend"):
        for direction in ("request", "response"):
            message = diagnostic["properties"][pipeline][direction]
            assert message["headers"] == []
            assert message["body"]["bytes"] == 0


def test_apim_resource_diagnostic_populates_gateway_and_llm_tables() -> None:
    template = json.loads(TELEMETRY_TEMPLATE.read_text(encoding="utf-8"))
    diagnostic = next(
        item
        for item in template["resources"]
        if item["type"]
        == "Microsoft.ApiManagement/service/providers/diagnosticSettings"
    )

    assert "logAnalyticsWorkspaceResourceId" in template["parameters"]
    assert diagnostic["properties"]["logAnalyticsDestinationType"] == "Dedicated"
    enabled = {
        item["category"]
        for item in diagnostic["properties"]["logs"]
        if item["enabled"]
    }
    assert enabled == {"GatewayLogs", "GatewayLlmLogs"}

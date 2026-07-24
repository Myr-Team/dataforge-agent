from __future__ import annotations

import json

from backend.finops.azure_apim import (
    ArmResponse,
    AzureApimPolicyClient,
    parse_apim_targets,
)


class _ArmTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, dict[str, str]]] = []
        self.active_revision = "4"
        self.policy_by_revision = {
            "4": (
                '<policies><inbound><base /></inbound><backend><base /></backend>'
                '<outbound><base /></outbound><on-error><base /></on-error></policies>'
            )
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> ArmResponse:
        self.calls.append((method, path, payload, dict(headers or {})))
        if method == "GET" and path.endswith("/apis/dataforge?api-version=2024-05-01"):
            return ArmResponse(
                status_code=200,
                body={
                    "properties": {
                        "apiRevision": self.active_revision,
                        "path": "openai",
                        "serviceUrl": "https://backend.example.test",
                    }
                },
                headers={"ETag": '"api-etag"'},
            )
        if "/policies/policy?" in path and method == "GET":
            revision = _revision(path, self.active_revision)
            return ArmResponse(
                status_code=200,
                body={
                    "properties": {
                        "format": "rawxml",
                        "value": self.policy_by_revision[revision],
                    }
                },
                headers={"ETag": '"policy-etag-v1"'},
            )
        if method == "PUT" and "/apis/dataforge;rev=" in path and "/policies/" not in path:
            return ArmResponse(status_code=201, body={}, headers={})
        if method == "PUT" and "/policies/policy?" in path:
            revision = _revision(path, self.active_revision)
            self.policy_by_revision[revision] = str(payload["properties"]["value"])
            return ArmResponse(status_code=201, body={}, headers={"ETag": '"candidate-etag"'})
        if method == "PUT" and "/releases/" in path:
            api_id = str(payload["properties"]["apiId"])
            self.active_revision = api_id.rsplit(";rev=", 1)[1]
            return ArmResponse(status_code=201, body={}, headers={})
        raise AssertionError(f"unexpected ARM request: {method} {path}")


class _SmokeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def probe(self, target, revision_id: str) -> dict[str, int]:
        self.calls.append((target.workspace_id, revision_id))
        return {"managed_identity_status": 200, "anonymous_status": 401}


def _revision(path: str, default: str) -> str:
    marker = ";rev="
    return path.split(marker, 1)[1].split("/", 1)[0] if marker in path else default


def test_apim_targets_are_server_owned_and_strictly_typed() -> None:
    targets = parse_apim_targets(
        json.dumps(
            {
                "ws-a": {
                    "subscription_id": "00000000-0000-0000-0000-000000000001",
                    "resource_group": "rg-dataforge",
                    "service_name": "apim-dataforge",
                    "api_id": "dataforge",
                    "gateway_url": "https://gateway.example.test/health",
                    "managed_identity_scope": "api://dataforge/.default",
                }
            }
        )
    )

    assert targets["ws-a"].api_id == "dataforge"
    assert "subscription_id" not in targets["ws-a"].public_descriptor()


def test_apim_client_creates_typed_candidate_reads_hash_and_releases_revision() -> None:
    targets = parse_apim_targets(
        json.dumps(
            {
                "ws-a": {
                    "subscription_id": "00000000-0000-0000-0000-000000000001",
                    "resource_group": "rg-dataforge",
                    "service_name": "apim-dataforge",
                    "api_id": "dataforge",
                    "gateway_url": "https://gateway.example.test/health",
                    "managed_identity_scope": "api://dataforge/.default",
                }
            }
        )
    )
    arm = _ArmTransport()
    smoke = _SmokeTransport()
    client = AzureApimPolicyClient(
        targets=targets,
        arm_transport=arm,
        smoke_transport=smoke,
        revision_factory=lambda: "finops-20260724",
        release_factory=lambda: "finops-release-20260724",
    )

    assert client.current_version("ws-a") == '"policy-etag-v1"'
    candidate = client.create_candidate(
        {
            "workspace_id": "ws-a",
            "quota_tokens": 1000,
            "window_seconds": 60,
            "rate_limit": None,
            "base_version": '"policy-etag-v1"',
        }
    )

    assert candidate["revision_id"] == "finops-20260724"
    assert candidate["previous_revision_id"] == "4"
    policy = arm.policy_by_revision["finops-20260724"]
    assert "<llm-token-limit" in policy
    assert 'tokens-per-minute="1000"' in policy
    assert "rawxml" not in candidate
    assert client.smoke_candidate("ws-a", "finops-20260724") == {
        "managed_identity_status": 200,
        "anonymous_status": 401,
    }
    assert client.read_policy_hash("ws-a", "finops-20260724") == candidate["policy_hash"]

    client.activate_candidate("ws-a", "finops-20260724")
    assert client.active_revision("ws-a") == "finops-20260724"
    client.activate_revision("ws-a", "4")
    assert client.active_revision("ws-a") == "4"

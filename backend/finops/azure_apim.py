from __future__ import annotations

import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl
from uuid import UUID, uuid4

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


_API_VERSION = "2024-05-01"
_MANAGEMENT_SCOPE = "https://management.azure.com/.default"
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class ApimTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
    subscription_id: UUID
    resource_group: str = Field(pattern=r"^[A-Za-z0-9_.()\-]{1,90}$")
    service_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,48}[A-Za-z0-9]$")
    api_id: str = Field(pattern=r"^[^*#&+:<>?;/]{1,80}$")
    gateway_url: str = Field(max_length=2048)
    managed_identity_scope: str = Field(min_length=1, max_length=512)

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway_url(cls, value: str) -> str:
        parsed = urlparse(str(value).strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("APIM gateway URL must be a credential-free HTTPS URL")
        return parsed.geturl()

    def public_descriptor(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "configured": True,
        }

    @property
    def api_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ApiManagement/service/{self.service_name}"
            f"/apis/{self.api_id}"
        )


@dataclass(frozen=True)
class ArmResponse:
    status_code: int
    body: dict[str, Any]
    headers: Mapping[str, str]


class ArmTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ArmResponse: ...


class SmokeTransport(Protocol):
    def probe(self, target: ApimTarget, revision_id: str) -> dict[str, int]: ...


def parse_apim_targets(raw: str | None) -> dict[str, ApimTarget]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("DF_FINOPS_APIM_TARGETS_JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("DF_FINOPS_APIM_TARGETS_JSON must be an object")
    targets: dict[str, ApimTarget] = {}
    try:
        for workspace_id, value in payload.items():
            if not isinstance(value, dict):
                raise ValueError("APIM target must be an object")
            target = ApimTarget.model_validate(
                {"workspace_id": str(workspace_id), **value}
            )
            targets[target.workspace_id] = target
    except ValidationError as exc:
        raise ValueError("DF_FINOPS_APIM_TARGETS_JSON contains an invalid target") from exc
    return targets


class AzureApimPolicyClient:
    """Azure APIM revision client with typed policy generation.

    Resource scope comes only from server-owned configuration. Public action
    payloads can control numeric token limits but cannot provide XML, scripts,
    URLs, or Azure resource identifiers.
    """

    def __init__(
        self,
        *,
        targets: Mapping[str, ApimTarget],
        arm_transport: ArmTransport,
        smoke_transport: SmokeTransport,
        revision_factory: Callable[[], str] | None = None,
        release_factory: Callable[[], str] | None = None,
    ) -> None:
        self._targets = dict(targets)
        self._arm = arm_transport
        self._smoke = smoke_transport
        self._revision_factory = revision_factory or (
            lambda: f"finops-{int(time.time())}-{uuid4().hex[:6]}"
        )
        self._release_factory = release_factory or (
            lambda: f"finops-{uuid4().hex[:20]}"
        )

    def current_version(self, workspace_id: str) -> str:
        target = self._target(workspace_id)
        response = self._arm.request("GET", _policy_path(target))
        return str(response.headers.get("ETag") or response.headers.get("etag") or "")

    def create_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "")
        target = self._target(workspace_id)
        current_api = self._arm.request("GET", _api_path(target))
        properties = current_api.body.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeError("APIM current API evidence is incomplete")
        previous_revision = str(properties.get("apiRevision") or "")
        path = str(properties.get("path") or "")
        service_url = str(properties.get("serviceUrl") or "")
        if not previous_revision:
            raise RuntimeError("APIM active revision evidence is incomplete")
        revision_id = _validated_revision(self._revision_factory())
        candidate_resource_id = f"{target.api_resource_id};rev={revision_id}"
        revision_properties: dict[str, Any] = {
            "path": path,
            "sourceApiId": target.api_resource_id,
            "apiRevisionDescription": "DataForge FinOps approval candidate",
        }
        if service_url:
            revision_properties["serviceUrl"] = service_url
        self._arm.request(
            "PUT",
            _api_path(target, revision_id),
            payload={"properties": revision_properties},
        )

        current_policy = self._arm.request("GET", _policy_path(target))
        current_policy_properties = current_policy.body.get("properties")
        if not isinstance(current_policy_properties, dict):
            raise RuntimeError("APIM current policy evidence is incomplete")
        policy_value = str(current_policy_properties.get("value") or "")
        candidate_policy = _with_token_limit(
            policy_value,
            workspace_id=workspace_id,
            quota_tokens=int(payload.get("quota_tokens") or 0),
            window_seconds=int(payload.get("window_seconds") or 0),
            rate_limit=(
                int(payload["rate_limit"])
                if payload.get("rate_limit") is not None
                else None
            ),
        )
        policy_hash = _policy_hash(candidate_policy)
        self._arm.request(
            "PUT",
            _policy_path(target, revision_id),
            payload={
                "properties": {
                    "format": "rawxml",
                    "value": candidate_policy,
                }
            },
        )
        return {
            "revision_id": revision_id,
            "previous_revision_id": previous_revision,
            "policy_hash": policy_hash,
            "candidate_resource_ref": hashlib.sha256(
                candidate_resource_id.encode("utf-8")
            ).hexdigest()[:20],
        }

    def smoke_candidate(self, workspace_id: str, revision_id: str) -> dict[str, int]:
        return self._smoke.probe(
            self._target(workspace_id),
            _validated_revision(revision_id),
        )

    def read_policy_hash(self, workspace_id: str, revision_id: str) -> str:
        target = self._target(workspace_id)
        response = self._arm.request(
            "GET",
            _policy_path(target, _validated_revision(revision_id)),
        )
        properties = response.body.get("properties")
        if not isinstance(properties, dict) or not properties.get("value"):
            raise RuntimeError("APIM policy read-back evidence is incomplete")
        return _policy_hash(str(properties["value"]))

    def activate_candidate(self, workspace_id: str, revision_id: str) -> None:
        self._release(workspace_id, revision_id)

    def active_revision(self, workspace_id: str) -> str:
        response = self._arm.request("GET", _api_path(self._target(workspace_id)))
        properties = response.body.get("properties")
        return str(properties.get("apiRevision") or "") if isinstance(properties, dict) else ""

    def activate_revision(self, workspace_id: str, revision_id: str) -> None:
        self._release(workspace_id, revision_id)

    def _release(self, workspace_id: str, revision_id: str) -> None:
        target = self._target(workspace_id)
        revision = _validated_revision(revision_id)
        release_id = _validated_release(self._release_factory())
        self._arm.request(
            "PUT",
            _release_path(target, release_id),
            payload={
                "properties": {
                    "apiId": f"{target.api_resource_id};rev={revision}",
                    "notes": "DataForge FinOps approved revision",
                }
            },
        )

    def _target(self, workspace_id: str) -> ApimTarget:
        target = self._targets.get(str(workspace_id or "").strip())
        if target is None:
            raise RuntimeError("APIM target is not configured for workspace")
        return target


class ManagedIdentityArmTransport:
    def __init__(
        self,
        *,
        credential: Any | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self._credential = credential
        self._timeout = max(1.0, min(float(timeout_seconds), 60.0))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ArmResponse:
        credential = self._credential or _managed_identity_credential()
        token = credential.get_token(_MANAGEMENT_SCOPE).token
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **dict(headers or {}),
        }
        try:
            response = requests.request(
                method,
                f"https://management.azure.com{path}",
                headers=request_headers,
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError("APIM ARM request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"APIM ARM request returned {response.status_code}")
        body = response.json() if response.content else {}
        if not isinstance(body, dict):
            body = {}
        return ArmResponse(
            status_code=response.status_code,
            body=body,
            headers=dict(response.headers),
        )


class ManagedIdentitySmokeTransport:
    def __init__(
        self,
        *,
        credential: Any | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        self._credential = credential
        self._timeout = max(1.0, min(float(timeout_seconds), 60.0))

    def probe(self, target: ApimTarget, revision_id: str) -> dict[str, int]:
        parsed = urlparse(target.gateway_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["api-revision"] = _validated_revision(revision_id)
        url = urlunparse(parsed._replace(query=urlencode(query)))
        credential = self._credential or _managed_identity_credential()
        token = credential.get_token(target.managed_identity_scope).token
        try:
            managed = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
                allow_redirects=False,
            )
            anonymous = requests.get(
                url,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError("APIM candidate smoke request failed") from exc
        return {
            "managed_identity_status": managed.status_code,
            "anonymous_status": anonymous.status_code,
        }


def build_azure_apim_policy_client() -> AzureApimPolicyClient | None:
    targets = parse_apim_targets(os.environ.get("DF_FINOPS_APIM_TARGETS_JSON"))
    if not targets:
        return None
    timeout = _bounded_float(
        os.environ.get("DF_FINOPS_APIM_TIMEOUT_SECONDS"),
        default=20,
        minimum=1,
        maximum=60,
    )
    return AzureApimPolicyClient(
        targets=targets,
        arm_transport=ManagedIdentityArmTransport(timeout_seconds=timeout),
        smoke_transport=ManagedIdentitySmokeTransport(timeout_seconds=timeout),
    )


def _managed_identity_credential() -> Any:
    from azure.identity import ManagedIdentityCredential

    client_id = str(os.environ.get("DF_AZURE_MANAGED_IDENTITY_CLIENT_ID") or "").strip()
    return (
        ManagedIdentityCredential(client_id=client_id)
        if client_id
        else ManagedIdentityCredential()
    )


def _api_path(target: ApimTarget, revision_id: str | None = None) -> str:
    identifier = target.api_id
    if revision_id:
        identifier = f"{identifier};rev={_validated_revision(revision_id)}"
    return (
        f"/subscriptions/{target.subscription_id}/resourceGroups/{quote(target.resource_group, safe='')}"
        f"/providers/Microsoft.ApiManagement/service/{quote(target.service_name, safe='')}"
        f"/apis/{quote(identifier, safe=';=')}?api-version={_API_VERSION}"
    )


def _policy_path(target: ApimTarget, revision_id: str | None = None) -> str:
    return _api_path(target, revision_id).replace(
        f"?api-version={_API_VERSION}",
        f"/policies/policy?api-version={_API_VERSION}",
    )


def _release_path(target: ApimTarget, release_id: str) -> str:
    return (
        f"/subscriptions/{target.subscription_id}/resourceGroups/{quote(target.resource_group, safe='')}"
        f"/providers/Microsoft.ApiManagement/service/{quote(target.service_name, safe='')}"
        f"/apis/{quote(target.api_id, safe='')}/releases/{quote(release_id, safe='')}"
        f"?api-version={_API_VERSION}"
    )


def _with_token_limit(
    policy_xml: str,
    *,
    workspace_id: str,
    quota_tokens: int,
    window_seconds: int,
    rate_limit: int | None,
) -> str:
    if quota_tokens <= 0:
        raise ValueError("APIM token quota must be positive")
    if rate_limit is not None and rate_limit <= 0:
        raise ValueError("APIM token rate must be positive")
    try:
        root = ET.fromstring(policy_xml)
    except ET.ParseError as exc:
        raise RuntimeError("APIM current policy XML is invalid") from exc
    if root.tag != "policies":
        raise RuntimeError("APIM current policy root is invalid")
    inbound = root.find("inbound")
    if inbound is None:
        raise RuntimeError("APIM current policy has no inbound section")
    counter_key = f"dataforge-finops:{workspace_id}"
    for element in list(inbound):
        if (
            element.tag == "llm-token-limit"
            and element.attrib.get("counter-key") == counter_key
        ):
            inbound.remove(element)
    attributes = {
        "counter-key": counter_key,
        "estimate-prompt-tokens": "false",
    }
    if window_seconds == 60:
        attributes["tokens-per-minute"] = str(rate_limit or quota_tokens)
    elif window_seconds in {3600, 86400}:
        attributes["token-quota"] = str(quota_tokens)
        attributes["token-quota-period"] = (
            "Hourly" if window_seconds == 3600 else "Daily"
        )
        if rate_limit is not None:
            attributes["tokens-per-minute"] = str(rate_limit)
    else:
        raise ValueError("APIM token window must be 60, 3600, or 86400 seconds")
    element = ET.Element("llm-token-limit", attributes)
    base_index = next(
        (index for index, child in enumerate(list(inbound)) if child.tag == "base"),
        -1,
    )
    inbound.insert(base_index + 1, element)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _policy_hash(policy_xml: str) -> str:
    try:
        canonical = ET.canonicalize(xml_data=policy_xml)
    except ET.ParseError as exc:
        raise RuntimeError("APIM policy XML cannot be canonicalized") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_revision(value: str) -> str:
    text = str(value or "").strip()
    if not _REVISION_PATTERN.fullmatch(text):
        raise ValueError("APIM revision identifier is invalid")
    return text


def _validated_release(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"^[A-Za-z0-9._-]{1,80}$", text):
        raise ValueError("APIM release identifier is invalid")
    return text


def _bounded_float(
    value: str | None,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import UUID


ALLOWED_TEMPLATE_VARIABLES = frozenset({
    "member_name", "budget_amount", "estimated_spend", "usage_percent",
    "threshold_percent", "period_label", "pricing_coverage", "portal_url",
})
_VARIABLE = re.compile(r"{{\s*([a-z_]+)\s*}}")
_SAFE_CATEGORIES = frozenset({"not_configured", "permission_required", "timeout", "service_unavailable"})


class AcsEmailError(RuntimeError):
    def __init__(self, category: str) -> None:
        if category not in _SAFE_CATEGORIES:
            category = "service_unavailable"
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class EmailMessage:
    recipient: str
    sender_display_name: str
    subject: str
    plain_text: str


@dataclass(frozen=True)
class EmailDeliveryResult:
    state: str
    sent_at: datetime | None
    safe_error_category: str | None
    provider_message_id: str | None = None


class _EmailClient(Protocol):
    def begin_send(self, message: Mapping[str, Any], *, operation_id: str) -> Any: ...


class AcsEmailSender:
    """Managed-identity ACS sender that intentionally exposes no SDK detail."""

    def __init__(self, *, client: _EmailClient, sender_address: str, poll_timeout_seconds: float = 10.0) -> None:
        if not sender_address or poll_timeout_seconds <= 0 or poll_timeout_seconds > 30:
            raise ValueError("invalid ACS sender configuration")
        self._client = client
        self._sender_address = sender_address
        self._poll_timeout_seconds = poll_timeout_seconds

    def send(self, message: EmailMessage, operation_id: str) -> EmailDeliveryResult:
        try:
            UUID(operation_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AcsEmailError("service_unavailable") from exc
        try:
            poller = self._client.begin_send(
                {
                    "senderAddress": self._sender_address,
                    "content": {"subject": message.subject, "plainText": message.plain_text},
                    "recipients": {"to": [{"address": message.recipient, "displayName": message.sender_display_name}]},
                },
                operation_id=operation_id,
            )
            result = poller.result(timeout=self._poll_timeout_seconds)
            done = getattr(poller, "done", None)
            if callable(done) and not done():
                raise AcsEmailError("timeout")
            if _delivery_state(result) != "succeeded":
                raise AcsEmailError("service_unavailable")
            provider_message_id = _delivery_id(result)
            if provider_message_id is None:
                raise AcsEmailError("service_unavailable")
            return EmailDeliveryResult(
                state="accepted",
                sent_at=datetime.now(timezone.utc),
                safe_error_category=None,
                provider_message_id=provider_message_id,
            )
        except AcsEmailError:
            raise
        except PermissionError as exc:
            raise AcsEmailError("permission_required") from exc
        except Exception as exc:
            category = "timeout" if _looks_like_timeout(exc) else "permission_required" if _looks_like_permission_error(exc) else "service_unavailable"
            raise AcsEmailError(category) from exc


def acs_email_sender_from_environment() -> AcsEmailSender:
    endpoint = str(os.environ.get("DF_ACS_EMAIL_ENDPOINT") or "").strip()
    sender_address = str(os.environ.get("DF_ACS_EMAIL_SENDER_ADDRESS") or "").strip()
    if not endpoint or not sender_address:
        raise AcsEmailError("not_configured")
    try:
        from azure.communication.email import EmailClient
        from azure.identity import ManagedIdentityCredential
        return AcsEmailSender(client=EmailClient(endpoint, ManagedIdentityCredential()), sender_address=sender_address)
    except AcsEmailError:
        raise
    except Exception as exc:
        category = "permission_required" if _looks_like_permission_error(exc) else "service_unavailable"
        raise AcsEmailError(category) from exc


def render_template(template: str, values: Mapping[str, str]) -> str:
    if not isinstance(template, str) or not isinstance(values, Mapping):
        raise ValueError("template_variable_not_allowed")
    if set(values) - ALLOWED_TEMPLATE_VARIABLES:
        raise ValueError("template_variable_not_allowed")
    names = set(_VARIABLE.findall(template))
    if names - ALLOWED_TEMPLATE_VARIABLES or names - set(values):
        raise ValueError("template_variable_not_allowed")
    rendered = _VARIABLE.sub(lambda match: str(values[match.group(1)]), template)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("template_variable_not_allowed")
    return rendered


def validate_template(template: str) -> None:
    """Validate placeholder syntax without retaining any caller-provided content."""
    render_template(template, {name: "" for name in ALLOWED_TEMPLATE_VARIABLES})


def _delivery_state(result: Any) -> str:
    if isinstance(result, Mapping):
        value = result.get("status") or result.get("state")
    else:
        value = getattr(result, "status", None) or getattr(result, "state", None)
    return str(value or "").strip().lower()


def _delivery_id(result: Any) -> str | None:
    value = result.get("id") if isinstance(result, Mapping) else getattr(result, "id", None)
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _looks_like_permission_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    status = status if status is not None else getattr(response, "status_code", None)
    return status in {401, 403} or any(marker in type(exc).__name__.lower() for marker in ("authentication", "authorization", "forbidden", "permission", "credential"))


def _looks_like_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


__all__ = ["ALLOWED_TEMPLATE_VARIABLES", "AcsEmailError", "AcsEmailSender", "EmailDeliveryResult", "EmailMessage", "acs_email_sender_from_environment", "render_template", "validate_template"]

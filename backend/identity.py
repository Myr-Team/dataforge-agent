from __future__ import annotations

import base64
import hmac
import json
import os
from typing import Any, Mapping
from urllib.parse import unquote


DEFAULT_OWNER_EMAIL = "fuzihao@gdjiuyun.onmicrosoft.com"
DEFAULT_OWNER_NAME = "傅子豪"
PLACEHOLDER_EMAILS = {
    "",
    "local.demo@dataforge",
    "owner@example.com",
    "fuzh084711@gmail.com",
}


def actor_from_request(request: Any | None, *, fallback: bool = True) -> dict[str, Any]:
    headers = getattr(request, "headers", None)
    return actor_from_headers(headers, fallback=fallback)


def actor_from_headers(headers: Mapping[str, Any] | None, *, fallback: bool = True) -> dict[str, Any]:
    if not headers:
        return default_actor() if fallback else {}
    expected_proxy_secret = _clean(os.environ.get("DF_WEB_PROXY_SECRET"))
    supplied_proxy_secret = _clean(_header(headers, "x-dataforge-proxy-secret"))
    trusted_proxy = bool(
        expected_proxy_secret
        and supplied_proxy_secret
        and hmac.compare_digest(expected_proxy_secret, supplied_proxy_secret)
    )
    principal = _decoded_easy_auth_principal(_header(headers, "x-ms-client-principal")) if trusted_proxy else {}
    claims = _claims(principal)
    header_name = _clean(_header(headers, "x-ms-client-principal-name")) if trusted_proxy else ""
    email = (
        _claim_value(claims, "preferred_username", "upn", "email", "emailaddress")
        or _clean(principal.get("userDetails"))
        or _clean(principal.get("user_details"))
        or header_name
    )
    email = _valid_email(email)
    name = (
        _claim_value(claims, "name", "displayname", "given_name")
        or _clean(principal.get("name"))
        or _clean(principal.get("displayName"))
        or ""
    )
    actor_id = (
        _claim_value(claims, "oid", "objectidentifier", "sub", "nameidentifier")
        or (_clean(_header(headers, "x-ms-client-principal-id")) if trusted_proxy else "")
    )
    tenant_id = _claim_value(claims, "tid", "tenantid")
    roles = _claim_values(claims, "roles", "role")
    groups = _claim_values(claims, "groups", "group")
    has_easy_auth_actor = bool(principal or actor_id or email)
    if not has_easy_auth_actor:
        client_actor = _decoded_client_actor(_header(headers, "x-dataforge-actor"))
        if client_actor:
            return _sanitize_actor(client_actor, fallback=fallback)

    source = "easy_auth" if has_easy_auth_actor else "workspace_default"
    actor = _sanitize_actor(
        {
            "name": name,
            "email": email,
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "roles": roles,
            "groups": groups,
            "source": source,
        },
        fallback=fallback,
    )
    if actor and actor.get("source") == "workspace_default" and source == "easy_auth":
        actor["source"] = "easy_auth"
    return actor


def actor_from_ui_context(ui_context: dict[str, Any] | None, *, fallback: bool = True) -> dict[str, Any]:
    context = ui_context if isinstance(ui_context, dict) else {}
    actor = context.get("actor")
    if not isinstance(actor, dict):
        actor = context.get("user") if isinstance(context.get("user"), dict) else {}
    return _sanitize_actor(actor, fallback=fallback)


def merge_actor_into_ui_context(ui_context: dict[str, Any] | None, actor: dict[str, Any]) -> dict[str, Any]:
    context = dict(ui_context or {}) if isinstance(ui_context, dict) else {}
    if actor:
        context["actor"] = public_actor(actor)
    return context


def default_actor() -> dict[str, Any]:
    return {
        "name": os.environ.get("DF_WORKSPACE_OWNER_NAME") or DEFAULT_OWNER_NAME,
        "email": os.environ.get("DF_WORKSPACE_OWNER_EMAIL") or os.environ.get("USER_EMAIL") or DEFAULT_OWNER_EMAIL,
        "actor_id": "",
        "tenant_id": "",
        "roles": [],
        "groups": [],
        "source": "workspace_default",
    }


def public_actor(actor: dict[str, Any] | None) -> dict[str, Any]:
    clean = _sanitize_actor(actor or {}, fallback=False)
    allowed = ("name", "email", "actor_id", "tenant_id", "source", "roles", "groups")
    return {key: clean[key] for key in allowed if clean.get(key) not in (None, "", [], {})}


def is_trusted_identity(actor: Mapping[str, Any] | None) -> bool:
    clean = _sanitize_actor(dict(actor or {}), fallback=False)
    return str(clean.get("source") or "") == "easy_auth" and bool(str(clean.get("actor_id") or "").strip())


def is_trusted_tenant_identity(actor: Mapping[str, Any] | None) -> bool:
    clean = _sanitize_actor(dict(actor or {}), fallback=False)
    return is_trusted_identity(clean) and bool(str(clean.get("tenant_id") or "").strip())


def canonical_actor_identity(actor: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Return the tenant-scoped stable identity used for authorization and review checks."""
    clean = _sanitize_actor(dict(actor or {}), fallback=False)
    actor_id = str(clean.get("actor_id") or "").strip().lower()
    tenant_id = str(clean.get("tenant_id") or "").strip().lower()
    if not actor_id or not tenant_id:
        return None
    return (tenant_id, actor_id)


def actor_for_history(actor: dict[str, Any] | None) -> dict[str, Any]:
    clean = _sanitize_actor(actor or {}, fallback=True)
    return {
        "user": clean.get("name") or _display_name_from_email(str(clean.get("email") or "")),
        "email": clean.get("email") or None,
        "actor_id": clean.get("actor_id") or None,
        "tenant_id": clean.get("tenant_id") or None,
        "source": clean.get("source") or "workspace_default",
    }


def member_from_actor(actor: dict[str, Any] | None, *, role: str = "owner", status: str = "active") -> dict[str, Any]:
    clean = _sanitize_actor(actor or {}, fallback=True)
    return {
        "user": clean.get("name") or _display_name_from_email(str(clean.get("email") or "")),
        "email": clean.get("email") or "",
        "actor_id": clean.get("actor_id") or "",
        "tenant_id": clean.get("tenant_id") or "",
        "role": role,
        "status": status,
        "source": clean.get("source") or "workspace_default",
    }


def _sanitize_actor(actor: dict[str, Any] | None, *, fallback: bool) -> dict[str, Any]:
    source = dict(actor or {}) if isinstance(actor, dict) else {}
    email = _valid_email(source.get("email"))
    name = _clean(source.get("name") or source.get("user"))
    actor_id = _clean(source.get("actor_id") or source.get("id") or source.get("oid"))
    tenant_id = _clean(source.get("tenant_id") or source.get("tid"))
    roles = _string_list(source.get("roles"))
    groups = _string_list(source.get("groups"))
    source_name = _clean(source.get("source")) or ("ui_context" if source else "")

    placeholder = email.lower() in PLACEHOLDER_EMAILS
    if placeholder:
        email = ""
    if not name or name.lower() == "demo user":
        name = ""

    if not email and fallback:
        base = default_actor()
        email = str(base.get("email") or DEFAULT_OWNER_EMAIL)
        if not name:
            name = str(base.get("name") or DEFAULT_OWNER_NAME)
        source_name = "workspace_default"
    if not name and email:
        name = _display_name_from_email(email)
    result = {
        "name": name,
        "email": email,
        "actor_id": actor_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "groups": groups,
        "source": source_name or "workspace_default",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _decoded_easy_auth_principal(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        padded = raw + ("=" * (-len(raw) % 4))
        data = base64.urlsafe_b64decode(padded)
        parsed = json.loads(data.decode("utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decoded_client_actor(raw: str | None) -> dict[str, Any]:
    text = _clean(raw)
    if not text:
        return {}
    candidates = [text]
    decoded = unquote(text)
    if decoded != text:
        candidates.append(decoded)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        actor = {
            "name": parsed.get("name") or parsed.get("user"),
            "email": parsed.get("email"),
            "actor_id": parsed.get("actor_id") or parsed.get("id") or parsed.get("oid"),
            "tenant_id": parsed.get("tenant_id") or parsed.get("tid"),
            "source": "client_actor",
        }
        return actor
    return {}


def _claims(principal: dict[str, Any]) -> list[dict[str, Any]]:
    raw = principal.get("claims") or principal.get("user_claims") or []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _claim_value(claims: list[dict[str, Any]], *names: str) -> str:
    values = _claim_values(claims, *names)
    return values[0] if values else ""


def _claim_values(claims: list[dict[str, Any]], *names: str) -> list[str]:
    wanted = {name.lower() for name in names}
    values: list[str] = []
    for claim in claims:
        typ = str(claim.get("typ") or claim.get("type") or claim.get("name") or "").lower()
        tail = typ.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if typ not in wanted and tail not in wanted:
            continue
        for raw in (claim.get("val"), claim.get("value")):
            for item in _string_list(raw):
                if item not in values:
                    values.append(item)
    return values


def _header(headers: Mapping[str, Any], key: str) -> str:
    try:
        return _clean(headers.get(key))
    except Exception:
        pass
    lowered = key.lower()
    for item_key, value in dict(headers).items():
        if str(item_key).lower() == lowered:
            return _clean(value)
    return ""


def _valid_email(value: Any) -> str:
    text = _clean(value)
    if not text or "@" not in text:
        return ""
    suffix = text.rsplit("@", 1)[-1]
    return text if "." in suffix else ""


def _display_name_from_email(email: str) -> str:
    if email.lower() == DEFAULT_OWNER_EMAIL:
        return DEFAULT_OWNER_NAME
    local = email.split("@", 1)[0] if email else ""
    return " ".join(part for part in local.replace("_", ".").replace("-", ".").split(".") if part) or "DataForge User"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean(item) for item in value if _clean(item)]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_clean(item) for item in value if _clean(item)]
    text = _clean(value)
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _clean(value: Any) -> str:
    return str(value or "").strip()

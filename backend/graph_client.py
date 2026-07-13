from __future__ import annotations

import os
import time
from typing import Any

import requests


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_HEADER_CANDIDATES = (
    "x-ms-token-aad-access-token",
    "x-ms-token-aad-id-token",
)

_APP_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}


class GraphClientError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 503, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message,
            "status": self.status,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def graph_token_from_request(request: Any | None = None) -> str:
    headers = getattr(request, "headers", None)
    for key in TOKEN_HEADER_CANDIDATES:
        token = _header(headers, key)
        if token:
            return token
    return _app_only_token()


def search_entra_users(query: str, request: Any | None = None, *, limit: int = 8) -> dict[str, Any]:
    token = graph_token_from_request(request)
    if not token:
        return _unavailable(
            "graph_token_missing",
            "Microsoft Graph token is not available. Enable Easy Auth token store on the backend path or configure app-only Graph credentials.",
        )
    safe_limit = max(1, min(int(limit or 8), 20))
    params = {
        "$select": "id,displayName,mail,userPrincipalName,userType",
        "$top": str(safe_limit),
    }
    filter_text = _user_filter(query)
    if filter_text:
        params["$filter"] = filter_text
    try:
        payload = graph_request("GET", "/users", token, params=params)
    except GraphClientError as exc:
        if exc.code == "graph_permission_denied":
            return _unavailable(
                "graph_directory_search_permission_denied",
                "Microsoft Graph directory search requires User.ReadBasic.All or Directory.Read.All with admin consent. Exact-email invitations can still be sent when User.Invite.All is available.",
                status=exc.status,
            )
        return _unavailable(exc.code, exc.message, status=exc.status, detail=exc.detail)
    return {
        "connected": True,
        "source": "microsoft_graph",
        "users": [_normalize_graph_user(item) for item in payload.get("value") or [] if isinstance(item, dict)],
    }


def send_graph_invitation(
    email: str,
    redirect_url: str,
    request: Any | None = None,
    *,
    display_name: str = "",
    message: str = "",
) -> dict[str, Any]:
    token = graph_token_from_request(request)
    if not token:
        raise GraphClientError(
            "graph_token_missing",
            "Microsoft Graph token is not available. The workspace member can still be added locally, but Entra email invite needs Graph permissions.",
        )
    body: dict[str, Any] = {
        "invitedUserEmailAddress": email,
        "inviteRedirectUrl": redirect_url,
        "sendInvitationMessage": True,
    }
    if display_name or message:
        body["invitedUserMessageInfo"] = {
            "customizedMessageBody": message or f"You have been invited to collaborate in DataForge.",
        }
        if display_name:
            body["invitedUserDisplayName"] = display_name
    payload = graph_request("POST", "/invitations", token, json_payload=body)
    invited_user = payload.get("invitedUser") if isinstance(payload.get("invitedUser"), dict) else {}
    return {
        "status": "sent",
        "source": "microsoft_graph",
        "invitation_id": payload.get("id") or "",
        "invited_user_id": invited_user.get("id") or "",
        "email": email,
    }


def graph_request(
    method: str,
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{GRAPH_BASE_URL}{path if path.startswith('/') else '/' + path}"
    request_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if json_payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    try:
        response = requests.request(method, url, headers=request_headers, params=params, json=json_payload, timeout=8)
    except requests.Timeout as exc:
        raise GraphClientError("graph_timeout", "Microsoft Graph request timed out.", status=504) from exc
    except requests.RequestException as exc:
        raise GraphClientError("graph_network_error", "Microsoft Graph request failed before a response was received.", status=503) from exc
    if response.status_code in {401, 403}:
        raise GraphClientError(
            "graph_permission_denied",
            "Microsoft Graph token is missing the required permissions or admin consent.",
            status=response.status_code,
            detail=_graph_error_code(response),
        )
    if response.status_code >= 400:
        raise GraphClientError(
            "graph_request_failed",
            "Microsoft Graph returned an error.",
            status=response.status_code,
            detail=_graph_error_code(response),
        )
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _app_only_token() -> str:
    tenant = os.environ.get("GRAPH_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("GRAPH_CLIENT_ID") or os.environ.get("AZURE_CLIENT_ID") or os.environ.get("MICROSOFT_CLIENT_ID")
    client_secret = os.environ.get("GRAPH_CLIENT_SECRET") or os.environ.get("AZURE_CLIENT_SECRET") or os.environ.get("MICROSOFT_CLIENT_SECRET")
    if not tenant or not client_id or not client_secret:
        return ""
    now = time.time()
    if _APP_TOKEN_CACHE.get("token") and float(_APP_TOKEN_CACHE.get("expires_at") or 0) > now + 60:
        return str(_APP_TOKEN_CACHE.get("token") or "")
    try:
        response = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": GRAPH_SCOPE,
            },
            timeout=8,
        )
    except requests.RequestException:
        return ""
    if response.status_code >= 400:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    token = str(payload.get("access_token") or "")
    if not token:
        return ""
    expires_in = int(payload.get("expires_in") or 3600)
    _APP_TOKEN_CACHE.update({"token": token, "expires_at": now + max(60, expires_in)})
    return token


def _normalize_graph_user(item: dict[str, Any]) -> dict[str, Any]:
    upn = str(item.get("userPrincipalName") or "").strip()
    email = str(item.get("mail") or "").strip() or upn
    return {
        "id": str(item.get("id") or "").strip(),
        "display_name": str(item.get("displayName") or "").strip() or _name_from_email(email),
        "email": email,
        "user_principal_name": upn,
        "user_type": str(item.get("userType") or "").strip(),
        "source": "microsoft_graph",
    }


def _user_filter(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    escaped = text.replace("'", "''")
    clauses = [f"startswith(displayName,'{escaped}')", f"startswith(userPrincipalName,'{escaped}')"]
    if "@" in escaped:
        clauses.append(f"startswith(mail,'{escaped}')")
    return " or ".join(clauses)


def _unavailable(code: str, message: str, *, status: int = 503, detail: Any = None) -> dict[str, Any]:
    error = {"code": code, "message": message, "status": status}
    if detail:
        error["detail"] = detail
    return {
        "connected": False,
        "source": "microsoft_graph",
        "users": [],
        "error": error,
    }


def _graph_error_code(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:240]
    error = payload.get("error") if isinstance(payload, dict) else {}
    if isinstance(error, dict):
        return str(error.get("code") or error.get("message") or "")[:240]
    return ""


def _header(headers: Any, key: str) -> str:
    if not headers:
        return ""
    try:
        return str(headers.get(key) or "").strip()
    except Exception:
        pass
    lowered = key.lower()
    try:
        items = dict(headers).items()
    except Exception:
        return ""
    for item_key, value in items:
        if str(item_key).lower() == lowered:
            return str(value or "").strip()
    return ""


def _name_from_email(email: str) -> str:
    local = email.split("@", 1)[0] if email else ""
    return " ".join(part for part in local.replace("_", ".").replace("-", ".").split(".") if part) or "DataForge User"

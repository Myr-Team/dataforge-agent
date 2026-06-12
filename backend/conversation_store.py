from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .blob_store import download_blob_json, upload_blob_json
except ImportError:
    from blob_store import download_blob_json, upload_blob_json


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_DIR = ROOT / "generated-outputs" / "conversations"
CONVERSATION_REGISTRY_BLOB = "registry/conversations.json"
CONVERSATION_BLOB_PREFIX = "conversations"

_LOCK = threading.RLock()


def get_conversation(conversation_id: str) -> dict[str, Any]:
    data = _load_conversation(conversation_id)
    if not data:
        raise FileNotFoundError(conversation_id)
    return data


def list_conversations(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in _local_registry_items():
        if item.get("conversation_id"):
            by_id[str(item["conversation_id"])] = item
    registry = download_blob_json(CONVERSATION_REGISTRY_BLOB) or {}
    for item in registry.get("conversations") or []:
        if isinstance(item, dict) and item.get("conversation_id"):
            by_id[str(item["conversation_id"])] = item
    items = list(by_id.values())
    if workspace_id:
        items = [item for item in items if item.get("workspace_id") == workspace_id]
    return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def append_message(
    conversation_id: str,
    *,
    workspace_id: str,
    role: str,
    text: str,
    verdict: str | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    with _LOCK:
        conversation = _load_conversation(conversation_id) or {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        conversation["workspace_id"] = conversation.get("workspace_id") or workspace_id
        conversation["updated_at"] = now
        messages = list(conversation.get("messages") or [])
        message: dict[str, Any] = {"role": role, "text": str(text or ""), "time": now}
        if verdict:
            message["verdict"] = verdict
            conversation["last_verdict"] = verdict
        messages.append(message)
        conversation["messages"] = messages
        conversation["title"] = conversation.get("title") or _title_from_messages(messages)
        conversation["turn_count"] = sum(1 for item in messages if item.get("role") == "user")
        return _persist_conversation(conversation)


def conversation_context(conversation_id: str | None, *, limit: int = 6) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    data = _load_conversation(conversation_id)
    if not data:
        return []
    messages = [item for item in data.get("messages") or [] if isinstance(item, dict)]
    return messages[-limit:]


def _persist_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(str(conversation.get("conversation_id") or "conversation"))
    path = CONVERSATION_DIR / f"{safe}.json"
    conversation["local_path"] = str(path)
    summary = _summary(conversation)
    blob_name = f"{CONVERSATION_BLOB_PREFIX}/{safe}.json"
    try:
        upload_blob_json(blob_name, conversation)
        registry = download_blob_json(CONVERSATION_REGISTRY_BLOB) or {}
        entries = [item for item in registry.get("conversations") or [] if isinstance(item, dict)]
        entries = [item for item in entries if item.get("conversation_id") != conversation.get("conversation_id")]
        entries.append(summary)
        entries = sorted(entries, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:500]
        upload_blob_json(CONVERSATION_REGISTRY_BLOB, {"version": 1, "conversations": entries})
        conversation["persistence"] = {"mode": "local_and_blob", "blob_name": blob_name}
    except Exception as exc:
        conversation["persistence"] = {"mode": "local_only", "error": f"{type(exc).__name__}: {exc}"[:500]}
    path.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")
    return conversation


def _load_conversation(conversation_id: str) -> dict[str, Any] | None:
    safe = _safe_name(conversation_id)
    blob = download_blob_json(f"{CONVERSATION_BLOB_PREFIX}/{safe}.json")
    if blob:
        return blob
    path = CONVERSATION_DIR / f"{safe}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def _local_registry_items() -> list[dict[str, Any]]:
    if not CONVERSATION_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in CONVERSATION_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            items.append(_summary(data))
    return items


def _summary(conversation: dict[str, Any]) -> dict[str, Any]:
    messages = [item for item in conversation.get("messages") or [] if isinstance(item, dict)]
    return {
        "conversation_id": conversation.get("conversation_id"),
        "workspace_id": conversation.get("workspace_id"),
        "title": conversation.get("title") or _title_from_messages(messages),
        "updated_at": conversation.get("updated_at"),
        "turn_count": sum(1 for item in messages if item.get("role") == "user"),
        "last_verdict": conversation.get("last_verdict"),
    }


def _title_from_messages(messages: list[dict[str, Any]]) -> str:
    for item in messages:
        if item.get("role") == "user":
            text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            if text:
                return text[:48]
    return "Untitled conversation"


def _safe_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "")).strip("-")
    if text:
        return text[:120]
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

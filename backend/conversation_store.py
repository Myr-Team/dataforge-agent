from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .blob_store import download_blob_json, upload_blob_json
    from .identity import is_trusted_tenant_identity, public_actor
except ImportError:
    from blob_store import download_blob_json, upload_blob_json
    from identity import is_trusted_tenant_identity, public_actor


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_DIR = ROOT / "generated-outputs" / "conversations"
CONVERSATION_REGISTRY_BLOB = "registry/conversations.json"
CONVERSATION_BLOB_PREFIX = "conversations"

_LOCK = threading.RLock()
_BLOB_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dataforge-conversation-blob")
_ALLOWED_DURABLE_FACT_KINDS = frozenset(
    {
        "verified_constraint",
        "selected_metric",
        "accepted_scope",
        "evidence_revision",
    }
)


def get_conversation(conversation_id: str) -> dict[str, Any]:
    data = _load_conversation(conversation_id)
    if not data:
        raise FileNotFoundError(conversation_id)
    return data


def list_conversations(workspace_id: str | None = None, *, include_system: bool = False) -> list[dict[str, Any]]:
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
    if not include_system:
        items = [item for item in items if item.get("visibility") != "system_activity"]
    return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def append_message(
    conversation_id: str,
    *,
    workspace_id: str,
    role: str,
    text: str,
    verdict: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    actor: dict[str, Any] | None = None,
    remote_load: bool = True,
) -> dict[str, Any]:
    now = _utc_now()
    clean_actor = public_actor(actor or {}) if actor else {}
    with _LOCK:
        loader = _load_conversation if remote_load else _load_local_conversation
        conversation = loader(conversation_id) or {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "origin": "conversation",
            "visibility": "default",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        conversation["workspace_id"] = conversation.get("workspace_id") or workspace_id
        conversation["updated_at"] = now
        messages = list(conversation.get("messages") or [])
        message: dict[str, Any] = {"message_id": f"message_{uuid4().hex[:20]}", "role": role, "text": str(text or ""), "time": now}
        if actor is not None:
            message["actor"] = clean_actor
            message["trusted_identity"] = is_trusted_tenant_identity(clean_actor)
            if clean_actor:
                conversation["actors"] = _merge_actor(conversation.get("actors"), clean_actor)
        if verdict:
            message["verdict"] = verdict
            conversation["last_verdict"] = verdict
        if citations:
            slim: list[dict[str, Any]] = []
            for c in citations[:8]:
                if isinstance(c, dict):
                    slim.append({k: c.get(k) for k in ("marker", "snippet", "quote", "confidence", "ref", "source_label", "source_file") if c.get(k) is not None})
            if slim:
                message["citations"] = slim
        messages.append(message)
        conversation["messages"] = messages
        conversation["title"] = conversation.get("title") or _title_from_messages(messages)
        conversation["turn_count"] = sum(1 for item in messages if item.get("role") == "user")
        return _persist_conversation(conversation)


def link_run(conversation_id: str, *, workspace_id: str, run_id: str) -> dict[str, Any]:
    """Link a human conversation to an execution run without adding a message."""
    now = _utc_now()
    with _LOCK:
        conversation = _load_conversation(conversation_id)
        if not conversation:
            raise FileNotFoundError(conversation_id)
        if conversation.get("workspace_id") and conversation.get("workspace_id") != workspace_id:
            raise ValueError("conversation does not belong to the requested workspace")
        linked_run_ids = [str(item) for item in (conversation.get("linked_run_ids") or []) if str(item)]
        if run_id not in linked_run_ids:
            linked_run_ids.append(run_id)
        conversation["workspace_id"] = conversation.get("workspace_id") or workspace_id
        conversation["origin"] = conversation.get("origin") or "conversation"
        conversation["visibility"] = conversation.get("visibility") or "default"
        conversation["linked_run_ids"] = linked_run_ids[-50:]
        conversation["updated_at"] = now
        return _persist_conversation(conversation)


def conversation_context(conversation_id: str | None, *, limit: int = 6) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    data = _load_conversation(conversation_id)
    if not data:
        return []
    messages = [item for item in data.get("messages") or [] if isinstance(item, dict)]
    return messages[-limit:]


def record_durable_fact(
    conversation_id: str,
    *,
    workspace_id: str,
    fact: dict[str, Any],
    remote_load: bool = True,
) -> dict[str, Any]:
    kind = str((fact or {}).get("kind") or "").strip()
    if kind not in _ALLOWED_DURABLE_FACT_KINDS:
        raise ValueError("durable fact kind is not allowlisted")
    text = str((fact or {}).get("text") or "").strip()
    if not text:
        raise ValueError("durable fact text is required")
    now = _utc_now()
    clean_workspace_id = str(workspace_id or "").strip()
    clean_conversation_id = str(conversation_id or "").strip()
    if not clean_workspace_id or not clean_conversation_id:
        raise ValueError("conversation_id and workspace_id are required")
    with _LOCK:
        loader = _load_conversation if remote_load else _load_local_conversation
        conversation = loader(clean_conversation_id) or {
            "conversation_id": clean_conversation_id,
            "workspace_id": clean_workspace_id,
            "origin": "conversation",
            "visibility": "default",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        if conversation.get("workspace_id") and conversation.get("workspace_id") != clean_workspace_id:
            raise ValueError("conversation does not belong to the requested workspace")
        stored = {
            "fact_id": str((fact or {}).get("fact_id") or (fact or {}).get("id") or f"fact_{uuid4().hex[:12]}").strip(),
            "scope": f"{clean_workspace_id}:{clean_conversation_id}",
            "workspace_id": clean_workspace_id,
            "conversation_id": clean_conversation_id,
            "kind": kind,
            "text": text,
            "source_run_id": str((fact or {}).get("source_run_id") or "").strip() or None,
            "source_revision": str((fact or {}).get("source_revision") or "").strip() or None,
            "evidence_ref": str((fact or {}).get("evidence_ref") or "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        durable_facts = [item for item in (conversation.get("durable_facts") or []) if isinstance(item, dict)]
        durable_facts = [
            item
            for item in durable_facts
            if str(item.get("fact_id") or "").strip() != stored["fact_id"]
        ]
        durable_facts.append({key: value for key, value in stored.items() if value not in (None, "")})
        conversation["workspace_id"] = clean_workspace_id
        conversation["updated_at"] = now
        conversation["durable_facts"] = durable_facts[-50:]
        _persist_conversation(conversation)
        return conversation["durable_facts"][-1]


def conversation_durable_facts(
    conversation_id: str | None,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    data = _load_conversation(conversation_id)
    if not data:
        return []
    clean_workspace_id = str(workspace_id or data.get("workspace_id") or "").strip()
    scope = f"{clean_workspace_id}:{conversation_id}" if clean_workspace_id else ""
    facts = [item for item in data.get("durable_facts") or [] if isinstance(item, dict)]
    filtered: list[dict[str, Any]] = []
    for item in facts:
        if scope and str(item.get("scope") or "").strip() != scope:
            continue
        if str(item.get("kind") or "").strip() not in _ALLOWED_DURABLE_FACT_KINDS:
            continue
        filtered.append(dict(item))
    return filtered[-limit:]


def stable_message_id(workspace_id: str, conversation_id: str, index: int, message: dict[str, Any]) -> str:
    """Derive a replay-stable ID for historical messages that predate message_id."""
    immutable = {
        "workspace_id": str(workspace_id),
        "conversation_id": str(conversation_id),
        "index": int(index),
        "role": str(message.get("role") or ""),
        "text": str(message.get("text") or ""),
        "time": str(message.get("time") or message.get("created_at") or message.get("updated_at") or ""),
        "verdict": str(message.get("verdict") or ""),
    }
    encoded = json.dumps(immutable, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"legacy_message_{hashlib.sha256(encoded).hexdigest()[:20]}"


def _persist_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(str(conversation.get("conversation_id") or "conversation"))
    path = CONVERSATION_DIR / f"{safe}.json"
    conversation["local_path"] = str(path)
    summary = _summary(conversation)
    blob_name = f"{CONVERSATION_BLOB_PREFIX}/{safe}.json"
    conversation["persistence"] = {"mode": "local_and_blob_async", "blob_name": blob_name}
    path.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot = json.loads(json.dumps(conversation, ensure_ascii=False))
    _BLOB_EXECUTOR.submit(_persist_conversation_blob, snapshot, summary, blob_name)
    return conversation


def _persist_conversation_blob(conversation: dict[str, Any], summary: dict[str, Any], blob_name: str) -> None:
    try:
        upload_blob_json(blob_name, conversation)
        registry = download_blob_json(CONVERSATION_REGISTRY_BLOB) or {}
        entries = [item for item in registry.get("conversations") or [] if isinstance(item, dict)]
        entries = [item for item in entries if item.get("conversation_id") != conversation.get("conversation_id")]
        entries.append(summary)
        entries = sorted(entries, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:500]
        upload_blob_json(CONVERSATION_REGISTRY_BLOB, {"version": 1, "conversations": entries})
    except Exception:
        return


def _load_conversation(conversation_id: str) -> dict[str, Any] | None:
    local = _load_local_conversation(conversation_id)
    if local:
        return local
    safe = _safe_name(conversation_id)
    path = CONVERSATION_DIR / f"{safe}.json"
    blob = download_blob_json(f"{CONVERSATION_BLOB_PREFIX}/{safe}.json")
    if blob:
        try:
            CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return blob
    return None


def _load_local_conversation(conversation_id: str) -> dict[str, Any] | None:
    safe = _safe_name(conversation_id)
    path = CONVERSATION_DIR / f"{safe}.json"
    if not path.exists():
        return None
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
        "origin": conversation.get("origin") or "conversation",
        "visibility": conversation.get("visibility") or "default",
        "linked_run_ids": [str(item) for item in (conversation.get("linked_run_ids") or []) if str(item)],
        "title": conversation.get("title") or _title_from_messages(messages),
        "updated_at": conversation.get("updated_at"),
        "turn_count": sum(1 for item in messages if item.get("role") == "user"),
        "last_verdict": conversation.get("last_verdict"),
        "actors": conversation.get("actors") or [],
    }


def _merge_actor(existing: Any, actor: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [item for item in (existing or []) if isinstance(item, dict)]
    key = str(actor.get("email") or actor.get("actor_id") or actor.get("name") or "").lower()
    rows = [
        item
        for item in rows
        if str(item.get("email") or item.get("actor_id") or item.get("name") or "").lower() != key
    ]
    rows.insert(0, actor)
    return rows[:20]


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

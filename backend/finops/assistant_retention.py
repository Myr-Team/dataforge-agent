from __future__ import annotations

import json

from .sql_assistant import SqlAssistantConversationStore


def main() -> int:
    try:
        store = _store()
        purged = store.purge_expired()
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "category": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            {"status": "completed", "purged_conversations": int(purged or 0)},
            separators=(",", ":"),
        )
    )
    return 0


def _store() -> SqlAssistantConversationStore:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    return SqlAssistantConversationStore(
        connection_factory=build_lineage_sql_connection_factory()
    )


if __name__ == "__main__":
    raise SystemExit(main())

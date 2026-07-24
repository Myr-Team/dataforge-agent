from __future__ import annotations

try:
    from ..lineage_sql import build_lineage_sql_connection_factory
except ImportError:
    from lineage_sql import build_lineage_sql_connection_factory

from .sql_repository import SqlFinOpsRepository


def main() -> None:
    repository = SqlFinOpsRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )
    repository.initialize_schema()


if __name__ == "__main__":
    main()

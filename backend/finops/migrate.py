from __future__ import annotations

try:
    from ..lineage_sql import build_lineage_sql_connection_factory
except ImportError:
    from lineage_sql import build_lineage_sql_connection_factory

from .sql_repository import ConnectionFactory, SqlFinOpsRepository


class FinOpsMigrationError(RuntimeError):
    code = "finops_schema_migration_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


def run_migration(connection_factory: ConnectionFactory) -> None:
    try:
        SqlFinOpsRepository(connection_factory=connection_factory).initialize_schema()
    except Exception:
        raise FinOpsMigrationError() from None


def main() -> None:
    run_migration(build_lineage_sql_connection_factory())


if __name__ == "__main__":
    main()

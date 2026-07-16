from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.lineage_sql import (  # noqa: E402
    LineageRepository,
    LineageUnavailable,
    build_lineage_sql_connection_factory,
)


EXPECTED_SCHEMA_VERSION = "2026-07-15.v1"
SCHEMA_VERSION_PROPERTY = "DataForgeLineageSchemaVersion"
EXPECTED_TABLES = frozenset(
    {
        "workspace_lineage",
        "experiment_version",
        "experiment_attachment",
        "workspace_generation_event",
    }
)
EXPECTED_CONSTRAINTS = frozenset(
    {
        "PK_workspace_lineage",
        "CK_workspace_lineage_generation",
        "CK_workspace_lineage_state",
        "CK_workspace_lineage_ordinal",
        "PK_experiment_version",
        "FK_experiment_version_workspace",
        "UQ_experiment_version_ordinal",
        "UQ_experiment_version_membership",
        "CK_experiment_version_generation",
        "CK_experiment_version_ordinal",
        "PK_experiment_attachment",
        "FK_experiment_attachment_version",
        "UQ_experiment_attachment_payload",
        "CK_experiment_attachment_generation",
        "PK_workspace_generation_event",
        "FK_workspace_generation_event_workspace",
        "CK_workspace_generation_event_generation",
        "CK_workspace_generation_event_kind",
    }
)
EXPECTED_INDEXES = frozenset({"IX_experiment_version_latest"})

SchemaObservation = namedtuple(
    "SchemaObservation",
    ("tables", "constraints", "indexes", "version_marker"),
)


class VerificationFailure(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or "verification_failed"


class ArgumentParseFailure(RuntimeError):
    """Raised without argparse's raw argv error rendering."""


class _BoundedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ArgumentParseFailure()


@contextmanager
def suppress_runtime_logging():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL + 1)
    try:
        yield
    finally:
        logging.disable(previous)


def build_parser() -> argparse.ArgumentParser:
    parser = _BoundedArgumentParser(
        description="Verify the DataForge lineage SQL release gate without secret inputs."
    )
    safe_mode = parser.add_mutually_exclusive_group()
    safe_mode.add_argument(
        "--check-prerequisites",
        action="store_true",
        help="Run local fail-closed and dependency checks without contacting Azure.",
    )
    safe_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for --check-prerequisites; never contacts Azure or writes SQL.",
    )
    parser.add_argument("--server", help="Azure SQL logical server FQDN.")
    parser.add_argument("--database", help="Azure SQL database name.")
    parser.add_argument(
        "--ephemeral-workspace",
        help="Explicit UUID authorizing the transactional create/rollback/purge probe.",
    )
    return parser


def parse_workspace_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise VerificationFailure(
            "ephemeral workspace must be an explicit UUID",
            code="ephemeral_workspace_uuid_required",
        ) from None
    return str(parsed)


def _verify_fail_closed() -> None:
    factory = build_lineage_sql_connection_factory(environ={})
    try:
        factory()
    except LineageUnavailable:
        outcome = getattr(factory, "outcome", None)
        if outcome is None or outcome.failure_category != "configuration":
            raise VerificationFailure(
                "missing configuration did not fail closed",
                code="fail_closed_check_failed",
            )
        return
    raise VerificationFailure(
        "missing configuration did not fail closed",
        code="fail_closed_check_failed",
    )


def _registered_odbc_driver() -> bool:
    try:
        import pyodbc

        return "ODBC Driver 18 for SQL Server" in pyodbc.drivers()
    except Exception:
        return False


def check_prerequisites() -> dict[str, Any]:
    _verify_fail_closed()
    schema_path = ROOT / "backend" / "sql" / "lineage_schema.sql"
    if not schema_path.is_file():
        raise VerificationFailure("schema source is missing", code="schema_source_missing")
    schema = schema_path.read_text(encoding="utf-8")
    required_names = EXPECTED_TABLES | EXPECTED_CONSTRAINTS | EXPECTED_INDEXES
    if any(name not in schema for name in required_names):
        raise VerificationFailure(
            "schema source does not match the verifier contract",
            code="schema_source_mismatch",
        )
    return {
        "azure_checked": False,
        "fail_closed": "verified",
        "odbc_driver_registered": _registered_odbc_driver(),
        "schema_source": "verified",
        "schema_version": EXPECTED_SCHEMA_VERSION,
    }


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    return row[index]


def observe_schema(connection_factory: Callable[[], Any]) -> SchemaObservation:
    connection = None
    try:
        connection = connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        tables = frozenset(
            str(_row_value(row, "name"))
            for row in cursor.execute(
                """SELECT t.name
                FROM sys.tables AS t
                WHERE t.schema_id = SCHEMA_ID(N'df_lineage')"""
            ).fetchall()
        )
        constraints = frozenset(
            str(_row_value(row, "name"))
            for row in cursor.execute(
                """SELECT o.name
                FROM sys.objects AS o
                WHERE o.parent_object_id IN (
                    SELECT t.object_id
                    FROM sys.tables AS t
                    WHERE t.schema_id = SCHEMA_ID(N'df_lineage')
                )
                  AND o.type IN (N'PK', N'UQ', N'F', N'C')"""
            ).fetchall()
        )
        indexes = frozenset(
            str(_row_value(row, "name"))
            for row in cursor.execute(
                """SELECT i.name
                FROM sys.indexes AS i
                INNER JOIN sys.tables AS t ON t.object_id = i.object_id
                WHERE t.schema_id = SCHEMA_ID(N'df_lineage')
                  AND i.name IS NOT NULL
                  AND i.is_primary_key = 0
                  AND i.is_unique_constraint = 0"""
            ).fetchall()
        )
        marker_row = cursor.execute(
            """SELECT CAST(ep.value AS NVARCHAR(128)) AS version_marker
            FROM sys.extended_properties AS ep
            WHERE ep.class = 3
              AND ep.major_id = SCHEMA_ID(N'df_lineage')
              AND ep.minor_id = 0
              AND ep.name = ?""",
            SCHEMA_VERSION_PROPERTY,
        ).fetchone()
        connection.rollback()
        marker = None if marker_row is None else str(_row_value(marker_row, "version_marker"))
        return SchemaObservation(tables, constraints, indexes, marker)
    except LineageUnavailable:
        raise
    except Exception:
        if connection is not None:
            _quiet_call(connection.rollback)
        raise VerificationFailure(
            "schema metadata query failed",
            code="schema_metadata_unavailable",
        ) from None
    finally:
        if connection is not None:
            _quiet_call(connection.close)


def assert_schema_contract(observed: SchemaObservation) -> None:
    if not EXPECTED_TABLES.issubset(observed.tables):
        raise VerificationFailure("expected schema tables are missing", code="schema_tables_missing")
    if not EXPECTED_CONSTRAINTS.issubset(observed.constraints):
        raise VerificationFailure(
            "expected schema constraints are missing",
            code="schema_constraints_missing",
        )
    if not EXPECTED_INDEXES.issubset(observed.indexes):
        raise VerificationFailure("expected schema indexes are missing", code="schema_indexes_missing")
    if observed.version_marker != EXPECTED_SCHEMA_VERSION:
        raise VerificationFailure(
            "expected schema version marker is missing",
            code="schema_version_mismatch",
        )


def _claim_ephemeral_workspace(connection_factory: Callable[[], Any], workspace_id: str) -> bool:
    connection = None
    try:
        connection = connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        existing = cursor.execute(
            """SELECT workspace_id
            FROM df_lineage.workspace_lineage WITH (UPDLOCK, HOLDLOCK)
            WHERE workspace_id = ?""",
            workspace_id,
        ).fetchone()
        if existing is not None:
            connection.rollback()
            return False
        cursor.execute(
            """INSERT INTO df_lineage.workspace_lineage (
                workspace_id, generation, lifecycle_state, next_version_ordinal, actor_metadata
            ) VALUES (?, 1, N'active', 1, ?)""",
            workspace_id,
            json.dumps(
                {
                    "actor_id": workspace_id,
                    "actor_type": "system",
                    "verification_id": workspace_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        connection.commit()
        return True
    except LineageUnavailable:
        raise
    except Exception:
        if connection is not None:
            _quiet_call(connection.rollback)
        raise VerificationFailure(
            "ephemeral workspace claim failed",
            code="ephemeral_workspace_claim_failed",
        ) from None
    finally:
        if connection is not None:
            _quiet_call(connection.close)


def _cleanup_ephemeral_workspace(connection_factory: Callable[[], Any], workspace_id: str) -> None:
    connection = None
    try:
        connection = connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM df_lineage.experiment_attachment WHERE workspace_id = ?", workspace_id
        )
        cursor.execute("DELETE FROM df_lineage.experiment_version WHERE workspace_id = ?", workspace_id)
        cursor.execute(
            "DELETE FROM df_lineage.workspace_generation_event WHERE workspace_id = ?",
            workspace_id,
        )
        deleted = cursor.execute(
            "DELETE FROM df_lineage.workspace_lineage WHERE workspace_id = ?", workspace_id
        )
        if deleted.rowcount != 1:
            raise VerificationFailure(
                "ephemeral workspace cleanup was incomplete",
                code="ephemeral_workspace_cleanup_failed",
            )
        connection.commit()
    except VerificationFailure:
        if connection is not None:
            _quiet_call(connection.rollback)
        raise
    except Exception:
        if connection is not None:
            _quiet_call(connection.rollback)
        raise VerificationFailure(
            "ephemeral workspace cleanup failed",
            code="ephemeral_workspace_cleanup_failed",
        ) from None
    finally:
        if connection is not None:
            _quiet_call(connection.close)


class _RollbackProbeCursor:
    def __init__(self, cursor: Any, marker: dict[str, bool]) -> None:
        self._cursor = cursor
        self._marker = marker
        self._failed = False

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def execute(self, operation: str, *parameters: Any) -> "_RollbackProbeCursor":
        if not self._failed and "/* lineage:advance-ordinal */" in operation:
            self._failed = True
            self._marker["triggered"] = True
            raise RuntimeError("intentional verifier rollback")
        self._cursor.execute(operation, *parameters)
        return self

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> Sequence[Any]:
        return self._cursor.fetchall()


class _RollbackProbeConnection:
    def __init__(self, connection: Any, marker: dict[str, bool]) -> None:
        self._connection = connection
        self._marker = marker

    @property
    def autocommit(self) -> bool:
        return self._connection.autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._connection.autocommit = value

    def cursor(self) -> _RollbackProbeCursor:
        return _RollbackProbeCursor(self._connection.cursor(), self._marker)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def assert_transaction_results(
    *,
    first_ordinal: int,
    duplicate_ordinal: int,
    duplicate_created: bool,
    duplicate_version_matches: bool,
    post_rollback_ordinals: Sequence[int],
    recreated_ordinal: int,
) -> None:
    if first_ordinal != 1:
        raise VerificationFailure("first canonical ordinal was not one")
    if duplicate_ordinal != 1 or duplicate_created or not duplicate_version_matches:
        raise VerificationFailure("duplicate analysis allocated a canonical ordinal")
    if tuple(post_rollback_ordinals) != (1, 2):
        raise VerificationFailure(
            "transaction rollback did not preserve the next canonical ordinal",
            code="transaction_rollback_failed",
        )
    if recreated_ordinal != 1:
        raise VerificationFailure("recreated generation did not restart canonical ordinals")


def verify_transaction_behavior(
    connection_factory: Callable[[], Any], workspace_id: str
) -> dict[str, Any]:
    if not _claim_ephemeral_workspace(connection_factory, workspace_id):
        raise VerificationFailure(
            "ephemeral workspace UUID already exists",
            code="ephemeral_workspace_exists",
        )

    repository = LineageRepository(connection_factory=connection_factory)
    actor = {"actor_id": workspace_id, "actor_type": "system"}
    first_run = f"verify-{workspace_id}-first"
    rollback_run = f"verify-{workspace_id}-rollback"
    second_run = f"verify-{workspace_id}-second"
    recreated_run = f"verify-{workspace_id}-recreated"
    stage = "first_commit"

    try:
        first = repository.commit_analysis(
            workspace_id=workspace_id,
            generation=1,
            canonical_run_id=first_run,
            decision_fingerprint="1" * 64,
            evidence_fingerprint="2" * 64,
            actor_metadata=actor,
        )
        stage = "duplicate_commit"
        duplicate = repository.commit_analysis(
            workspace_id=workspace_id,
            generation=1,
            canonical_run_id=f"verify-{workspace_id}-duplicate",
            decision_fingerprint="1" * 64,
            evidence_fingerprint="2" * 64,
            actor_metadata=actor,
        )
        stage = "rollback_probe"
        rollback_marker = {"triggered": False}
        rollback_repository = LineageRepository(
            connection_factory=lambda: _RollbackProbeConnection(
                connection_factory(), rollback_marker
            )
        )
        try:
            rollback_repository.commit_analysis(
                workspace_id=workspace_id,
                generation=1,
                canonical_run_id=rollback_run,
                decision_fingerprint="3" * 64,
                evidence_fingerprint="4" * 64,
                actor_metadata=actor,
            )
        except LineageUnavailable:
            if not rollback_marker["triggered"]:
                raise VerificationFailure(
                    "rollback probe did not reach the ordinal advancement",
                    code="transaction_rollback_failed",
                )
        else:
            raise VerificationFailure(
                "intentional transaction failure committed",
                code="transaction_rollback_failed",
            )

        stage = "second_commit"
        second = repository.commit_analysis(
            workspace_id=workspace_id,
            generation=1,
            canonical_run_id=second_run,
            decision_fingerprint="5" * 64,
            evidence_fingerprint="6" * 64,
            actor_metadata=actor,
        )
        stage = "list_versions"
        after_rollback = repository.list_versions(workspace_id=workspace_id, generation=1)
        if rollback_run in {item.canonical_run_id for item in after_rollback}:
            raise VerificationFailure(
                "rolled back version remained visible",
                code="transaction_rollback_failed",
            )
        stage = "purge"
        if not repository.purge_workspace(
            workspace_id=workspace_id, generation=1, actor_metadata=actor
        ):
            raise VerificationFailure("ephemeral purge did not execute")
        stage = "recreate"
        if repository.recreate_workspace(
            workspace_id=workspace_id, generation=1, actor_metadata=actor
        ) != 2:
            raise VerificationFailure("ephemeral recreation did not advance generation")
        stage = "recreated_commit"
        recreated = repository.commit_analysis(
            workspace_id=workspace_id,
            generation=2,
            canonical_run_id=recreated_run,
            decision_fingerprint="7" * 64,
            evidence_fingerprint="8" * 64,
            actor_metadata=actor,
        )
        stage = "assertions"
        assert_transaction_results(
            first_ordinal=first.ordinal,
            duplicate_ordinal=duplicate.ordinal,
            duplicate_created=duplicate.created,
            duplicate_version_matches=duplicate.version_id == first.version_id,
            post_rollback_ordinals=tuple(item.ordinal for item in after_rollback),
            recreated_ordinal=recreated.ordinal,
        )
        return {
            "duplicate_deduplication": "verified",
            "generation_reset": "verified",
            "rollback": "verified",
        }
    except LineageUnavailable:
        raise VerificationFailure(
            "transaction probe could not complete",
            code=f"transaction_{stage}_unavailable",
        ) from None
    except VerificationFailure as error:
        if error.code == "verification_failed":
            raise VerificationFailure(
                "transaction probe did not satisfy its contract",
                code=f"transaction_{stage}_failed",
            ) from None
        raise
    finally:
        _cleanup_ephemeral_workspace(connection_factory, workspace_id)


def _quiet_call(operation: Callable[[], Any]) -> None:
    try:
        operation()
    except Exception:
        pass


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    factory = None
    try:
        if args.check_prerequisites or args.dry_run:
            result = check_prerequisites()
            _emit({"mode": "check-prerequisites", "status": "ok", **result})
            return 0
        if not args.server or not args.database:
            raise VerificationFailure(
                "server and database identifiers are required in verify mode",
                code="runtime_identifiers_required",
            )
        if not args.ephemeral_workspace:
            raise VerificationFailure(
                "an ephemeral workspace UUID is required for the full release gate",
                code="ephemeral_workspace_uuid_required",
            )
        workspace_id = parse_workspace_uuid(args.ephemeral_workspace)
        _verify_fail_closed()
        factory = build_lineage_sql_connection_factory(
            environ={"LINEAGE_SQL_SERVER": args.server, "LINEAGE_SQL_DATABASE": args.database}
        )
        observed = observe_schema(factory)
        assert_schema_contract(observed)
        transaction = verify_transaction_behavior(factory, workspace_id)
        _emit(
            {
                "fail_closed": "verified",
                "managed_identity_connection": "verified",
                "mode": "verify",
                "schema": "verified",
                "schema_version": EXPECTED_SCHEMA_VERSION,
                "status": "ok",
                "transaction": transaction,
            }
        )
        return 0
    except VerificationFailure as error:
        _emit({"mode": "verify", "reason": error.code, "status": "failed"})
        return 1
    except LineageUnavailable:
        outcome = getattr(factory, "outcome", None) if factory is not None else None
        category = getattr(outcome, "failure_category", None)
        payload = {
            "mode": "verify",
            "reason": "lineage_unavailable",
            "status": "failed",
        }
        if category in {"configuration", "token", "driver", "connection"}:
            payload["failure_category"] = category
        _emit(payload)
        return 1
    except Exception:
        _emit({"mode": "verify", "reason": "verification_error", "status": "failed"})
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except ArgumentParseFailure:
        _emit({"mode": "verify", "reason": "invalid_arguments", "status": "failed"})
        return 2
    with suppress_runtime_logging():
        return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())

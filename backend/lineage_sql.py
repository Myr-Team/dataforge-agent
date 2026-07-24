from __future__ import annotations

import json
import os
import re
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from azure.identity import ManagedIdentityCredential


_SCHEMA_PATH = Path(__file__).with_name("sql") / "lineage_schema.sql"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ACTOR_TYPES = {"member", "service", "system"}
_AZURE_SQL_SCOPE = "https://database.windows.net/.default"
_LINEAGE_UNAVAILABLE_MESSAGE = "lineage database is unavailable"
_SQL_CONNECT_TIMEOUT_SECONDS = 5
_WORKLOAD_IDENTITY_ENVIRONMENT = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
)
_SQL_SERVER_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.database\.windows\.net"
)
_SQL_DATABASE_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_. -]{0,126}[A-Za-z0-9_.-])?")

# ODBC Driver 18 defines this driver-specific connection attribute as 1256.
SQL_COPT_SS_ACCESS_TOKEN = 1256


class LineageUnavailable(RuntimeError):
    """Raised when authoritative lineage cannot safely accept an operation."""


@dataclass(frozen=True, slots=True)
class VersionCommit:
    version_id: str
    workspace_id: str
    generation: int
    ordinal: int
    canonical_run_id: str
    decision_fingerprint: str
    evidence_fingerprint: str
    created: bool


@dataclass(frozen=True, slots=True)
class AttachmentCommit:
    attachment_id: str
    version_id: str
    workspace_id: str
    generation: int
    kind: str
    source_run_id: str
    payload_sha256: str
    created: bool


class _Cursor(Protocol):
    rowcount: int

    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...

    def fetchone(self) -> Any | None: ...

    def fetchall(self) -> Sequence[Any]: ...


class _Connection(Protocol):
    autocommit: bool

    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], _Connection]
LineageConnectionFailureCategory = Literal["configuration", "token", "driver", "connection"]


@dataclass(frozen=True, slots=True)
class LineageConnectionOutcome:
    """Safe, in-process state for the most recent connection attempt."""

    available: bool | None
    failure_category: LineageConnectionFailureCategory | None


class _OdbcDriverUnavailable(RuntimeError):
    pass


class _LineageSqlConnectionFactory:
    def __init__(
        self,
        *,
        environ: Mapping[str, str],
        credential: Any | None,
        connect: Callable[..., _Connection] | None,
    ) -> None:
        self._environment = environ
        self._credential = credential
        self._connect = connect
        self._outcome = LineageConnectionOutcome(available=None, failure_category=None)

    @property
    def outcome(self) -> LineageConnectionOutcome:
        return self._outcome

    def __call__(self) -> _Connection:
        category: LineageConnectionFailureCategory | None = None
        connection: _Connection | None = None

        try:
            server, database = _lineage_sql_settings(self._environment)
        except Exception:
            category = "configuration"

        token: str | None = None
        if category is None:
            if self._credential is None and _workload_identity_environment_configured():
                category = "configuration"

        if category is None:
            try:
                if self._credential is None:
                    managed_identity_client_id = str(
                        self._environment.get(
                            "LINEAGE_SQL_MANAGED_IDENTITY_CLIENT_ID",
                            "",
                        )
                    ).strip()
                    if managed_identity_client_id:
                        self._credential = ManagedIdentityCredential(
                            client_id=managed_identity_client_id,
                            _exclude_workload_identity_credential=True,
                        )
                    else:
                        self._credential = ManagedIdentityCredential(
                            _exclude_workload_identity_credential=True
                        )
                token = self._credential.get_token(_AZURE_SQL_SCOPE).token
                packed_token = _pack_access_token(token)
            except Exception:
                category = "token"

        if category is None:
            try:
                connector = self._connect or _pyodbc_connect
                connection = connector(
                    _lineage_sql_connection_string(server=server, database=database),
                    attrs_before={SQL_COPT_SS_ACCESS_TOKEN: packed_token},
                    timeout=_SQL_CONNECT_TIMEOUT_SECONDS,
                )
            except (ModuleNotFoundError, _OdbcDriverUnavailable):
                category = "driver"
            except Exception:
                category = "connection"

        if category is not None:
            self._outcome = LineageConnectionOutcome(
                available=False,
                failure_category=category,
            )
            raise LineageUnavailable(_LINEAGE_UNAVAILABLE_MESSAGE)

        self._outcome = LineageConnectionOutcome(available=True, failure_category=None)
        assert connection is not None
        return connection


def build_lineage_sql_connection_factory(
    *,
    environ: Mapping[str, str] | None = None,
    credential: Any | None = None,
    connect: Callable[..., _Connection] | None = None,
) -> ConnectionFactory:
    """Build a lazy, managed-identity-only Azure SQL connection factory."""
    environment = os.environ if environ is None else environ
    return _LineageSqlConnectionFactory(
        environ=environment,
        credential=credential,
        connect=connect,
    )


def _lineage_sql_settings(environment: Mapping[str, str]) -> tuple[str, str]:
    server = environment.get("LINEAGE_SQL_SERVER", "")
    database = environment.get("LINEAGE_SQL_DATABASE", "")
    if not isinstance(server, str) or not _SQL_SERVER_PATTERN.fullmatch(server):
        raise ValueError("invalid lineage SQL configuration")
    if not isinstance(database, str) or not _SQL_DATABASE_PATTERN.fullmatch(database):
        raise ValueError("invalid lineage SQL configuration")
    return server, database


def _workload_identity_environment_configured() -> bool:
    return all(os.environ.get(name) for name in _WORKLOAD_IDENTITY_ENVIRONMENT)


def _lineage_sql_connection_string(*, server: str, database: str) -> str:
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )


def _pack_access_token(token: str) -> bytes:
    if not isinstance(token, str) or not token:
        raise ValueError("invalid Azure SQL access token")
    encoded = token.encode("utf-16-le")
    return struct.pack("<I", len(encoded)) + encoded


def _pyodbc_connect(connection_string: str, **kwargs: Any) -> _Connection:
    import pyodbc

    if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
        raise _OdbcDriverUnavailable()
    return pyodbc.connect(connection_string, **kwargs)


class LineageRepository:
    """Transactional Azure SQL repository with an explicit pyodbc-style factory."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        schema_path: Path | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._schema_path = schema_path or _SCHEMA_PATH

    def initialize_schema(self) -> None:
        schema = self._schema_path.read_text(encoding="utf-8")
        with self._transaction() as cursor:
            cursor.execute(f"/* lineage:schema */\n{schema}")

    def commit_analysis(
        self,
        *,
        workspace_id: str,
        generation: int,
        canonical_run_id: str,
        decision_fingerprint: str,
        evidence_fingerprint: str,
        actor_metadata: Mapping[str, str | int] | None = None,
    ) -> VersionCommit:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        canonical_run_id = _bounded_text("canonical_run_id", canonical_run_id, 128)
        decision_fingerprint = _sha256("decision_fingerprint", decision_fingerprint)
        evidence_fingerprint = _sha256("evidence_fingerprint", evidence_fingerprint)
        actor_json = _actor_metadata_json(actor_metadata)

        with self._transaction() as cursor:
            workspace = self._lock_workspace(
                cursor,
                workspace_id=workspace_id,
                generation=generation,
                actor_json=actor_json,
                create=True,
            )
            self._require_active_generation(workspace, generation)

            latest = cursor.execute(
                """/* lineage:latest-version */
                SELECT TOP (1)
                    version_id,
                    workspace_id,
                    generation,
                    ordinal,
                    canonical_run_id,
                    decision_fingerprint,
                    evidence_fingerprint
                FROM df_lineage.experiment_version WITH (UPDLOCK, HOLDLOCK)
                WHERE workspace_id = ? AND generation = ?
                ORDER BY ordinal DESC""",
                workspace_id,
                generation,
            ).fetchone()

            if latest and (
                str(latest.decision_fingerprint) == decision_fingerprint
                and str(latest.evidence_fingerprint) == evidence_fingerprint
            ):
                return _version_commit(latest, created=False)

            ordinal = int(workspace.next_version_ordinal)
            version_id = str(uuid4())
            cursor.execute(
                """/* lineage:insert-version */
                INSERT INTO df_lineage.experiment_version (
                    version_id,
                    workspace_id,
                    generation,
                    ordinal,
                    canonical_run_id,
                    decision_fingerprint,
                    evidence_fingerprint,
                    actor_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                version_id,
                workspace_id,
                generation,
                ordinal,
                canonical_run_id,
                decision_fingerprint,
                evidence_fingerprint,
                actor_json,
            )
            updated = cursor.execute(
                """/* lineage:advance-ordinal */
                UPDATE df_lineage.workspace_lineage
                SET next_version_ordinal = ?, updated_at = SYSUTCDATETIME()
                WHERE workspace_id = ?
                  AND generation = ?
                  AND lifecycle_state = N'active'""",
                ordinal + 1,
                workspace_id,
                generation,
            )
            if updated.rowcount != 1:
                raise LineageUnavailable("workspace generation is not active")

            return VersionCommit(
                version_id=version_id,
                workspace_id=workspace_id,
                generation=generation,
                ordinal=ordinal,
                canonical_run_id=canonical_run_id,
                decision_fingerprint=decision_fingerprint,
                evidence_fingerprint=evidence_fingerprint,
                created=True,
            )

    def attach_snapshot(
        self,
        *,
        workspace_id: str,
        generation: int,
        version_id: str,
        kind: str,
        source_run_id: str,
        payload_sha256: str,
        actor_metadata: Mapping[str, str | int] | None = None,
    ) -> AttachmentCommit:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        version_id = _uuid_text("version_id", version_id)
        kind = _bounded_text("kind", kind, 32)
        source_run_id = _bounded_text("source_run_id", source_run_id, 128)
        payload_sha256 = _sha256("payload_sha256", payload_sha256)
        actor_json = _actor_metadata_json(actor_metadata)

        with self._transaction() as cursor:
            workspace = self._lock_workspace(
                cursor,
                workspace_id=workspace_id,
                generation=generation,
                actor_json=actor_json,
                create=False,
            )
            self._require_active_generation(workspace, generation)

            version = cursor.execute(
                """/* lineage:lock-version */
                SELECT version_id
                FROM df_lineage.experiment_version WITH (UPDLOCK, HOLDLOCK)
                WHERE version_id = ? AND workspace_id = ? AND generation = ?""",
                version_id,
                workspace_id,
                generation,
            ).fetchone()
            if version is None:
                raise LineageUnavailable("version is not available for attachment")

            existing = cursor.execute(
                """/* lineage:existing-attachment */
                SELECT attachment_id
                FROM df_lineage.experiment_attachment WITH (UPDLOCK, HOLDLOCK)
                WHERE version_id = ?
                  AND kind = ?
                  AND source_run_id = ?
                  AND payload_sha256 = ?""",
                version_id,
                kind,
                source_run_id,
                payload_sha256,
            ).fetchone()
            if existing is not None:
                return AttachmentCommit(
                    attachment_id=str(existing.attachment_id),
                    version_id=version_id,
                    workspace_id=workspace_id,
                    generation=generation,
                    kind=kind,
                    source_run_id=source_run_id,
                    payload_sha256=payload_sha256,
                    created=False,
                )

            attachment_id = str(uuid4())
            cursor.execute(
                """/* lineage:insert-attachment */
                INSERT INTO df_lineage.experiment_attachment (
                    attachment_id,
                    version_id,
                    workspace_id,
                    generation,
                    kind,
                    source_run_id,
                    payload_sha256,
                    actor_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                attachment_id,
                version_id,
                workspace_id,
                generation,
                kind,
                source_run_id,
                payload_sha256,
                actor_json,
            )
            return AttachmentCommit(
                attachment_id=attachment_id,
                version_id=version_id,
                workspace_id=workspace_id,
                generation=generation,
                kind=kind,
                source_run_id=source_run_id,
                payload_sha256=payload_sha256,
                created=True,
            )

    def purge_workspace(
        self,
        *,
        workspace_id: str,
        generation: int,
        actor_metadata: Mapping[str, str | int] | None = None,
    ) -> bool:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        actor_json = _actor_metadata_json(actor_metadata)

        with self._transaction() as cursor:
            workspace = self._lock_workspace(
                cursor,
                workspace_id=workspace_id,
                generation=generation,
                actor_json=actor_json,
                create=False,
            )
            if int(workspace.generation) != generation:
                raise LineageUnavailable("workspace generation is not active")
            if str(workspace.lifecycle_state) == "purged":
                return False
            if str(workspace.lifecycle_state) != "active":
                raise LineageUnavailable("workspace generation is not active")

            marked = cursor.execute(
                """/* lineage:mark-purging */
                UPDATE df_lineage.workspace_lineage
                SET lifecycle_state = N'purging', updated_at = SYSUTCDATETIME()
                WHERE workspace_id = ?
                  AND generation = ?
                  AND lifecycle_state = N'active'""",
                workspace_id,
                generation,
            )
            if marked.rowcount != 1:
                raise LineageUnavailable("workspace generation is not active")

            cursor.execute(
                """/* lineage:delete-attachments */
                DELETE FROM df_lineage.experiment_attachment
                WHERE workspace_id = ? AND generation = ?""",
                workspace_id,
                generation,
            )
            cursor.execute(
                """/* lineage:delete-versions */
                DELETE FROM df_lineage.experiment_version
                WHERE workspace_id = ? AND generation = ?""",
                workspace_id,
                generation,
            )
            purged = cursor.execute(
                """/* lineage:mark-purged */
                UPDATE df_lineage.workspace_lineage
                SET lifecycle_state = N'purged',
                    next_version_ordinal = 1,
                    updated_at = SYSUTCDATETIME()
                WHERE workspace_id = ?
                  AND generation = ?
                  AND lifecycle_state = N'purging'""",
                workspace_id,
                generation,
            )
            if purged.rowcount != 1:
                raise LineageUnavailable("workspace purge did not complete")
            self._insert_generation_event(
                cursor,
                workspace_id=workspace_id,
                generation=generation,
                event_kind="purged",
                actor_json=actor_json,
            )
            return True

    def recreate_workspace(
        self,
        *,
        workspace_id: str,
        generation: int,
        actor_metadata: Mapping[str, str | int] | None = None,
    ) -> int:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        actor_json = _actor_metadata_json(actor_metadata)

        with self._transaction() as cursor:
            workspace = self._lock_workspace(
                cursor,
                workspace_id=workspace_id,
                generation=generation,
                actor_json=actor_json,
                create=False,
            )
            if (
                int(workspace.generation) != generation
                or str(workspace.lifecycle_state) != "purged"
            ):
                raise LineageUnavailable("workspace generation is not purged")

            next_generation = generation + 1
            updated = cursor.execute(
                """/* lineage:recreate-workspace */
                UPDATE df_lineage.workspace_lineage
                SET generation = ?,
                    lifecycle_state = N'active',
                    next_version_ordinal = 1,
                    updated_at = SYSUTCDATETIME()
                WHERE workspace_id = ?
                  AND generation = ?
                  AND lifecycle_state = N'purged'""",
                next_generation,
                workspace_id,
                generation,
            )
            if updated.rowcount != 1:
                raise LineageUnavailable("workspace generation is not purged")
            self._insert_generation_event(
                cursor,
                workspace_id=workspace_id,
                generation=next_generation,
                event_kind="recreated",
                actor_json=actor_json,
            )
            return next_generation

    def current_generation(self, *, workspace_id: str) -> int:
        """Read the active generation from SQL; an unseen workspace starts at generation one."""
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        with self._transaction() as cursor:
            workspace = cursor.execute(
                """/* lineage:current-workspace */
                SELECT workspace_id, generation, lifecycle_state, next_version_ordinal
                FROM df_lineage.workspace_lineage
                WHERE workspace_id = ?""",
                workspace_id,
            ).fetchone()
            if workspace is None:
                return 1
            return _generation(int(workspace.generation))

    def list_versions(self, *, workspace_id: str, generation: int) -> tuple[VersionCommit, ...]:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* lineage:list-versions */
                SELECT
                    version_id,
                    workspace_id,
                    generation,
                    ordinal,
                    canonical_run_id,
                    decision_fingerprint,
                    evidence_fingerprint
                FROM df_lineage.experiment_version
                WHERE workspace_id = ? AND generation = ?
                ORDER BY ordinal ASC""",
                workspace_id,
                generation,
            ).fetchall()
            return tuple(_version_commit(row, created=False) for row in rows)

    def list_attachments(
        self,
        *,
        workspace_id: str,
        generation: int,
    ) -> tuple[AttachmentCommit, ...]:
        workspace_id = _bounded_text("workspace_id", workspace_id, 128)
        generation = _generation(generation)
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* lineage:list-attachments */
                SELECT
                    attachment_id,
                    version_id,
                    workspace_id,
                    generation,
                    kind,
                    source_run_id,
                    payload_sha256
                FROM df_lineage.experiment_attachment
                WHERE workspace_id = ? AND generation = ?
                ORDER BY attachment_id ASC""",
                workspace_id,
                generation,
            ).fetchall()
            return tuple(_attachment_commit(row, created=False) for row in rows)

    def _lock_workspace(
        self,
        cursor: _Cursor,
        *,
        workspace_id: str,
        generation: int,
        actor_json: str | None,
        create: bool,
    ) -> Any:
        workspace = cursor.execute(
            """/* lineage:lock-workspace */
            SELECT workspace_id, generation, lifecycle_state, next_version_ordinal
            FROM df_lineage.workspace_lineage WITH (UPDLOCK, HOLDLOCK)
            WHERE workspace_id = ?""",
            workspace_id,
        ).fetchone()
        if workspace is not None:
            return workspace
        if not create or generation != 1:
            raise LineageUnavailable("workspace generation is not active")

        cursor.execute(
            """/* lineage:insert-workspace */
            INSERT INTO df_lineage.workspace_lineage (
                workspace_id, generation, lifecycle_state, next_version_ordinal, actor_metadata
            ) VALUES (?, ?, N'active', 1, ?)""",
            workspace_id,
            generation,
            actor_json,
        )
        return _WorkspaceRow(
            workspace_id=workspace_id,
            generation=generation,
            lifecycle_state="active",
            next_version_ordinal=1,
        )

    @staticmethod
    def _require_active_generation(workspace: Any, generation: int) -> None:
        if (
            int(workspace.generation) != generation
            or str(workspace.lifecycle_state) != "active"
        ):
            raise LineageUnavailable("workspace generation is not active")

    @staticmethod
    def _insert_generation_event(
        cursor: _Cursor,
        *,
        workspace_id: str,
        generation: int,
        event_kind: str,
        actor_json: str | None,
    ) -> None:
        cursor.execute(
            """/* lineage:insert-generation-event */
            INSERT INTO df_lineage.workspace_generation_event (
                workspace_id, generation, event_kind, actor_metadata
            ) VALUES (?, ?, ?, ?)""",
            workspace_id,
            generation,
            event_kind,
            actor_json,
        )

    @contextmanager
    def _transaction(self) -> Iterator[_Cursor]:
        connection: _Connection | None = None
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except LineageUnavailable:
            if connection is not None:
                _quiet_call(connection.rollback)
            raise
        except Exception:
            if connection is not None:
                _quiet_call(connection.rollback)
            raise LineageUnavailable("lineage database operation failed") from None
        finally:
            if connection is not None:
                _quiet_call(connection.close)


@dataclass(frozen=True, slots=True)
class _WorkspaceRow:
    workspace_id: str
    generation: int
    lifecycle_state: str
    next_version_ordinal: int


def _version_commit(row: Any, *, created: bool) -> VersionCommit:
    return VersionCommit(
        version_id=_uuid_text("version_id", row.version_id),
        workspace_id=str(row.workspace_id),
        generation=int(row.generation),
        ordinal=int(row.ordinal),
        canonical_run_id=str(row.canonical_run_id),
        decision_fingerprint=str(row.decision_fingerprint),
        evidence_fingerprint=str(row.evidence_fingerprint),
        created=created,
    )


def _attachment_commit(row: Any, *, created: bool) -> AttachmentCommit:
    return AttachmentCommit(
        attachment_id=str(row.attachment_id),
        version_id=str(row.version_id),
        workspace_id=str(row.workspace_id),
        generation=int(row.generation),
        kind=str(row.kind),
        source_run_id=str(row.source_run_id),
        payload_sha256=str(row.payload_sha256),
        created=created,
    )


def _generation(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("generation must be a positive integer")
    return value


def _bounded_text(name: str, value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum} characters")
    return value


def _sha256(name: str, value: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a normalized lowercase SHA-256")
    return value


def _uuid_text(name: str, value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{name} must be a UUID") from None


def _actor_metadata_json(
    metadata: Mapping[str, str | int] | None,
) -> str | None:
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise ValueError("actor_metadata must be a flat mapping")

    safe: dict[str, str | int] = {}
    for key, value in metadata.items():
        if key == "actor_id" or key == "request_id":
            safe[key] = _uuid_text(f"actor_metadata.{key}", value)
        elif key == "actor_type":
            if not isinstance(value, str) or value not in _ACTOR_TYPES:
                raise ValueError("actor_metadata.actor_type is not permitted")
            safe[key] = value
        elif key == "actor_sequence":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
                raise ValueError("actor_metadata.actor_sequence is not permitted")
            safe[key] = value
        else:
            raise ValueError("actor_metadata contains an unknown field")

    encoded = json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 2048:
        raise ValueError("actor_metadata is too large")
    return encoded


def _quiet_call(operation: Callable[[], Any]) -> None:
    try:
        operation()
    except Exception:
        pass


__all__ = [
    "AttachmentCommit",
    "LineageConnectionOutcome",
    "LineageRepository",
    "LineageUnavailable",
    "SQL_COPT_SS_ACCESS_TOKEN",
    "VersionCommit",
    "build_lineage_sql_connection_factory",
]

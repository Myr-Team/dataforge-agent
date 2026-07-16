from __future__ import annotations

import copy
import importlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import backend.run_store as run_store


_ACTOR_METADATA = {
    "actor_id": "00000000-0000-0000-0000-000000000001",
    "actor_type": "member",
}
_LOCK_MARKERS = (
    "/* lineage:lock-workspace */",
    "/* lineage:latest-version */",
    "/* lineage:lock-version */",
    "/* lineage:existing-attachment */",
)
_REAL_SQL_FACTORY_ENV = "LINEAGE_SQL_TEST_CONNECTION_FACTORY"
_FACTORY_REFERENCE_PATTERN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")
_ATTACHMENT_FK_NAME = "FK_experiment_attachment_version"


class _MemorySqlDatabase:
    """DB-API test double injected explicitly into LineageRepository."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.attachments: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.schema_executions = 0

    def connect(self) -> "_MemoryConnection":
        return _MemoryConnection(self)


class _MemoryConnection:
    def __init__(self, database: _MemorySqlDatabase) -> None:
        self.database = database
        self.autocommit = False
        self._locked = False
        self._closed = False
        self._state: dict[str, Any] | None = None

    def cursor(self) -> "_MemoryCursor":
        return _MemoryCursor(self)

    def _begin(self) -> dict[str, Any]:
        if not self._locked:
            self.database.lock.acquire()
            self._locked = True
            self._state = {
                "workspaces": copy.deepcopy(self.database.workspaces),
                "versions": copy.deepcopy(self.database.versions),
                "attachments": copy.deepcopy(self.database.attachments),
                "events": copy.deepcopy(self.database.events),
            }
        assert self._state is not None
        return self._state

    def commit(self) -> None:
        if self._locked:
            assert self._state is not None
            self.database.workspaces = self._state["workspaces"]
            self.database.versions = self._state["versions"]
            self.database.attachments = self._state["attachments"]
            self.database.events = self._state["events"]
            self.database.lock.release()
            self._locked = False

    def rollback(self) -> None:
        if self._locked:
            self.database.lock.release()
            self._locked = False

    def close(self) -> None:
        self.rollback()
        self._closed = True


class _MemoryCursor:
    def __init__(self, connection: _MemoryConnection) -> None:
        self.connection = connection
        self._rows: list[SimpleNamespace] = []
        self.rowcount = 0

    def execute(self, sql: str, *parameters: Any) -> "_MemoryCursor":
        params = tuple(parameters)
        database = self.connection.database
        with database.lock:
            database.executions.append((sql, params))

        if "/* lineage:schema */" in sql:
            _assert_schema_contract(sql)
            database.schema_executions += 1
            return self

        if any(marker in sql for marker in _LOCK_MARKERS):
            assert "WITH (UPDLOCK, HOLDLOCK)" in sql

        state = self.connection._begin()
        workspaces = state["workspaces"]
        versions = state["versions"]
        attachments = state["attachments"]

        if "/* lineage:lock-workspace */" in sql:
            workspace = workspaces.get(str(params[0]))
            self._rows = [_row(workspace)] if workspace else []
        elif "/* lineage:current-workspace */" in sql:
            workspace = workspaces.get(str(params[0]))
            self._rows = [_row(workspace)] if workspace else []
        elif "/* lineage:insert-workspace */" in sql:
            workspace_id, generation, actor_metadata = params
            workspaces[str(workspace_id)] = {
                "workspace_id": str(workspace_id),
                "generation": int(generation),
                "lifecycle_state": "active",
                "next_version_ordinal": 1,
                "actor_metadata": actor_metadata,
            }
            self.rowcount = 1
        elif "/* lineage:latest-version */" in sql:
            workspace_id, generation = params
            matches = [
                value
                for value in versions.values()
                if value["workspace_id"] == workspace_id and value["generation"] == generation
            ]
            matches.sort(key=lambda value: value["ordinal"], reverse=True)
            self._rows = [_row(matches[0])] if matches else []
        elif "/* lineage:insert-version */" in sql:
            (
                version_id,
                workspace_id,
                generation,
                ordinal,
                canonical_run_id,
                decision_fingerprint,
                evidence_fingerprint,
                actor_metadata,
            ) = params
            versions[str(version_id)] = {
                "version_id": str(version_id),
                "workspace_id": str(workspace_id),
                "generation": int(generation),
                "ordinal": int(ordinal),
                "canonical_run_id": str(canonical_run_id),
                "decision_fingerprint": str(decision_fingerprint),
                "evidence_fingerprint": str(evidence_fingerprint),
                "actor_metadata": actor_metadata,
            }
            self.rowcount = 1
        elif "/* lineage:advance-ordinal */" in sql:
            next_ordinal, workspace_id, generation = params
            workspace = workspaces[str(workspace_id)]
            if workspace["generation"] == generation and workspace["lifecycle_state"] == "active":
                workspace["next_version_ordinal"] = int(next_ordinal)
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif "/* lineage:lock-version */" in sql:
            version_id, workspace_id, generation = params
            version = versions.get(str(version_id))
            if (
                version
                and version["workspace_id"] == workspace_id
                and version["generation"] == generation
            ):
                self._rows = [_row(version)]
            else:
                self._rows = []
        elif "/* lineage:existing-attachment */" in sql:
            version_id, kind, source_run_id, payload_sha256 = params
            matches = [
                value
                for value in attachments.values()
                if value["version_id"] == version_id
                and value["kind"] == kind
                and value["source_run_id"] == source_run_id
                and value["payload_sha256"] == payload_sha256
            ]
            self._rows = [_row(matches[0])] if matches else []
        elif "/* lineage:insert-attachment */" in sql:
            (
                attachment_id,
                version_id,
                workspace_id,
                generation,
                kind,
                source_run_id,
                payload_sha256,
                actor_metadata,
            ) = params
            version = versions.get(str(version_id))
            assert version is not None
            assert version["workspace_id"] == workspace_id
            assert version["generation"] == generation
            attachments[str(attachment_id)] = {
                "attachment_id": str(attachment_id),
                "version_id": str(version_id),
                "workspace_id": str(workspace_id),
                "generation": int(generation),
                "kind": str(kind),
                "source_run_id": str(source_run_id),
                "payload_sha256": str(payload_sha256),
                "actor_metadata": actor_metadata,
            }
            self.rowcount = 1
        elif "/* lineage:mark-purging */" in sql:
            workspace_id, generation = params
            workspace = workspaces[str(workspace_id)]
            if workspace["generation"] == generation and workspace["lifecycle_state"] == "active":
                workspace["lifecycle_state"] = "purging"
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif "/* lineage:delete-attachments */" in sql:
            workspace_id, generation = params
            doomed = [
                key
                for key, value in attachments.items()
                if value["workspace_id"] == workspace_id and value["generation"] == generation
            ]
            for key in doomed:
                del attachments[key]
            self.rowcount = len(doomed)
        elif "/* lineage:delete-versions */" in sql:
            workspace_id, generation = params
            doomed = [
                key
                for key, value in versions.items()
                if value["workspace_id"] == workspace_id and value["generation"] == generation
            ]
            for key in doomed:
                del versions[key]
            self.rowcount = len(doomed)
        elif "/* lineage:mark-purged */" in sql:
            workspace_id, generation = params
            workspace = workspaces[str(workspace_id)]
            if workspace["generation"] == generation and workspace["lifecycle_state"] == "purging":
                workspace["lifecycle_state"] = "purged"
                workspace["next_version_ordinal"] = 1
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif "/* lineage:insert-generation-event */" in sql:
            workspace_id, generation, event_kind, actor_metadata = params
            state["events"].append(
                {
                    "workspace_id": workspace_id,
                    "generation": generation,
                    "event_kind": event_kind,
                    "actor_metadata": actor_metadata,
                }
            )
            self.rowcount = 1
        elif "/* lineage:recreate-workspace */" in sql:
            next_generation, workspace_id, generation = params
            workspace = workspaces[str(workspace_id)]
            if workspace["generation"] == generation and workspace["lifecycle_state"] == "purged":
                workspace["generation"] = int(next_generation)
                workspace["lifecycle_state"] = "active"
                workspace["next_version_ordinal"] = 1
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif "/* lineage:list-versions */" in sql:
            workspace_id, generation = params
            matches = [
                value
                for value in versions.values()
                if value["workspace_id"] == workspace_id and value["generation"] == generation
            ]
            matches.sort(key=lambda value: value["ordinal"])
            self._rows = [_row(value) for value in matches]
        elif "/* lineage:list-attachments */" in sql:
            workspace_id, generation = params
            matches = [
                value
                for value in attachments.values()
                if value["workspace_id"] == workspace_id and value["generation"] == generation
            ]
            matches.sort(key=lambda value: value["attachment_id"])
            self._rows = [_row(value) for value in matches]
        else:
            raise AssertionError(f"Unexpected SQL statement: {sql}")
        return self

    def fetchone(self) -> SimpleNamespace | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[SimpleNamespace]:
        return list(self._rows)


def _row(value: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**value)


def _assert_schema_contract(sql: str) -> None:
    ddl = sql.upper()
    assert "IF OBJECT_ID" in ddl
    assert "ROWVERSION" not in ddl
    assert "VERDICT" not in ddl
    assert "CONFIDENCE" not in ddl
    assert "UNIQUE (WORKSPACE_ID, GENERATION, ORDINAL)" in ddl
    assert "FOREIGN KEY (VERSION_ID, WORKSPACE_ID, GENERATION)" in ddl
    assert (
        "REFERENCES DF_LINEAGE.EXPERIMENT_VERSION (VERSION_ID, WORKSPACE_ID, GENERATION)"
        in ddl
    )


def _api():
    return importlib.import_module("backend.lineage_sql")


def _repository(database: _MemorySqlDatabase):
    return _api().LineageRepository(connection_factory=database.connect)


def test_version_commit_canonicalizes_sql_uniqueidentifier_text() -> None:
    api = _api()
    row = SimpleNamespace(
        version_id="{A0B1C2D3-E4F5-4678-9012-3456789ABCDE}",
        workspace_id="workspace-1",
        generation=1,
        ordinal=1,
        canonical_run_id="run-1",
        decision_fingerprint="1" * 64,
        evidence_fingerprint="2" * 64,
    )

    commit = api._version_commit(row, created=False)

    assert commit.version_id == "a0b1c2d3-e4f5-4678-9012-3456789abcde"


def _real_sql_connection_factory():
    reference = os.environ[_REAL_SQL_FACTORY_ENV]
    if not _FACTORY_REFERENCE_PATTERN.fullmatch(reference):
        pytest.fail(f"{_REAL_SQL_FACTORY_ENV} must be a non-secret module:function reference")
    module_name, attribute_name = reference.split(":", maxsplit=1)
    factory = getattr(importlib.import_module(module_name), attribute_name, None)
    if not callable(factory):
        pytest.fail(f"{_REAL_SQL_FACTORY_ENV} did not resolve to a callable")
    return factory


def _cleanup_real_sql_workspace(connection_factory, workspace_id: str) -> None:
    connection = connection_factory()
    try:
        connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM df_lineage.experiment_attachment WHERE workspace_id = ?",
            workspace_id,
        )
        cursor.execute(
            "DELETE FROM df_lineage.experiment_version WHERE workspace_id = ?",
            workspace_id,
        )
        cursor.execute(
            "DELETE FROM df_lineage.workspace_generation_event WHERE workspace_id = ?",
            workspace_id,
        )
        cursor.execute(
            "DELETE FROM df_lineage.workspace_lineage WHERE workspace_id = ?",
            workspace_id,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class _DiagnosticError(Exception):
    pass


def _is_expected_attachment_fk_violation(error: BaseException) -> bool:
    diagnostic = " ".join(str(value) for value in error.args)
    sqlstates = set(re.findall(r"(?<![A-Z0-9])[A-Z0-9]{5}(?![A-Z0-9])", diagnostic.upper()))
    return (
        "23000" in sqlstates
        and _ATTACHMENT_FK_NAME.casefold() in diagnostic.casefold()
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            _DiagnosticError(
                "23000",
                '[Microsoft][ODBC Driver 18 for SQL Server][SQL Server]The INSERT statement conflicted with the FOREIGN KEY constraint "FK_experiment_attachment_version". The conflict occurred in database "lineage", table "df_lineage.experiment_version". (547)',
            ),
            True,
        ),
        (
            _DiagnosticError(
                "28000",
                '[Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Login failed for user. "FK_experiment_attachment_version"',
            ),
            False,
        ),
        (
            _DiagnosticError(
                "23000",
                '[Microsoft][ODBC Driver 18 for SQL Server][SQL Server]The INSERT statement conflicted with the FOREIGN KEY constraint "FK_other". (547)',
            ),
            False,
        ),
    ],
)
def test_attachment_fk_violation_classifier_requires_sqlstate_and_constraint(error, expected) -> None:
    assert _is_expected_attachment_fk_violation(error) is expected


def _commit(repository, run_id: str, *, decision: str = "a" * 64, evidence: str = "b" * 64):
    return repository.commit_analysis(
        workspace_id="workspace-1",
        generation=1,
        canonical_run_id=run_id,
        decision_fingerprint=decision,
        evidence_fingerprint=evidence,
        actor_metadata=_ACTOR_METADATA,
    )


def test_parallel_duplicate_commits_share_one_canonical_ordinal() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)

    with ThreadPoolExecutor(max_workers=8) as executor:
        commits = list(executor.map(lambda index: _commit(repository, f"run-{index}"), range(8)))

    assert {commit.ordinal for commit in commits} == {1}
    assert len({commit.version_id for commit in commits}) == 1
    assert sum(commit.created for commit in commits) == 1
    assert len(database.versions) == 1


def test_parallel_distinct_commits_allocate_contiguous_ordinals() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)

    with ThreadPoolExecutor(max_workers=6) as executor:
        commits = list(
            executor.map(
                lambda index: _commit(
                    repository,
                    f"run-{index}",
                    decision=f"{index:064x}",
                    evidence=f"{index + 100:064x}",
                ),
                range(6),
            )
        )

    assert sorted(commit.ordinal for commit in commits) == [1, 2, 3, 4, 5, 6]
    assert len({commit.version_id for commit in commits}) == 6


def test_purge_is_terminal_until_explicit_recreation() -> None:
    api = _api()
    database = _MemorySqlDatabase()
    repository = _repository(database)
    version = _commit(repository, "run-before-purge")

    assert repository.purge_workspace(
        workspace_id="workspace-1", generation=1, actor_metadata=_ACTOR_METADATA
    )
    assert not repository.purge_workspace(
        workspace_id="workspace-1", generation=1, actor_metadata=_ACTOR_METADATA
    )
    assert database.versions == {}

    with pytest.raises(api.LineageUnavailable, match="workspace generation is not active"):
        _commit(repository, "late-run")
    with pytest.raises(api.LineageUnavailable, match="workspace generation is not active"):
        repository.attach_snapshot(
            workspace_id="workspace-1",
            generation=1,
            version_id=version.version_id,
            kind="plan",
            source_run_id="late-run",
            payload_sha256="c" * 64,
        )

    assert repository.recreate_workspace(
        workspace_id="workspace-1", generation=1, actor_metadata=_ACTOR_METADATA
    ) == 2
    with pytest.raises(api.LineageUnavailable, match="workspace generation is not active"):
        _commit(repository, "stale-generation-run")
    recreated = repository.commit_analysis(
        workspace_id="workspace-1",
        generation=2,
        canonical_run_id="new-generation-run",
        decision_fingerprint="d" * 64,
        evidence_fingerprint="e" * 64,
    )
    assert recreated.ordinal == 1
    assert recreated.generation == 2


def test_purge_without_sql_workspace_preserves_legacy_payload(tmp_path, monkeypatch) -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)
    workspace_id = "workspace-legacy-only"
    run_id = "legacy-analysis"
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    payload_path = run_dir / f"{run_store._safe_name(run_id)}.json"
    payload_path.write_text("{}", encoding="utf-8")
    summary = {"run_id": run_id, "workspace_id": workspace_id}

    monkeypatch.setattr(run_store, "RUN_DIR", run_dir)
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    monkeypatch.setattr(run_store, "list_runs", lambda _workspace_id=None: [summary])
    monkeypatch.setattr(
        run_store,
        "authoritative_run_registry",
        lambda _workspace_id=None: {
            "version": 2,
            "revision": 1,
            "history_truncated": False,
            "read_status": "present",
            "runs": [summary],
        },
    )
    monkeypatch.setattr(run_store, "_write_local_registry", lambda _value: None)
    monkeypatch.setattr(run_store, "set_flagship_plan", lambda *_args, **_kwargs: {})

    result = run_store.purge_workspace_runs(
        workspace_id,
        lineage_repository=repository,
    )

    assert result == {
        "workspace_id": workspace_id,
        "run_ids": [],
        "deleted_local_runs": 0,
        "deleted_blob_runs": 0,
        "registry_updated": False,
        "lineage_updated": False,
        "status": "unavailable",
        "reason": "lineage_unavailable",
    }
    assert payload_path.is_file()
    assert database.workspaces == {}


def test_attachment_requires_a_version_in_the_active_generation() -> None:
    api = _api()
    database = _MemorySqlDatabase()
    repository = _repository(database)
    version = _commit(repository, "analysis-run")

    with pytest.raises(api.LineageUnavailable, match="version is not available for attachment"):
        repository.attach_snapshot(
            workspace_id="workspace-1",
            generation=1,
            version_id="00000000-0000-0000-0000-000000000000",
            kind="plan",
            source_run_id="plan-run",
            payload_sha256="c" * 64,
        )

    first = repository.attach_snapshot(
        workspace_id="workspace-1",
        generation=1,
        version_id=version.version_id,
        kind="plan",
        source_run_id="plan-run",
        payload_sha256="c" * 64,
    )
    duplicate = repository.attach_snapshot(
        workspace_id="workspace-1",
        generation=1,
        version_id=version.version_id,
        kind="plan",
        source_run_id="plan-run",
        payload_sha256="c" * 64,
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.attachment_id == first.attachment_id
    assert len(database.attachments) == 1


def test_repository_reads_current_generation_and_sql_attachment_rows() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)
    version = _commit(repository, "analysis-run")
    attachment = repository.attach_snapshot(
        workspace_id="workspace-1",
        generation=1,
        version_id=version.version_id,
        kind="artifact_generation",
        source_run_id="analysis-run",
        payload_sha256="c" * 64,
    )

    assert repository.current_generation(workspace_id="workspace-1") == 1
    listed = repository.list_attachments(workspace_id="workspace-1", generation=1)
    assert [item.attachment_id for item in listed] == [attachment.attachment_id]

    assert repository.purge_workspace(workspace_id="workspace-1", generation=1)
    assert repository.recreate_workspace(workspace_id="workspace-1", generation=1) == 2
    assert repository.current_generation(workspace_id="workspace-1") == 2
    assert repository.list_attachments(workspace_id="workspace-1", generation=2) == ()


def test_schema_is_idempotent_and_declares_database_foreign_keys() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)

    repository.initialize_schema()
    repository.initialize_schema()

    assert database.schema_executions == 2


def test_dynamic_values_are_bound_and_locking_queries_use_required_hints() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)
    malicious_workspace_id = "workspace'; DROP TABLE experiment_version;--"

    repository.commit_analysis(
        workspace_id=malicious_workspace_id,
        generation=1,
        canonical_run_id="run-1",
        decision_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
    )

    executed_sql = [sql for sql, _params in database.executions]
    assert all(malicious_workspace_id not in sql for sql in executed_sql)
    lock_statements = [sql for sql in executed_sql if "lineage:lock-" in sql]
    assert lock_statements
    assert all("UPDLOCK, HOLDLOCK" in sql for sql in lock_statements)


def test_commit_does_not_accept_caller_selected_outcome_strength() -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)

    with pytest.raises(TypeError):
        repository.commit_analysis(
            workspace_id="workspace-1",
            generation=1,
            canonical_run_id="run-1",
            decision_fingerprint="a" * 64,
            evidence_fingerprint="b" * 64,
            verdict="production_confirmed",
            confidence="observed",
        )

    assert database.executions == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"actor_id": "sql-password=not-safe", "actor_type": "member"},
        {
            "actor_id": _ACTOR_METADATA["actor_id"],
            "actor_type": "member",
            "request_id": "eyJhbGciOiJub25lIn0.token.claims",
        },
        {
            "actor_id": _ACTOR_METADATA["actor_id"],
            "actor_type": "member",
            "context": '{"claims":["not-safe"]}',
        },
        {
            "actor_id": _ACTOR_METADATA["actor_id"],
            "actor_type": {"nested": "member"},
        },
    ],
)
def test_actor_metadata_rejects_tokens_claims_and_credentials_in_benign_fields(metadata) -> None:
    database = _MemorySqlDatabase()
    repository = _repository(database)

    with pytest.raises(ValueError):
        repository.commit_analysis(
            workspace_id="workspace-1",
            generation=1,
            canonical_run_id="run-1",
            decision_fingerprint="a" * 64,
            evidence_fingerprint="b" * 64,
            actor_metadata=metadata,
        )

    assert database.executions == []


@pytest.mark.skipif(
    not os.environ.get(_REAL_SQL_FACTORY_ENV),
    reason=(
        "set LINEAGE_SQL_TEST_CONNECTION_FACTORY to an already-provisioned, "
        "non-secret module:function connection factory to run Azure SQL coverage"
    ),
)
def test_real_sql_server_schema_concurrency_and_attachment_foreign_key() -> None:
    import pyodbc

    connection_factory = _real_sql_connection_factory()
    repository = _api().LineageRepository(connection_factory=connection_factory)
    workspace_id = f"it-lineage-{uuid4()}"

    try:
        repository.initialize_schema()
        repository.initialize_schema()

        with ThreadPoolExecutor(max_workers=2) as executor:
            commits = list(
                executor.map(
                    lambda item: repository.commit_analysis(
                        workspace_id=workspace_id,
                        generation=1,
                        canonical_run_id=f"integration-run-{item}",
                        decision_fingerprint=f"{item + 1:064x}",
                        evidence_fingerprint=f"{item + 101:064x}",
                        actor_metadata=_ACTOR_METADATA,
                    ),
                    range(2),
                )
            )

        assert sorted(commit.ordinal for commit in commits) == [1, 2]

        connection = connection_factory()
        try:
            connection.autocommit = False
            cursor = connection.cursor()
            with pytest.raises(pyodbc.IntegrityError) as raised:
                cursor.execute(
                    """INSERT INTO df_lineage.experiment_attachment (
                        attachment_id,
                        version_id,
                        workspace_id,
                        generation,
                        kind,
                        source_run_id,
                        payload_sha256,
                        actor_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    str(uuid4()),
                    commits[0].version_id,
                    workspace_id,
                    2,
                    "plan",
                    "integration-plan-run",
                    "f" * 64,
                    None,
                )
                connection.commit()
            assert _is_expected_attachment_fk_violation(raised.value)
        finally:
            connection.rollback()
            connection.close()
    finally:
        _cleanup_real_sql_workspace(connection_factory, workspace_id)

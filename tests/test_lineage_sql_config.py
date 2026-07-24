from __future__ import annotations

import importlib
import hmac
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_VALID_ENV = {
    "LINEAGE_SQL_SERVER": "dataforge-lineage.database.windows.net",
    "LINEAGE_SQL_DATABASE": "df_lineage",
}
_UNAVAILABLE_MESSAGE = "lineage database is unavailable"
_WORKLOAD_IDENTITY_ENVIRONMENT = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
)
_MANAGED_IDENTITY_ENVIRONMENT = (
    "IDENTITY_ENDPOINT",
    "IDENTITY_HEADER",
    "MSI_ENDPOINT",
    "MSI_SECRET",
)


class _Credential:
    def __init__(self, token_length: int = 19) -> None:
        self.scopes: list[str] = []
        self._token_length = token_length

    def get_token(self, scope: str):
        self.scopes.append(scope)
        return SimpleNamespace(token="x" * self._token_length)


def _api():
    return importlib.import_module("backend.lineage_sql")


def _clear_identity_environment(monkeypatch) -> None:
    for name in _WORKLOAD_IDENTITY_ENVIRONMENT + _MANAGED_IDENTITY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"LINEAGE_SQL_SERVER": _VALID_ENV["LINEAGE_SQL_SERVER"]},
        {"LINEAGE_SQL_DATABASE": _VALID_ENV["LINEAGE_SQL_DATABASE"]},
        {**_VALID_ENV, "LINEAGE_SQL_SERVER": "not-an-azure-sql-host"},
        {**_VALID_ENV, "LINEAGE_SQL_SERVER": "server.database.windows.net;UID=unsafe"},
        {**_VALID_ENV, "LINEAGE_SQL_DATABASE": "lineage;PWD=unsafe"},
    ],
)
def test_invalid_production_configuration_fails_closed_before_authentication(environment) -> None:
    api = _api()
    credential = _Credential()
    connector_calls = []
    factory = api.build_lineage_sql_connection_factory(
        environ=environment,
        credential=credential,
        connect=lambda *args, **kwargs: connector_calls.append((args, kwargs)),
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert credential.scopes == []
    assert connector_calls == []


def test_factory_creation_is_lazy_when_sql_configuration_is_missing() -> None:
    api = _api()

    factory = api.build_lineage_sql_connection_factory(environ={})

    assert callable(factory)


def test_system_assigned_managed_identity_is_constructed_lazily_with_workload_identity_excluded(monkeypatch) -> None:
    api = _api()
    credentials = []

    def credential_factory(*args, **kwargs):
        credential = _Credential()
        credentials.append((args, kwargs, credential))
        return credential

    monkeypatch.setattr(api, "ManagedIdentityCredential", credential_factory)
    factory = api.build_lineage_sql_connection_factory(
        environ=_VALID_ENV,
        connect=lambda *args, **kwargs: object(),
    )

    assert credentials == []
    factory()

    assert len(credentials) == 1
    args, kwargs, credential = credentials[0]
    assert args == ()
    assert kwargs == {"_exclude_workload_identity_credential": True}
    assert credential.scopes == ["https://database.windows.net/.default"]


def test_dedicated_user_assigned_managed_identity_is_selected_lazily(monkeypatch) -> None:
    api = _api()
    credentials = []
    client_id = "11111111-2222-3333-4444-555555555555"

    def credential_factory(*args, **kwargs):
        credential = _Credential()
        credentials.append((args, kwargs, credential))
        return credential

    monkeypatch.setattr(api, "ManagedIdentityCredential", credential_factory)
    factory = api.build_lineage_sql_connection_factory(
        environ={
            **_VALID_ENV,
            "LINEAGE_SQL_MANAGED_IDENTITY_CLIENT_ID": client_id,
        },
        connect=lambda *args, **kwargs: object(),
    )

    assert credentials == []
    factory()

    assert len(credentials) == 1
    args, kwargs, credential = credentials[0]
    assert args == ()
    assert kwargs == {
        "client_id": client_id,
        "_exclude_workload_identity_credential": True,
    }
    assert credential.scopes == ["https://database.windows.net/.default"]


def test_production_factory_does_not_admit_credential_chain_or_user_assigned_configuration(monkeypatch) -> None:
    api = _api()
    credentials = []

    def credential_factory(*args, **kwargs):
        credential = _Credential()
        credentials.append((args, kwargs, credential))
        return credential

    monkeypatch.setattr(api, "ManagedIdentityCredential", credential_factory)
    environment = {
        **_VALID_ENV,
        "AZURE_CLIENT_ID": "user-assigned-identity",
        "AZURE_CLIENT_SECRET": "not-a-production-lineage-config-value",
        "AZURE_TENANT_ID": "not-a-production-lineage-config-value",
    }
    factory = api.build_lineage_sql_connection_factory(
        environ=environment,
        connect=lambda *args, **kwargs: object(),
    )

    factory()

    assert len(credentials) == 1
    assert credentials[0][0] == ()
    assert credentials[0][1] == {"_exclude_workload_identity_credential": True}
    source = Path(api.__file__).read_text(encoding="utf-8")
    assert "DefaultAzureCredential" not in source
    assert "ClientSecretCredential" not in source
    assert "AzureCliCredential" not in source
    assert "_exclude_workload_identity_credential=True" in source


def test_real_workload_identity_environment_fails_closed_as_configuration(monkeypatch) -> None:
    api = _api()
    _clear_identity_environment(monkeypatch)
    for name, value in _VALID_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AZURE_TENANT_ID", "workload-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "workload-client")
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", "C:/non-secret/federated-token")
    connector_calls = []
    factory = api.build_lineage_sql_connection_factory(
        connect=lambda *args, **kwargs: connector_calls.append((args, kwargs)),
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert factory.outcome.failure_category == "configuration"
    assert connector_calls == []


def test_real_workload_identity_environment_never_constructs_managed_identity(monkeypatch) -> None:
    api = _api()
    _clear_identity_environment(monkeypatch)
    for name, value in _VALID_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AZURE_TENANT_ID", "workload-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "workload-client")
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", "C:/non-secret/federated-token")
    constructed = []

    def unexpected_credential(*args, **kwargs):
        constructed.append((args, kwargs))
        raise AssertionError("ManagedIdentityCredential must not be constructed")

    monkeypatch.setattr(api, "ManagedIdentityCredential", unexpected_credential)
    factory = api.build_lineage_sql_connection_factory(
        connect=lambda *args, **kwargs: object(),
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert factory.outcome.failure_category == "configuration"
    assert constructed == []


def test_real_container_apps_system_identity_environment_remains_valid(monkeypatch) -> None:
    api = _api()
    _clear_identity_environment(monkeypatch)
    for name, value in _VALID_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1/managed-identity")
    monkeypatch.setenv("IDENTITY_HEADER", "container-apps-identity-header")
    credentials = []

    def credential_factory(*args, **kwargs):
        credential = _Credential()
        credentials.append((args, kwargs, credential))
        return credential

    monkeypatch.setattr(api, "ManagedIdentityCredential", credential_factory)
    factory = api.build_lineage_sql_connection_factory(
        connect=lambda *args, **kwargs: object(),
    )

    factory()

    assert len(credentials) == 1
    assert credentials[0][0] == ()
    assert credentials[0][1] == {"_exclude_workload_identity_credential": True}
    assert credentials[0][2].scopes == ["https://database.windows.net/.default"]


@pytest.mark.parametrize("token_length", [1, 19, 256])
def test_access_token_uses_odbc_length_prefixed_utf16le_layout(token_length) -> None:
    api = _api()
    token = "x" * token_length

    packed = api._pack_access_token(token)

    encoded_length = int.from_bytes(packed[:4], byteorder="little")
    assert encoded_length == token_length * 2
    assert len(packed) == encoded_length + 4
    assert set(packed[5::2]) == {0}
    assert hmac.compare_digest(packed[4:], token.encode("utf-16-le"))


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("Driver", "{ODBC Driver 18 for SQL Server}"),
        ("Server", "tcp:dataforge-lineage.database.windows.net,1433"),
        ("Database", "df_lineage"),
        ("Encrypt", "yes"),
        ("TrustServerCertificate", "no"),
    ],
)
def test_connection_uses_managed_identity_and_required_odbc_options(option, expected) -> None:
    api = _api()
    credential = _Credential()
    calls = []
    expected_connection = object()

    def connect(connection_string, **kwargs):
        calls.append((connection_string, kwargs))
        return expected_connection

    factory = api.build_lineage_sql_connection_factory(
        environ=_VALID_ENV,
        credential=credential,
        connect=connect,
    )

    assert factory() is expected_connection
    assert len(calls) == 1
    connection_string, kwargs = calls[0]
    options = dict(
        option.split("=", maxsplit=1)
        for option in connection_string.rstrip(";").split(";")
    )
    assert options[option] == expected
    assert set(options) == {"Driver", "Server", "Database", "Encrypt", "TrustServerCertificate"}
    assert kwargs["timeout"] == 5
    assert set(kwargs["attrs_before"]) == {api.SQL_COPT_SS_ACCESS_TOKEN}
    packed = kwargs["attrs_before"][api.SQL_COPT_SS_ACCESS_TOKEN]
    assert int.from_bytes(packed[:4], byteorder="little") == len(packed) - 4
    assert credential.scopes == ["https://database.windows.net/.default"]
    assert not {"UID", "PWD", "Authentication", "AccessToken"}.intersection(options)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("credential secret-token-value"),
        ModuleNotFoundError("ODBC Driver 18 missing at /internal/path"),
        TimeoutError("server.database.windows.net timed out after credential details"),
    ],
)
def test_auth_driver_and_connection_failures_share_one_redacted_error(failure) -> None:
    api = _api()

    class FailingCredential:
        def get_token(self, _scope):
            if isinstance(failure, RuntimeError) and not isinstance(failure, TimeoutError):
                raise failure
            return SimpleNamespace(token="x" * 19)

    def failing_connect(*_args, **_kwargs):
        raise failure

    factory = api.build_lineage_sql_connection_factory(
        environ=_VALID_ENV,
        credential=FailingCredential(),
        connect=failing_connect,
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("environment", "credential", "connect", "expected_category"),
    [
        ({}, _Credential(), lambda *args, **kwargs: object(), "configuration"),
        (
            _VALID_ENV,
            SimpleNamespace(get_token=lambda _scope: (_ for _ in ()).throw(RuntimeError("token secret"))),
            lambda *args, **kwargs: object(),
            "token",
        ),
        (
            _VALID_ENV,
            _Credential(),
            lambda *args, **kwargs: (_ for _ in ()).throw(ModuleNotFoundError("driver path")),
            "driver",
        ),
        (
            _VALID_ENV,
            _Credential(),
            lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("connection detail")),
            "connection",
        ),
    ],
)
def test_failure_category_is_retained_only_as_safe_in_process_outcome(
    environment, credential, connect, expected_category
) -> None:
    api = _api()
    factory = api.build_lineage_sql_connection_factory(
        environ=environment,
        credential=credential,
        connect=connect,
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert factory.outcome == api.LineageConnectionOutcome(
        available=False,
        failure_category=expected_category,
    )
    assert "secret" not in repr(factory.outcome)
    assert "path" not in repr(factory.outcome)
    assert "detail" not in repr(factory.outcome)


def test_missing_registered_odbc_driver_is_a_safe_driver_outcome(monkeypatch) -> None:
    api = _api()
    connect_calls = []
    monkeypatch.setitem(
        sys.modules,
        "pyodbc",
        SimpleNamespace(
            drivers=lambda: [],
            connect=lambda *args, **kwargs: connect_calls.append((args, kwargs)),
        ),
    )
    factory = api.build_lineage_sql_connection_factory(
        environ=_VALID_ENV,
        credential=_Credential(),
    )

    with pytest.raises(api.LineageUnavailable) as raised:
        factory()

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert factory.outcome.failure_category == "driver"
    assert connect_calls == []


def test_dockerfile_checks_registered_odbc_driver_before_app_import_smoke() -> None:
    dockerfile = Path(__file__).parents[1] / "backend" / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "import pyodbc" in content
    assert "pyodbc.drivers()" in content
    assert "ODBC Driver 18 for SQL Server" in content
    assert content.index("pyodbc.drivers()") < content.index("python -m backend.import_smoke")


def test_app_lineage_repository_registration_is_lazy_without_configuration(monkeypatch) -> None:
    monkeypatch.delenv("LINEAGE_SQL_SERVER", raising=False)
    monkeypatch.delenv("LINEAGE_SQL_DATABASE", raising=False)
    app_module = importlib.import_module("backend.app")
    api = _api()
    monkeypatch.setattr(
        app_module,
        "_LINEAGE_CONNECTION_FACTORY",
        api.build_lineage_sql_connection_factory(),
    )
    monkeypatch.setattr(
        app_module,
        "_LINEAGE_REPOSITORY",
        api.LineageRepository(connection_factory=app_module._LINEAGE_CONNECTION_FACTORY),
    )

    repository = app_module.get_lineage_repository()

    assert isinstance(repository, api.LineageRepository)
    assert app_module.get_lineage_sql_connection_outcome() == api.LineageConnectionOutcome(
        available=None,
        failure_category=None,
    )
    with pytest.raises(api.LineageUnavailable) as raised:
        repository.initialize_schema()
    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert app_module.get_lineage_sql_connection_outcome().failure_category == "configuration"

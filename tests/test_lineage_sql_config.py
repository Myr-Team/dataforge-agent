from __future__ import annotations

import importlib
import hmac
from types import SimpleNamespace

import pytest


_VALID_ENV = {
    "LINEAGE_SQL_SERVER": "dataforge-lineage.database.windows.net",
    "LINEAGE_SQL_DATABASE": "df_lineage",
}
_UNAVAILABLE_MESSAGE = "lineage database is unavailable"


class _Credential:
    def __init__(self, token_length: int = 19) -> None:
        self.scopes: list[str] = []
        self._token_length = token_length

    def get_token(self, scope: str):
        self.scopes.append(scope)
        return SimpleNamespace(token="x" * self._token_length)


def _api():
    return importlib.import_module("backend.lineage_sql")


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


def test_default_credential_is_constructed_only_when_connection_is_requested(monkeypatch) -> None:
    api = _api()
    credentials = []

    def credential_factory():
        credential = _Credential()
        credentials.append(credential)
        return credential

    monkeypatch.setattr(api, "DefaultAzureCredential", credential_factory)
    factory = api.build_lineage_sql_connection_factory(
        environ=_VALID_ENV,
        connect=lambda *args, **kwargs: object(),
    )

    assert credentials == []
    factory()

    assert len(credentials) == 1
    assert credentials[0].scopes == ["https://database.windows.net/.default"]


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


def test_app_lineage_repository_registration_is_lazy_without_configuration(monkeypatch) -> None:
    monkeypatch.delenv("LINEAGE_SQL_SERVER", raising=False)
    monkeypatch.delenv("LINEAGE_SQL_DATABASE", raising=False)
    app_module = importlib.import_module("backend.app")
    api = _api()

    repository = app_module.get_lineage_repository()

    assert isinstance(repository, api.LineageRepository)
    with pytest.raises(api.LineageUnavailable) as raised:
        repository.initialize_schema()
    assert str(raised.value) == _UNAVAILABLE_MESSAGE

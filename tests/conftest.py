import base64
import json
import secrets

import pytest

import backend.audit_store as audit_store


@pytest.fixture(autouse=True)
def _explicit_isolated_audit_test_mode(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(audit_store, "AUDIT_DIR", audit_dir)
    monkeypatch.setenv("DF_AUDIT_LOCAL_DIR", str(audit_dir))
    monkeypatch.setenv("DF_AUDIT_LOCAL_MODE", "1")
    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "pytest-v1")
    monkeypatch.setenv(
        "DF_AUDIT_HMAC_KEYS",
        json.dumps({"pytest-v1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")}),
    )

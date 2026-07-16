from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verify_lineage_sql.py"


def _script_module():
    spec = importlib.util.spec_from_file_location("verify_lineage_sql_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_credential_like_arguments_are_bounded_and_redacted() -> None:
    sentinel = "TASK5_SENTINEL_MUST_NOT_ECHO"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--client-secret", sentinel],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert json.loads(completed.stdout) == {
        "mode": "verify",
        "reason": "invalid_arguments",
        "status": "failed",
    }
    assert completed.stderr == ""
    assert sentinel not in completed.stdout
    assert sentinel not in completed.stderr
    assert "--client-secret" not in completed.stdout
    assert "--client-secret" not in completed.stderr


def test_transaction_probe_identifies_the_failing_stage_without_driver_details(monkeypatch) -> None:
    module = _script_module()

    class _FailingRepository:
        def __init__(self, **_kwargs) -> None:
            pass

        def commit_analysis(self, **_kwargs):
            raise module.LineageUnavailable("driver detail must not escape")

    monkeypatch.setattr(module, "LineageRepository", _FailingRepository)
    monkeypatch.setattr(module, "_claim_ephemeral_workspace", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_ephemeral_workspace", lambda *_args: None)

    with pytest.raises(module.VerificationFailure) as raised:
        module.verify_transaction_behavior(lambda: object(), "00000000-0000-0000-0000-000000000001")

    assert raised.value.code == "transaction_first_commit_unavailable"
    assert "driver detail" not in str(raised.value)

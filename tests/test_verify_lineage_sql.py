from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verify_lineage_sql.py"


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

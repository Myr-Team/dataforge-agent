from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = (
    ROOT
    / "docs"
    / "validation"
    / "2026-07-28-member-budget-email-candidate-runbook.md"
)


def _communication_state_helper() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(
        r"^```powershell\s*\n(.*?)^```\s*$",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    matching = [
        block
        for block in blocks
        if "function Get-ValidatedCommunicationState" in block
    ]
    assert len(matching) == 1
    return matching[0].split("\n$CommunicationApiVersion", maxsplit=1)[0]


def test_linked_domains_shape_is_checked_before_array_coercion() -> None:
    helper = _communication_state_helper()

    assert "$linkedDomains -isnot [System.Array]" in helper
    assert helper.index("$linkedDomains -isnot [System.Array]") < helper.index(
        "@($linkedDomains)"
    )


@pytest.mark.parametrize(
    ("linked_domains", "accepted"),
    [
        (None, False),
        ("/subscriptions/example/domain", False),
        ([], True),
        (["/subscriptions/example/domain"], True),
        (
            [
                "/subscriptions/example/domain-a",
                "/subscriptions/example/domain-b",
            ],
            True,
        ),
        ([None], False),
        ([42], False),
        ([""], False),
        (["   "], False),
    ],
    ids=[
        "null",
        "scalar-string",
        "empty-array",
        "one-valid-string",
        "multiple-valid-strings",
        "null-element",
        "numeric-element",
        "empty-string-element",
        "whitespace-string-element",
    ],
)
def test_linked_domains_runtime_shape_validation(
    linked_domains: object,
    accepted: bool,
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable; static helper contract still runs")

    helper = _communication_state_helper()
    state_json = json.dumps(
        {"properties": {"linkedDomains": linked_domains}},
        separators=(",", ":"),
    )
    script = f"""
{helper}
function az {{
  $global:LASTEXITCODE = 0
  return '{state_json}'
}}
try {{
  $null = Get-ValidatedCommunicationState -Uri 'https://local.invalid'
  Write-Output 'ACCEPT'
}} catch {{
  Write-Output 'REJECT'
}}
"""
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"PowerShell harness failed for {linked_domains!r}: "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    expected_result = "ACCEPT" if accepted else "REJECT"
    assert result.stdout.strip() == expected_result, (
        f"unexpected validation result for {linked_domains!r}: "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )

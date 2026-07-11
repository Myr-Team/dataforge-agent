# Task 1 Report

## Scope

Implemented the MAF runtime contracts and deterministic runtime configuration in the authorized backend and test files. Cleaned the graph description metadata so its labels are valid UTF-8 Chinese text.

## TDD Evidence

1. Added `tests/test_maf_contracts.py` before production implementation.
2. Ran `python -m pytest tests/test_maf_contracts.py -q` and confirmed the expected collection failure: `ModuleNotFoundError: No module named 'backend.maf_contracts'`.
3. Added the minimal implementation in `backend/maf_contracts.py` and corrected `graph_description` metadata in `backend/maf_orchestrator.py`.
4. Re-ran the focused suite: `8 passed`.

## Implementation

- Added `MafRuntimeMode` and `CollaborationPattern` enums.
- Added Pydantic `CollaborationPlan`, `MafAgentRecord`, and `MafRunSummary` models.
- Added explicit runtime mode resolution with legacy `DF_USE_MAF=1` mapping to audit mode.
- Added clamped traffic percentage parsing and stable SHA-256 canary selection.
- Replaced corrupted graph description labels with UTF-8-clean text.

## Verification

- `python -m pytest tests/test_maf_contracts.py -q`: `8 passed`
- `python -m pytest -q`: `66 passed`

## Concerns

None identified within the Task 1 scope.

## Review Fix Evidence

The review identified four valid runtime issues. Tests were added before implementation and the focused suite was run in the expected failing state: `6 failed, 10 passed`.

- `maf_enabled()` now uses `runtime_mode()` and requires MAF import availability; explicit `off` disables the graph while `audit` and `full` enable it.
- A present blank or invalid `DF_MAF_RUNTIME` now resolves to `off` and never falls back to `DF_USE_MAF`; the legacy flag is consulted only when the variable is absent.
- `MAX_MAF_REVISIONS = 2` is enforced by `CollaborationPlan`, `default_max_revisions()`, and the direct `run_feasibility_audit_loop(..., max_revisions=...)` argument.
- Canary selection remains a standalone contract helper and is not integrated into the orchestrator, as required for Task 4 scope control.

Post-fix verification:

- `python -m pytest tests/test_maf_contracts.py -q`: `16 passed`
- `python -m pytest -q`: `74 passed`

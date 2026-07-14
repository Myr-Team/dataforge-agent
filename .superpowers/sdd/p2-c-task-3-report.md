# P2-C Task 3: Experiment Version Promotion

## Implementation

- Canonical versions now require a completed analysis plus changed source-linked evidence, a newly incorporated source-linked observed metric, or a changed normalized decision.
- Duplicate analysis runs are aliased to the latest canonical version and do not increment version ordinals.
- Outcomes are incorporated only by a later completed analysis in the same run lineage; recording feedback alone does not mutate or promote the prior version.
- Evidence identity includes stable ref, file, connector, and version fields. Deltas deterministically report `added`, `removed`, `contradicted`, `strengthened`, `unchanged`, and `unverifiable`.
- Synthetic and untraceable inputs cannot increase verdict, confidence, or dimension scores. Source-linked evidence removal can still create a truthful downgraded-evidence version.
- Plan and artifact snapshot runs retain their compatibility shape but are marked as attachments to the server-derived existing `version:<source_run_id>`; they are never canonical experiment versions.
- Iteration input normalization preserves only bounded source-lineage fields and retains the explicit `synthetic` kind.

## TDD Evidence

RED command:

`python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

RED result: `7 failed, 4 passed in 3.98s`. Failures covered premature outcome incorporation, duplicate and synthetic-only promotion, missing source-version delta categories, and missing canonical attachment refs.

Additional RED command:

`python -m pytest tests/test_followup_plan_version.py::test_iteration_inputs_preserve_source_lineage_and_synthetic_kind -q`

Additional RED result: `1 failed in 2.80s` because normalized iteration metrics dropped `source` lineage.

GREEN command:

`python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

GREEN result: `12 passed in 3.06s`.

Broader regression command:

`python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py tests/test_data_workbench_task_bridge.py tests/test_outcome_roi.py -q`

Broader result: `57 passed in 21.66s`.

Final verification:

- Focused suite rerun: `12 passed in 3.03s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

## Changed Files

- `backend/experiment_store.py`
- `backend/outcome_store.py`
- `backend/run_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_followup_plan_version.py`
- `tests/test_artifact_version_snapshot.py`
- `.superpowers/sdd/p2-c-task-3-report.md`

## Remaining Risks

- Outcome-to-experiment association requires a source `run_id`; file-only outcomes remain recorded but cannot promote an experiment without explicit run lineage.
- Historical analysis records whose status is not `completed*` are intentionally excluded from promotion even if they contain feasibility fields.
- Attachment records retain legacy `version_kind` names for public-contract compatibility; canonical version counting is enforced by the experiment ledger.

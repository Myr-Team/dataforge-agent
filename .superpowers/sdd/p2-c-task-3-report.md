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

## R2 Review Remediation

### Behavior

- Outcome metrics retain their actual `observed`, `synthetic`, `target`, or `assumption` provenance and a bounded verification projection. Only source-linked `observed` metrics with verification status `verified` or `passed` are promotion-authoritative.
- New non-authoritative feedback cannot create either an upgraded or downgraded canonical version on its own. Its model decision is retained only as guarded metadata.
- Decision comparison uses verdict, confidence, and normalized dimension name/score/confidence. Opportunity labels, gaps, rationale, quotes, excerpts, and other model prose remain display data and cannot promote.
- Evidence is deduplicated and compared by ref plus complete source/file/connector/version identity. Less-specific citation copies are removed when a richer stable identity exists.
- Quote-only changes are `unchanged`. Structured value/status/direction/polarity changes and confidence decreases are adverse changes; confidence increases are `strengthened`.
- Every evidence delta item carries a deterministic human-readable `reason` derived from its stable identity or changed structured fields.
- Synthetic-only new dimensions are omitted from the effective decision and listed as unverifiable metadata without score authority.
- Follow-up completion no longer overwrites an existing completed analysis at the conversation ID. It persists a distinct follow-up run while keeping the original conversation scope and canonical source ID.
- Plan/artifact attachment persistence rebuilds the workspace ledger and requires an exact existing canonical `experiment_version_id`. Missing runs and deduplicated aliases fail closed before persistence.

### R2 TDD Evidence

Initial R2 RED command:

`python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

Initial R2 RED result: `10 failed, 12 passed in 4.24s`. Failures covered provenance/verification rewriting, wording authority, ref-only dedupe, synthetic new dimensions, missing item reasons, source-run overwrite, and nonexistent attachment targets.

Additional RED checks:

- Verification projection: `1 failed in 2.83s` because request normalization dropped verification status.
- Non-authoritative downgrade: `1 failed in 3.50s` because synthetic feedback could create a downgraded V2.
- Opportunity wording: `1 failed in 4.03s` because a renamed free-form label could create V2.

R2 GREEN results:

- Focused Task3 suite: `23 passed in 5.01s`.
- Run-store/MAF/outcome/workbench/audit compatibility suite: `122 passed in 27.00s`.
- Final combined Task3 and relevant run-store regression suite: `145 passed in 27.63s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

### R2 Deliberate Limitations

- File-only outcomes still require explicit source-run lineage before they can participate in experiment promotion.
- A new dimension without newly traceable evidence is fail-closed as unverifiable, even if a model presents it as a normalized decision change.
- Free-form opportunity labels are not promotion identity. A future stable server-issued opportunity ID can be added without restoring wording authority.

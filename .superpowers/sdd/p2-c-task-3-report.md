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

## R3 Review Remediation

### Behavior

- Request iteration inputs remain bounded display data. An `observed`/`verified` request body is always `reported_unverified` for promotion purposes.
- Observed metrics become authoritative only when the server outcome store contains the same event/value/source, a persisted trusted independent verification event, and a source that still validates in the workspace.
- New or strengthened dimensions require newly added or strengthened traceable evidence attached to that normalized dimension identity. Unrelated synthetic-only dimensions are omitted or clamped and reported as unverifiable.
- Evidence deltas classify passed/failed transitions and higher/lower-is-better value changes before generic structured contradiction, with deterministic favorable/adverse reasons.
- Verdict and confidence tokens are case/whitespace normalized; numeric dimension scores canonicalize equivalent values such as `3` and `"3"`.
- A plan follow-up started under a new conversation resolves the workspace's canonical last-analysis run before follow-up completion, persists the new conversation as the follow-up, and attaches the plan snapshot to the existing version.
- Artifact responses expose `experiment_version_id` only after exact canonical snapshot persistence succeeds. Failed attachment returns a bounded nonfatal unavailable state and warning without an ID.

### R3 TDD Evidence

RED command:

`python -m pytest -q tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py`

RED result: `10 failed, 23 passed in 4.60s`. Failures covered fabricated verification authority, mixed synthetic dimensions, semantic normalization, directional/status delta semantics, new-conversation plan attachment, and premature artifact version IDs.

GREEN commands and results:

- Focused Task3 suite: `33 passed in 5.25s`.
- Final Task3 plus endpoint/run-store/outcome persistence suite: `51 passed in 6.08s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

### R3 Changed Files

- `backend/experiment_store.py`
- `backend/outcome_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_followup_plan_version.py`
- `tests/test_artifact_version_snapshot.py`
- `.superpowers/sdd/p2-c-task-3-report.md`

### R3 Deliberate Limitation

- Dimension authority is fail-closed: an authoritative metric does not strengthen a dimension unless evidence with that dimension's stable identity is also attached to the completed analysis decision.

## R4 Review Remediation

### Behavior

- New-conversation plan follow-ups resolve a latest-analysis duplicate through the experiment ledger to its exact canonical source run before invoking the strict attachment writer.
- Dimension comparison uses normalized case/whitespace identity while retaining the decision's display spelling. Equal-rank verdict aliases such as `not_feasible`/`not_yet_feasible` and `feasible`/`recommended` compare semantically.
- Evidence snapshots canonicalize `pass`/`passed`/`verified` to one favorable state and normalize direction aliases such as `higher`/`higher_is_better`. Equivalent spellings are unchanged; actual favorable/adverse transitions still classify deterministically.
- Decision-only promotions include a deterministic reason on every changed normalized field, and those reasons are included in the summary.
- The unused previous-dimension evidence state and parameter were removed; dimension transition authority continues to derive from current dimension evidence intersecting added/strengthened evidence identities.

### R4 TDD Evidence

RED command:

`python -m pytest -q tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py`

RED result: `7 failed, 35 passed in 5.94s`. Failures covered duplicate-analysis alias attachment, dimension/verdict aliases, equivalent evidence spellings, opposing status transitions, and missing decision-only reasons.

GREEN commands and results:

- Focused Task3 suite: `42 passed in 6.60s`.
- Task3 plus follow-up, outcome, run-store, and control-plane integration suite: `81 passed in 7.49s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

### R4 Changed Files

- `backend/experiment_store.py`
- `backend/run_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_followup_plan_version.py`
- `.superpowers/sdd/p2-c-task-3-report.md`

### R4 Remaining Risk

- Canonical alias resolution uses the same bounded 300-run workspace scan as attachment validation. A source outside that retained window fails closed and is not attached.

## R5 Review Remediation

### Behavior

- Completed analysis runs now persist a server-derived `canonical_experiment_run_id` and resolution status. Alias links are accepted only when they terminate at an existing same-workspace analysis that is self-linked and resolved.
- Strict plan/artifact attachment validation and all producer paths use the same canonical resolver. Legacy fallback reads the complete available run view without a 300-run slice; truncated unproven history fails closed.
- PDF/general artifact, roadmap, and validation-plan generation resolve requested duplicate runs to the canonical source before proposal update and snapshot persistence. Unresolved requests return bounded unavailable state and warnings without an experiment version ID.
- Plan attachment is attempted before follow-up completion so success or bounded unavailable/failure state is persisted on the follow-up artifact instead of being silently dropped.
- Evidence normalization preserves `direction` and `polarity` independently. Polarity transitions produce deterministic favorable/adverse reasons.
- Status, polarity, direction, and directed value changes are evaluated together. Mixed favorable/adverse signals classify as conflict/adverse, cannot strengthen the decision, and do not authorize dimension score increases.
- Analysis and registry ordering use timestamp plus stable `run_id` tie-breaking.

### R5 TDD Evidence

Initial RED command:

`python -m pytest -q tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py`

Initial RED result: `8 failed, 42 passed in 7.88s`. Failures covered the truncated-history false canonical, duplicate PDF/roadmap/validation attachments, collapsed polarity, mixed-signal strengthening, unstable ties, and silent plan snapshot failure.

Additional proof-chain RED command:

`python -m pytest -q tests/test_artifact_version_snapshot.py::test_persisted_alias_fails_closed_when_target_is_not_self_resolved`

Additional RED result: `1 failed in 2.97s` because a persisted alias link to an unproven legacy target was accepted.

Truncated-ledger RED command:

`python -m pytest -q tests/test_artifact_version_snapshot.py::test_strict_writer_never_accepts_duplicate_when_canonical_is_outside_300_run_view`

Truncated-ledger RED result: `1 failed in 2.84s` because a durable alias without its canonical target in the view was still rendered as V1.

GREEN commands and results:

- Focused Task3 suite: `51 passed in 5.79s`.
- Task3 plus follow-up, outcome, run-store, and control-plane integration suite: `90 passed in 6.41s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

### R5 Changed Files

- `backend/experiment_store.py`
- `backend/run_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_followup_plan_version.py`
- `tests/test_artifact_version_snapshot.py`
- `.superpowers/sdd/p2-c-task-3-report.md`

### R5 Remaining Risk

- Legacy analyses without durable self-resolved canonical metadata cannot be attached when the server registry indicates truncated history. This is intentionally fail-closed; a complete-history migration can backfill those links later.

## R6 Structural Lineage Refactor

### Architecture

- Analysis completion now keeps canonical decision, trusted lineage assignment, registry summary construction, and run persistence under the same re-entrant run-store lock. Concurrent local completions therefore observe one serialized registry/run state instead of deciding lineage outside the persistence boundary.
- Blob-backed run registry writes use the existing revision/ETag compare-and-swap primitive with bounded retries. Failure to confirm the conditional update removes trusted canonical IDs and persists the analysis as unresolved/local-only, so an unconfirmed cross-process write cannot promote or accept attachments.
- Every server-resolved analysis lineage uses one envelope: exact canonical run ID, exact `version:<canonical_run_id>`, `resolved` resolution status, and `trusted` lineage status. Alias validation requires a same-workspace completed analysis source and a self-resolved, trusted, non-snapshot target; missing, inconsistent, cross-workspace, cyclic, or fabricated links fail closed.
- Ledger synchronization hydrates trusted canonical targets omitted from the control plane's recent-run window. Invalid or unavailable targets produce a bounded `lineage_resolution: unavailable` state and no attachment success. Artifact, roadmap, validation-plan, and plan follow-up paths continue to use the same strict resolver/writers.
- Workspace latest-analysis selection excludes all snapshot `version_kind` values. Same- and new-conversation plan follow-ups persist the canonical analysis `source_run_id` and exact experiment version on both the follow-up and plan snapshot.
- Evidence authorization is dimension-linked and fail-closed. Status, polarity, confidence, direction, and directed value changes are combined before classification; mixed signals are conflict/adverse. Newly added adverse evidence remains visible with a structured reason but cannot authorize dimension, verdict, score, or confidence strengthening.

### R6 TDD Evidence

Initial focused RED command:

`python -m pytest -q tests/test_artifact_version_snapshot.py::test_concurrent_completions_assign_and_persist_lineage_under_one_lock tests/test_artifact_version_snapshot.py::test_persisted_alias_requires_trusted_exact_experiment_lineage tests/test_experiment_versions.py::test_untrusted_nonself_lineage_is_bounded_unavailable_not_a_ledger_alias tests/test_experiment_versions.py::test_control_plane_hydrates_trusted_canonical_target_and_attachment tests/test_experiment_versions.py::test_favorable_status_with_adverse_confidence_is_not_strengthening tests/test_experiment_versions.py::test_new_traceable_adverse_evidence_cannot_authorize_strengthening tests/test_followup_plan_version.py::test_new_conversation_plan_resolves_latest_duplicate_analysis_alias tests/test_followup_plan_version.py::test_workspace_latest_analysis_excludes_plan_and_artifact_snapshots`

Initial RED result: `8 failed in 4.83s`. Failures demonstrated lock release before persistence, acceptance of inconsistent alias metadata, missing bounded lineage status/hydration, confidence conflicts treated as strengthening, adverse new evidence authorizing stronger decisions, missing canonical source fields on follow-ups, and snapshot selection as latest analysis.

GREEN results:

- Focused Task3 suite: `58 passed in 5.91s`.
- Relevant run-store, outcome, control-plane persistence, audit, and workspace-role suite: `124 passed in 10.15s`.
- Final combined focused and relevant suite: `182 passed in 13.22s`.
- `python -m py_compile backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py`: exit 0.
- `git diff --check`: exit 0.

### R6 Changed Files

- `backend/experiment_store.py`
- `backend/run_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_followup_plan_version.py`
- `tests/test_artifact_version_snapshot.py`
- `.superpowers/sdd/p2-c-task-3-report.md`

### R6 Remaining Risks

- Historical analyses without the trusted lineage envelope remain readable as legacy decisions, but they cannot be accepted as durable aliases when history is truncated. Backfill requires a server-side migration with complete source visibility.
- The Blob store has conditional single-blob updates, not a multi-blob transaction. The implementation uses conditional registry confirmation and fails lineage closed on any unconfirmed persistence path rather than claiming atomic success.

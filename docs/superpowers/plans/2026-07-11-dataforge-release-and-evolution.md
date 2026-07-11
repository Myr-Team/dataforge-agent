# DataForge Release And Evolution Implementation Plan

**Goal:** Restore source-to-production traceability, publish the accumulated product hardening, and leave executable architecture work for ROI, genuine MAF collaboration, evidence-based iteration, and industry generalization.

## Task 1: Release Truthfulness Fixes

**Files:**
- Modify: `backend/control_plane.py`
- Test: `tests/test_governance_roi_summary.py`
- Test: `tests/test_ui_truthfulness_contract.py`

- [x] Replace the static dashboard default workspace with the requested or configured workspace.
- [x] Count only actual model runs in ROI analysis/follow-up totals.
- [x] Expose ROI calculation status and assumptions without presenting estimates as measured value.
- [x] Add focused regression tests.

## Task 2: Source Hygiene

**Files:**
- Modify: `.gitignore`
- Review: all modified and untracked files

- [x] Exclude the local `tmp/` verification directory.
- [x] Confirm all deleted and replacement demo files are intentional.
- [x] Confirm no secret, connector credential, production payload, log, or build output is staged.
- [ ] Record the final source diff and release commit.

## Task 3: Local Verification

- [x] Run `python -m pytest -q`.
- [x] Run `python -m compileall -q backend`.
- [x] Run `npm run build` in `web`.
- [x] Run focused API smoke for system status, workspace overview, runs, governance, and artifacts.

## Task 4: GitHub Publication

- [ ] Stage explicit intended files.
- [ ] Commit with a release-focused message.
- [ ] Push the current `codex/` branch.
- [ ] Open a pull request against `main` with verification evidence and strategic follow-up scope.

## Task 5: Production Deployment

- [ ] Build backend image `dataforge-backend:release-<timestamp>-<sha>` in ACR.
- [ ] Build frontend image `dataforge-web:release-<timestamp>-<sha>` in ACR.
- [ ] Set release metadata environment variables on the backend.
- [ ] Update the backend Container App and verify the new revision.
- [ ] Update the web Container App and verify the new revision.

## Task 6: Production Verification

- [ ] Verify backend system status and dependency details.
- [ ] Verify workspaces, data, run summaries, artifacts, members, and governance APIs.
- [ ] Verify production navigation across workspaces, data, runs, conversation, artifacts, and settings.
- [ ] Send a grounded conversation question and verify SSE completion, persisted run, and coherent response.
- [ ] Confirm the deployed image tags and revisions match the release commit.

## Strategic Follow-Up

- [ ] R1: business outcome ledger and measured/verified ROI states.
- [ ] R2: OpenTelemetry correlation with Azure Monitor and Application Insights.
- [ ] R3: Foundry native ROI private-preview adapter.
- [ ] M1: first-class MAF specialist agents with typed contracts and independent telemetry.
- [ ] M2: coordinator-selected direct, concurrent, handoff, and bounded-review patterns.
- [ ] M3: evaluation gate and feature-flag rollout.
- [ ] I1: experiment, evidence, metric, and decision lineage.
- [ ] I2: source-backed customer feedback import and evidence deltas.
- [ ] G1: schema-driven capability packs without industry-specific conclusions.

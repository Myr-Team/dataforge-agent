# DataForge P0 Agent And Web Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the P0 demo-critical Agent conversation and Web controls feel complete: natural plan replies, durable plan versions, and clickable controls that either work or clearly explain their state.

**Architecture:** Keep the existing backend orchestrator and control-plane boundaries. Add focused behavior to follow-up completion, run version persistence, workspace member role APIs, and small frontend UI closures without broad refactors.

**Tech Stack:** FastAPI/Python backend, Vite/React frontend, existing pytest and Vite build verification.

## Global Constraints

- Do not change authentication or disable Easy Auth.
- Do not hard-code conclusions, scores, opportunities, or dataset-specific logic.
- Preserve UTF-8 Chinese text; avoid introducing `?` or mojibake into user-visible strings.
- Do not reset or revert unrelated dirty worktree changes.

---

### Task 1: Conversation Plan Versions

**Files:**
- Modify: `backend/orchestrator.py`
- Test: `tests/test_followup_plan_version.py`

**Interfaces:**
- Consumes: `_emit_lightweight_final(...)`, `record_artifact_version(...)`
- Produces: plan-draft follow-up runs with `version_kind="plan_draft"` and `produced_kinds=["plan_draft"]`

- [x] Write a failing test that a plan-draft follow-up persists a version.
- [x] Run the test and confirm it fails because no version is recorded.
- [x] Add a helper that records a plan version after `_persist_chat_completion` for `result["is_plan"]`.
- [x] Run the focused test and existing follow-up tests.

### Task 2: Member Role Update

**Files:**
- Modify: `backend/control_plane.py`
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Test: `tests/test_entra_member_invites.py`

**Interfaces:**
- Produces: `PATCH /api/workspaces/{workspace_id}/members/{email}` with body `{ role }`
- Produces frontend API `updateWorkspaceMemberRole(workspaceId, email, role)`

- [x] Write a failing backend test for changing an invited member from editor to viewer.
- [x] Add the FastAPI route and persistence helper.
- [x] Wire the existing role arrow into a real `<select>`.
- [x] Run focused backend tests and frontend build.

### Task 3: Button Closure And Status Copy

**Files:**
- Modify: `web/src/components.jsx`
- Modify: `web/src/DataWorkbench.jsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Settings “管理” opens the relevant tab or status card instead of appearing inert.
- Terms/privacy links open lightweight drawers.
- Data Lake planned connector opens a demo/coming-soon explanation rather than a dead disabled button.
- Left “Invite members” opens Settings > Members and focuses invite controls.

- [x] Replace inert text-like controls with buttons and local state drawers.
- [x] Add Data Lake planned connector explanation modal.
- [x] Connect Invite members navigation to Settings members tab.
- [x] Verify with Vite build and browser smoke.

### Task 4: Trace And Artifact Naming Polish

**Files:**
- Modify: `web/src/components.jsx`
- Modify: `backend/run_store.py`

**Interfaces:**
- Trace rows suppress empty `ready:`-style details.
- Artifact versions use business title plus V label when possible.

- [x] Add formatting tests where possible; otherwise verify via existing trace/run tests.
- [x] Polish user-visible run trace fallback text.
- [x] Ensure artifact/plan version title includes the opportunity or topic.
- [x] Run all focused tests.

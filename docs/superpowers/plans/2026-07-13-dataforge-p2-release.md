# DataForge P2 Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate P2-A/B/C, provision only required Azure dependencies, validate in preview and production, and promote MAF traffic only from measured evidence.

**Architecture:** Keep one stacked P2 branch and one final PR. Build immutable ACR images from committed source, deploy preview first, collect machine-readable acceptance evidence, then update production. MAF uses deterministic canary percentages with rollback modes preserved.

**Tech Stack:** Git/GitHub, pytest, Vite, Playwright, Azure CLI, Azure Container Apps, ACR, Key Vault, Application Insights, Microsoft Graph.

## Global Constraints

- Do not alter Easy Auth configuration.
- Do not print, persist, or return credentials, bearer tokens, Key Vault values, or connector secrets.
- Production claims require observed production evidence tied to build/revision/run IDs.
- P2 remains stacked on PR #15 until #15 merges; then rebase/retarget to `main` without force-pushing over other work.
- MAF promotion order is 10 -> 50 -> 100 and stops when any quality, latency, token, error, or grounding gate fails.

---

### Task 1: Plan And Source Integration Gate

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-dataforge-p2-a-productization.md`
- Modify: `docs/superpowers/plans/2026-07-13-dataforge-p2-b-azure-governance.md`
- Modify: `docs/superpowers/plans/2026-07-13-dataforge-p2-c-generalization.md`
- Create: `eval/run_p2_release_gate.py`
- Create: `tests/test_p2_release_gate.py`

- [ ] **Step 1: Write the release-gate test**

```python
def test_release_gate_requires_all_workstreams_and_observed_labels():
    report = build_release_gate(a_report(), b_report(), c_report())
    assert set(report["workstreams"]) == {"A", "B", "C"}
    assert report["production_ready"] is False  # fixture-only component reports
```

- [ ] **Step 2: Implement report composition**

Production readiness requires all functional gates pass and every production-sensitive metric carries build, revision, observed time, and run/task IDs. Fixture reports can prove behavior but cannot satisfy production latency, export, connector recovery, Graph invitation, or native ROI gates.

- [ ] **Step 3: Run full source verification**

Run: `python -m pytest -q`

Run: `python -m compileall -q backend tests eval`

Run: `npm audit --omit=dev` and `npm run build` from `web`.

Run: `python eval/run_p2_release_gate.py --a generated-outputs/p2-a-acceptance.json --b generated-outputs/p2-b-acceptance.json --c generated-outputs/p2-c-eval.json --output generated-outputs/p2-release-local.json`

Expected: tests/build pass; local report is not yet production-ready.

- [ ] **Step 4: Commit**

```powershell
git add docs/superpowers/plans eval/run_p2_release_gate.py tests/test_p2_release_gate.py
git commit -m "test: add P2 release evidence gate"
```

---

### Task 2: Azure Dependency Provisioning

**Files:**
- Create: `deploy/p2-azure-prerequisites.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Inventory existing resources without changing auth**

Record resource IDs and configuration state for Container Apps managed identity, Key Vault, Application Insights/Log Analytics, Foundry project/agents, Graph application permissions, ACR, Blob, Search, Redis, Speech, and Content Safety. Do not record secrets.

- [ ] **Step 2: Provision only missing dependencies**

Create or reuse one Key Vault, grant the backend managed identity least-privilege secret get/set/delete permissions, and set `DF_KEY_VAULT_URL`. Grant Azure Monitor query access only when needed. Graph permissions remain the minimum required for directory search/invitation and require explicit tenant admin consent when Azure reports it.

- [ ] **Step 3: Verify dependency probes**

Run backend dependency/status endpoints through the authenticated web proxy. Confirm Key Vault reports connected only after a put/get/delete smoke using a generated disposable secret whose value is never printed. Confirm Azure Monitor delivery only after a correlated trace is queried successfully.

- [ ] **Step 4: Document truthful Foundry ROI state**

If Foundry native ROI is not discoverable/configured, keep `not_configured` and document portal prerequisites. Do not set `DF_FOUNDRY_ROI_ENABLED=1` merely to make the UI green.

- [ ] **Step 5: Commit documentation**

```powershell
git add deploy/p2-azure-prerequisites.md README.md README.zh-CN.md
git commit -m "docs: record P2 Azure prerequisites"
```

---

### Task 3: Immutable Preview Deployment

**Files:**
- Create: `generated-outputs/p2-preview-acceptance.json` (ignored evidence output)

- [ ] **Step 1: Commit and verify clean source**

Run: `git status --short`, `git diff --check`, full pytest, compileall, npm audit, and Vite build. Stop if any tracked/unexpected file remains.

- [ ] **Step 2: Build immutable Linux images**

Build backend and web ACR tags using `p2-<git-sha>`. Record ACR digests. The build context excludes generated outputs, credentials, local connector sessions, and test downloads.

- [ ] **Step 3: Deploy preview revisions**

Deploy backend first, verify health/import/API contracts, then web. Preserve min replicas and existing Easy Auth. Preview uses the same Key Vault/Monitor integrations but isolated task/test records where possible.

- [ ] **Step 4: Run preview acceptance**

Verify six pages, market source rejection, MAF/legacy A/B, task navigation/reload recovery, connector reconnect/sync/disconnect, Entra invitation lifecycle, audit events, trace deep link, local/provider ROI distinction, capability-pack variation, experiment delta, branded PDF/image, and mobile overflow/console checks.

- [ ] **Step 5: Generate observed preview report**

Every item includes build SHA, image digests, revision names, run/task/invitation/experiment/artifact IDs, timestamps, and redacted screenshots/log summaries. Failed gates keep preview unpromoted.

---

### Task 4: Production Deployment And Canary Promotion

**Files:**
- Create: `generated-outputs/p2-production-acceptance.json` (ignored evidence output)

- [ ] **Step 1: Deploy committed P2 images to production**

Update backend then web. Confirm both latest revisions are Healthy and use the expected immutable digests. Confirm min replicas remain 1 and Easy Auth config is unchanged.

- [ ] **Step 2: Run production acceptance at 10 percent MAF**

Use deterministic conversation IDs that cover MAF and legacy paths. Execute the same reference cases and compare groundedness, unsupported claims, source relevance, task completion, wall time, token use, fallback/degradation, and user-visible output.

- [ ] **Step 3: Evaluate promotion to 50 percent**

Promote only when all reference cases pass, no irrelevant source is accepted, required-agent failures are within the approved threshold, groundedness does not regress, and published latency/token gates pass. Otherwise keep 10 percent and record the failed gate.

- [ ] **Step 4: Evaluate promotion to 100 percent**

After a measured 50 percent observation window, repeat the gate. Preserve `audit` and `off` rollback modes until stable production history exists.

- [ ] **Step 5: Verify production artifacts and security**

Generate and download one branded PDF and concept image, reload and recover them from the artifact registry, verify connector secret persistence across revision replacement, verify an unauthorized object request returns 403/404 as appropriate, and confirm no secret/raw actor data appears in logs or traces.

---

### Task 5: GitHub Publication And Handoff

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Create: `docs/p2-acceptance-report.md`

- [ ] **Step 1: Update documentation from observed evidence**

Document actual deployed build/revisions, MAF percentage, measured latency/token results, connected/partial/not-configured Azure states, connector persistence mode, supported capability packs, and remaining limitations.

- [ ] **Step 2: Push and create/update one P2 PR**

PR body contains A/B/C evidence, test/build commands, ACR digests, preview/production run IDs, screenshots, security checks, and truthful Foundry ROI state. Retarget to `main` after PR #15 merges.

- [ ] **Step 3: Final clean verification**

Confirm local HEAD equals remote branch head, worktree is clean, PR is open and non-draft when all gates pass, and production build ID equals the committed SHA.

- [ ] **Step 4: Commit**

```powershell
git add README.md README.zh-CN.md docs/p2-acceptance-report.md
git commit -m "docs: publish DataForge P2 acceptance evidence"
```

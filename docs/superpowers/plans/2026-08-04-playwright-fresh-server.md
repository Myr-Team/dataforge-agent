# Playwright Fresh Server Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Playwright from silently reusing a stale preview server before PR #23 is merged.

**Architecture:** Keep the existing configurable port and loopback proxy bypass. Change preview reuse from unconditional to explicit opt-in, protect it with a source contract test, and ignore only Playwright's generated result directory.

**Tech Stack:** Node test runner, Playwright, Vite, GitHub pull requests.

## Global Constraints

- No backend, authentication or API changes.
- No production deployment or traffic switch.
- Do not ignore workspace fixture directories broadly.

---

### Task 1: Protect fresh-server behavior

**Files:**
- Modify: `web/src/playwrightConfig.test.mjs`
- Modify: `web/playwright.config.mjs`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `DF_PLAYWRIGHT_PORT`, `DF_PLAYWRIGHT_REUSE_SERVER`, `NO_PROXY`, `no_proxy`.
- Produces: Playwright configuration that starts a fresh server by default.

- [ ] Add a Node assertion requiring explicit `DF_PLAYWRIGHT_REUSE_SERVER` opt-in.
- [ ] Run `node --test src/playwrightConfig.test.mjs` and confirm it fails because reuse is currently unconditional.
- [ ] Set `reuseExistingServer` from `DF_PLAYWRIGHT_REUSE_SERVER === "1"`.
- [ ] Add `web/test-results/` to `.gitignore`.
- [ ] Re-run the focused Node test and confirm it passes.

### Task 2: Verify and merge

**Files:**
- Verify only; do not add generated outputs.

**Interfaces:**
- Consumes: updated PR #23 branch.
- Produces: verified and merged PR #23, with production unchanged.

- [ ] Run `python -m pytest -q`.
- [ ] Run `node --test` and `npm run build` in `web/`.
- [ ] Run Playwright on a unique `DF_PLAYWRIGHT_PORT` and confirm all tests pass.
- [ ] Run `git diff --check` and inspect staged files.
- [ ] Commit and push the focused fix to PR #23.
- [ ] Confirm GitHub reports the PR mergeable, then merge it into `main`.
- [ ] Confirm the merged commit exists on `team/main`; do not deploy.

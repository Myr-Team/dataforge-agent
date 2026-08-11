# FinOps Trend Zero-Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing FinOps trend bars start at the true `$0` baseline and present dates and conditional horizontal scrolling in one coherent coordinate system.

**Architecture:** Keep the existing React/CSS chart and view-model calculations. Replace independent absolute offsets with a shared two-row chart body, then verify pixel geometry and tooltip behavior in Playwright.

**Tech Stack:** React, CSS Grid/Flexbox, Node test runner, Vite, Playwright.

## Global Constraints

- Modify only frontend chart presentation and its tests.
- Do not change backend APIs, data structures, field logic, filters, statistics, or metric formulas.
- Do not add a chart library.
- Preserve the white theme, orange cost bars, cache colors, and all existing tooltip fields.
- Use TDD: observe each new regression assertion fail before changing production code.

---

### Task 1: Unify the chart coordinate system

**Files:**
- Modify: `web/src/FinOpsPortal.jsx:440-590`
- Modify: `web/src/styles.css:5446-5495`
- Test: `web/src/finopsLayout.test.mjs`

**Interfaces:**
- Consumes: `niceFinOpsAxis(values, 5)` and `finopsBarPercent(value, axis.max)` unchanged.
- Produces: `.finops-trend-body`, `.finops-trend-axis-track`, `.finops-trend-viewport`, and a shared `--trend-point-count` layout property.

- [ ] **Step 1: Replace the old layout assertion with failing semantic assertions**

  Require a shared chart body/viewport, a dedicated axis plot track, no `translateY` on trend hover, and a common baseline outside individual columns. Keep assertions for orange cost and green cache colors.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `node --test src/finopsLayout.test.mjs`

  Expected: FAIL because the shared body/viewport classes are absent and hover still translates vertically.

- [ ] **Step 3: Implement the shared React structure**

  Render the Y-axis and plot viewport under one `.finops-trend-body`. Pass the point count through an inline custom property:

  ```jsx
  <div className="finops-trend-body">
    <div className="finops-trend-scale" aria-hidden="true">
      <div className="finops-trend-axis-track">{ticks}</div>
      <span className="finops-trend-axis-label-spacer" />
    </div>
    <div className="finops-trend-viewport">
      <div className="finops-trend-gridlines" aria-hidden="true">{lines}</div>
      <div className="finops-trend-columns" style={{ "--trend-point-count": visibleRows.length }}>
        {columns}
      </div>
    </div>
  </div>
  ```

  Derive `visibleRows` once from the same rows already used by the chart. Do not alter row values or tooltip inputs.

- [ ] **Step 4: Implement the shared CSS tracks**

  Use the same plot and label row sizes for scale and columns. Put the common baseline at the bottom of the plot track, move gridlines into that track, and remove the per-column baseline. Use a content minimum width based on `--trend-point-count`; overflow remains `auto`, so no scrollbar appears without actual overflow.

- [ ] **Step 5: Fix hover/focus geometry and styling**

  Replace `translateY(-2px) scaleX(1.08)` with horizontal-only emphasis. Add a subtle column-centered vertical guide limited to the plot track. Narrow the bar from the current 34px maximum and increase spacing without changing values.

- [ ] **Step 6: Run focused Node tests and verify GREEN**

  Run: `node --test src/finopsLayout.test.mjs src/finopsViewModel.test.mjs`

  Expected: all tests pass.

- [ ] **Step 7: Commit**

  ```powershell
  git add web/src/FinOpsPortal.jsx web/src/styles.css web/src/finopsLayout.test.mjs
  git commit -m "fix(finops): align trend bars to zero baseline"
  ```

### Task 2: Verify geometry, conditional overflow, and tooltips

**Files:**
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Reuse: `web/tests/finopsMockApi.mjs`

**Interfaces:**
- Consumes: the chart DOM/classes produced by Task 1 and existing FinOps mock payloads.
- Produces: browser acceptance evidence for baseline alignment, non-lifting hover, responsive overflow, and tooltip retention.

- [ ] **Step 1: Add a failing browser geometry test**

  For a seven-point desktop series, assert:

  ```js
  expect(Math.abs(barBox.y + barBox.height - baselineY)).toBeLessThanOrEqual(1);
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth + 1);
  ```

  Record the bar bottom before and after hover/focus and require it to remain within one pixel.

- [ ] **Step 2: Verify RED against the pre-fix chart**

  Run the focused Playwright spec with an isolated port. Expected: the old coordinate/hover behavior fails at least one new geometry assertion.

- [ ] **Step 3: Add long-range and tooltip acceptance**

  Use a local request fixture with enough daily points to exceed the viewport and assert `scrollWidth > clientWidth`. Focus one column and assert the tooltip still contains date, estimated cost, cache hit, cache miss, bypass, avoided tokens, and estimated savings.

- [ ] **Step 4: Run focused Playwright desktop and mobile tests**

  Run: `$env:DF_PLAYWRIGHT_PORT='5217'; npx playwright test tests/finops-operations-management.spec.mjs`

  Expected: all focused tests pass with a fresh preview.

- [ ] **Step 5: Run the frontend regression gate**

  Run: `node --test`

  Run: `npm run build`

  Run: `git diff --check`

  Expected: zero test failures, successful Vite build, and no whitespace errors.

- [ ] **Step 6: Commit**

  ```powershell
  git add web/tests/finops-operations-management.spec.mjs web/tests/finopsMockApi.mjs
  git commit -m "test(finops): verify zero-baseline trend geometry"
  ```

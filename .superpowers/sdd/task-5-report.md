## Task 5 Report

### Scope

- `web/src/MonitorPage.jsx`
- `web/src/monitorDashboardViewModel.js`
- `web/src/monitorDashboardViewModel.test.mjs`
- `web/src/constants.test.mjs`
- `web/src/api.js`
- `web/src/constants.js`
- `web/src/App.jsx`
- `web/src/components.jsx`
- `web/src/styles.css`

### Red phase

1. `node --test web/src/constants.test.mjs`
   - Failed because `constants.js` did not export `normalizePrimaryView`.
   - Error: `SyntaxError: The requested module './constants.js' does not provide an export named 'normalizePrimaryView'`
2. `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Failed because monitor member rows surfaced `actor_id`.
   - Assertion: expected `成员`, actual `raw-owner-id`.

### Implementation

- Added `normalizePrimaryView(view)` so legacy `governance` resolves to the single owner-only top-level `monitor` concept.
- Tightened `monitorDashboardViewModel` into a safe pure projection:
  - allowlisted state mapping for cost, ROI, optimization status
  - removed raw payload passthrough
  - stopped falling back to `actor_id` in member labels
  - added safe scope label projection for toolbar/meta display
- Rebuilt `MonitorPage` as a fixed-geometry BI surface with:
  - owner guard using server-backed workspace access
  - request cancellation plus request-id guard
  - stable loading/error/empty frames
  - data-layer-only opacity/transform transitions during refresh
- Updated `App.jsx` and `components.jsx` so legacy governance view selection normalizes into `monitor`.
- Refined monitor CSS so loading never shifts card/chart geometry and ranking bars use a restrained solid fill.

### Green phase

1. `node --test web/src/constants.test.mjs`
   - Pass: 3/3
2. `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Pass: 3/3
3. `node --test web/src/monitorDashboardViewModel.test.mjs web/src/constants.test.mjs web/src/governanceViewModel.test.mjs web/src/navigationContract.test.mjs`
   - Pass: 27/27
4. `npm --prefix web run build`
   - Pass: Vite build exited 0
   - Output bundles included `dist/assets/MonitorPage-4kR8HQHs.js` and updated `dist/assets/index-D4vgeuII.css`

### Changed files

- `web/src/MonitorPage.jsx`
- `web/src/monitorDashboardViewModel.js`
- `web/src/monitorDashboardViewModel.test.mjs`
- `web/src/constants.test.mjs`
- `web/src/constants.js`
- `web/src/App.jsx`
- `web/src/components.jsx`
- `web/src/styles.css`

### Residual risks

- This task did not add browser-level interaction tests for the monitor toolbar; candidate browser smoke remains in Task 6.
- The worktree already contained unrelated unstaged changes (`web/src/navigationContract.test.mjs` and generated `workspaces/*`), which were intentionally left out of this task.

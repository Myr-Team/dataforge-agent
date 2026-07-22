## Task 5 Report

### Scope

- `.superpowers/sdd/task-5-report.md`
- `web/src/App.jsx`
- `web/src/MonitorPage.jsx`
- `web/src/constants.js`
- `web/src/constants.test.mjs`
- `web/src/monitorDashboardViewModel.js`
- `web/src/monitorDashboardViewModel.test.mjs`
- `web/src/navigationContract.test.mjs`

### Red phase

1. `node --test web/src/constants.test.mjs`
   - Failed because `constants.js` did not export `resolvePrimaryView`.
   - Error: `SyntaxError: The requested module './constants.js' does not provide an export named 'resolvePrimaryView'`
2. `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Failed because member display did not carry a stable zero-token label.
   - Assertion: expected `'0'`, actual `undefined`.

### Implementation

- Added `resolvePrimaryView(view, access)` so legacy `governance` and direct `monitor` deep links render as `workspaces` until owner access is positively confirmed.
- Updated `App.jsx` to render navigation and `WorkbenchMain` from the resolved safe view, while still normalizing persisted legacy values.
- Extended `monitorDashboardViewModel` to emit `memberRows[].totalTokensLabel`, preserving explicit zero totals as `"0"` instead of collapsing to `未记录`.
- Updated `MonitorPage` to render the member token label directly from the view model.
- Included the already-corrected `web/src/navigationContract.test.mjs` rename so the committed tree, not only the dirty worktree, reflects the owner-only `monitor` nav contract.

### Green phase

1. `node --test web/src/constants.test.mjs`
   - Pass: 4/4
2. `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Pass: 4/4
3. `node --test web/src/constants.test.mjs web/src/monitorDashboardViewModel.test.mjs web/src/governanceViewModel.test.mjs web/src/navigationContract.test.mjs`
   - Pass: 29/29
4. `npm --prefix web run build`
   - Pass: Vite build exited 0
   - Output bundles included `dist/assets/MonitorPage-BbONBzzl.js` and updated `dist/assets/index-BZjjyjnF.js`

### Changed files

- `.superpowers/sdd/task-5-report.md`
- `web/src/App.jsx`
- `web/src/MonitorPage.jsx`
- `web/src/constants.js`
- `web/src/constants.test.mjs`
- `web/src/monitorDashboardViewModel.js`
- `web/src/monitorDashboardViewModel.test.mjs`
- `web/src/navigationContract.test.mjs`

### Residual risks

- This task still relies on Task 6 browser smoke to validate that owner access resolves before the monitor view is ever chosen in a real signed-in session.
- The worktree still contains unrelated generated `workspaces/*` directories, which were intentionally left untracked.

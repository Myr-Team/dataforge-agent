# P2-A Task 5 Report: Global Task Center Frontend

## RED / GREEN

- RED: `node --test src/taskCenter.test.mjs` failed 4/4 because `TaskCenter.jsx` did not exist.
- GREEN: the same command passed 4/4 after adding the task view model.

## Changes

- Added server task list/detail/cancel/retry API clients.
- Added the global task-center drawer, terminal notifications, retry/cancel pending and error states, result destinations, and keyboard-focusable icon controls.
- Replaced the top-bar local task notification surface with a global task-center entry point.
- Loaded task truth from the server on workspace changes; polling is limited to server `queued`/`running` tasks, pauses while hidden, and refreshes immediately when visible.
- Persisted only dismissed notification identities in local storage. Notification identity combines server task id, terminal status, and server timestamp.
- Added the data-workbench task projection, which opens the corresponding global task center instead of inventing local progress.

## Verification

- `node --test src/taskCenter.test.mjs`: 4 passing.
- `node --test src/*.test.mjs`: 14 passing.
- `npm run build`: Vite production build passed.
- `git diff --check`: clean before commit.
- Browser acceptance: desktop task-center entry and drawer opened; reload retained the entry and drawer; mobile navigation covered all six pages with no horizontal overflow; console error log was empty.

## Self-check

- Terminal tasks do not offer cancellation.
- Partial results remain openable and use warning treatment.
- Cancel/retry controls expose pending and error state.
- Drawer is top-bar global and survives navigation across all six views.

## Browser Acceptance Preparation

- Local Vite server: `http://127.0.0.1:5173`.
- Use an upload, connector import, or artifact generation to observe a durable server task reach a terminal state; reload and reopen the task center to verify recovery.

## Concerns

- The existing streamed analysis and iteration endpoints do not currently emit or return a durable server task id. The frontend deliberately does not fabricate a task for them; full task-center projection for those two surfaces requires the backend contract to create and return a task record.

## Rejection Fix Iteration

### RED / GREEN

- RED: queued and running cancellation returned `cancel_requested`; retry exposed an accepted generic copy without a worker dispatcher; an upstream stream cancellation was converted to a normal SSE completion and then marked `failed`.
- GREEN: cancellation is immediately terminal `cancelled`; artifact retries create a new durable linked task/job and schedule the existing artifact worker; connector and other unsupported actions return stable `409 Task retry is not supported`.
- RED: workspace task responses could race across workspace changes and terminal notification handling had no isolated, testable guard.
- GREEN: task loads use an abort controller, monotonic request sequence, current-workspace guard, workspace-cleared snapshots/actions/toasts, and notification dedupe based on server task id/status/timestamp.

### Changes

- `POST /api/chat` now creates and claims a durable `analysis.run` or structured-input `analysis.iterate` task, returns `X-DataForge-Task-Id`, and preserves existing SSE payloads.
- The stream wrapper records safe `run_id`/`version_id` result links, and transitions success, error, incomplete stream, and cancellation outcomes to terminal task state.
- Generic task records now publish `retryable`; only retryable artifact tasks have a real retry dispatcher. Retry preserves `retry_of` and attempt linkage.
- Task-center retry buttons require server `retryable: true`; cancelled tasks use a cancellation icon/state and never expose cancel controls.
- Drawer keyboard handling traps Tab, closes on Escape, restores trigger focus, and marks the application background inert/aria-hidden while open.
- Artifact recovery only observes server jobs and refreshes their linked server tasks; it creates no local task state.

### Verification

- Focused backend tests: task cancellation, analysis/iteration task header/result contract, stream cancellation, artifact retry scheduling, unsupported retry rejection: 6 passing.
- Focused task/artifact/data-workbench suites: 54 passing.
- Task-center node tests: 7 passing.
- Production build: `npm run build` passed.
- Full backend suite and final diff check are required immediately before the fix commit.

### Self-check

- Server task records remain the only task state source; task local storage contains only dismissed terminal notification IDs.
- All task fetches and task action completion handlers are workspace-guarded; stale task results cannot update task snapshots, toasts, actions, or result destinations.
- Polling is restricted to queued/running server tasks and pauses while the tab is hidden; visibility triggers an immediate refresh.
- Partial/failed artifact task results retain their safe destination links; retry is hidden where no dispatcher exists.

### Browser Acceptance Preparation

- Main-controller retest: open the top-bar task center in each of the six views; verify Escape, focus return, Tab containment, and no background tab stops.
- Start analysis and structured iteration; verify the task appears immediately from the response header and opens its run result after terminal completion.
- Switch workspaces while a task request/action is in flight; verify stale task rows, toast, and result action do not appear in the new workspace.
- On desktop and mobile widths, confirm no horizontal overflow and cancelled tasks render cancellation rather than success.

### Concerns

- Browser evidence is intentionally left for the main controller's requested retest; this iteration supplies the deterministic tests and acceptance checklist above.

## Second Review Fix Iteration

### RED / GREEN

- RED: cancelling a running task wrote terminal `cancelled` immediately; artifact and stream workers could still accept later success output; the drawer effect restarted on every new `onClose` callback; and a superseded task refresh could be reported as an action failure.
- GREEN: queued tasks still cancel immediately, while running tasks persist `cancel_requested` and a server timestamp without changing their `running` status. Worker boundaries confirm cancellation before writing terminal `cancelled` and discard returned artifact/stream results.

### Changes

- Added durable `cancel_requested` and `cancel_requested_at` task fields. A success/partial/failure update after the flag is set is coerced to `cancelled` without persisting the later result.
- Artifact jobs check cancellation before producer work and immediately after any uninterruptible producer call; cancelled jobs clear uncommitted output and do not enter result processing.
- The chat/iteration SSE wrapper checks persisted cancellation between frames, stops forwarding subsequent frames, and records only the safe run link already observed before cancellation.
- The task-center presents a running cancellation as `Stopping`, hides its cancel action, and continues normal running-task polling.
- Task actions clear pending state after a successful POST, then refresh independently. `AbortError` from a superseded refresh is silent; real POST errors remain visible.
- Extracted an executable focus controller used by the drawer. It holds the latest close callback by ref, installs listeners once per open state, traps Tab, handles Escape, restores trigger focus, and restores inert background state on close.

### Verification

- Focused cancellation/action/drawer tests: backend 3 passing; task-center node tests 9 passing.
- Focused backend task, artifact, stream bridge, API, and UI-contract suites: 70 passing.
- Full suite, all web node tests, production build, and diff check are rerun before this iteration commit.

### Self-check

- Running task cancellation is not represented as terminal until its worker observes the persisted flag.
- Queued cancellation remains immediately terminal.
- Cancelled tasks cannot be overwritten by late success and no cancelled artifact output is committed after the producer boundary.
- Drawer focus does not reset during stream-driven rerenders because its effect depends only on `open`.

### Concerns

- A single external producer call cannot be forcibly interrupted by this process; the worker remains running until that call returns, then discards its output and stops subsequent processing.

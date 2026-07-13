import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function loadTaskViewModel() {
  const source = await readFile(new URL("./TaskCenter.jsx", import.meta.url), "utf8");
  const start = source.indexOf("const TERMINAL_STATUSES");
  const end = source.indexOf("export function TaskCenter", start);
  assert.ok(start >= 0 && end > start, "TaskCenter must export taskViewModel before the component");
  const moduleSource = source.slice(start, end);
  return import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`);
}

test("rejects stale workspace task responses and dedupes server task notifications", async () => {
  const { isCurrentWorkspaceTaskResponse, terminalTaskNotifications } = await loadTaskViewModel();
  assert.equal(isCurrentWorkspaceTaskResponse({ requestSequence: 2, currentSequence: 2, requestWorkspaceId: "ws-b", currentWorkspaceId: "ws-b", aborted: false }), true);
  assert.equal(isCurrentWorkspaceTaskResponse({ requestSequence: 1, currentSequence: 2, requestWorkspaceId: "ws-a", currentWorkspaceId: "ws-b", aborted: false }), false);
  const task = { task_id: "task-42", status: "completed", updated_at: "2026-07-13T06:00:00Z" };
  assert.deepEqual(terminalTaskNotifications([task], new Map(), true, new Set()), [task]);
  assert.deepEqual(terminalTaskNotifications([task], new Map([["task-42", "task-42:completed:2026-07-13T06:00:00Z"]]), true, new Set()), []);
});

test("implements modal keyboard containment and inert background handling", async () => {
  const source = await readFile(new URL("./TaskCenter.jsx", import.meta.url), "utf8");
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /event\.key !== "Tab"/);
  assert.match(source, /returnFocusRef\.current\?\.focus/);
  assert.match(source, /node\.inert = true/);
  assert.match(source, /aria-hidden/);
});

test("keeps partial task results available after failure", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  const model = taskViewModel({
    status: "partial",
    result: { file_ids: ["f1"] },
    errors: [{ message: "one table failed" }],
  });

  assert.equal(model.canOpenResult, true);
  assert.equal(model.severity, "warning");
});

test("does not offer cancellation for terminal tasks", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  assert.equal(taskViewModel({ status: "completed" }).canCancel, false);
  assert.equal(taskViewModel({ status: "failed" }).canCancel, false);
  assert.equal(taskViewModel({ status: "partial" }).canCancel, false);
});

test("only marks retryable terminal failures as retryable", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  assert.equal(taskViewModel({ status: "failed", retryable: true }).canRetry, true);
  assert.equal(taskViewModel({ status: "failed" }).canRetry, false);
  assert.equal(taskViewModel({ status: "running", retryable: true }).canRetry, false);
});

test("renders cancelled tasks as terminal cancellation rather than success", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  const model = taskViewModel({ status: "cancelled", result: { run_id: "run-1" } });
  assert.equal(model.canCancel, false);
  assert.equal(model.severity, "cancelled");
});

test("uses a server task id and update timestamp for completion notification identity", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  const model = taskViewModel({ task_id: "task-42", status: "completed", updated_at: "2026-07-13T06:00:00Z" });
  assert.equal(model.notificationId, "task-42:completed:2026-07-13T06:00:00Z");
});

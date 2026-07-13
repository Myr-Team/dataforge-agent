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

async function loadTaskActionController() {
  const source = await readFile(new URL("./App.jsx", import.meta.url), "utf8");
  const start = source.indexOf("export async function performServerTaskAction");
  const end = source.indexOf("export function App", start);
  assert.ok(start >= 0 && end > start, "App must expose the task action controller before App");
  return import(`data:text/javascript;base64,${Buffer.from(source.slice(start, end)).toString("base64")}`);
}

test("rejects stale workspace task responses and dedupes server task notifications", async () => {
  const { isCurrentWorkspaceTaskResponse, terminalTaskNotifications } = await loadTaskViewModel();
  assert.equal(isCurrentWorkspaceTaskResponse({ requestSequence: 2, currentSequence: 2, requestWorkspaceId: "ws-b", currentWorkspaceId: "ws-b", aborted: false }), true);
  assert.equal(isCurrentWorkspaceTaskResponse({ requestSequence: 1, currentSequence: 2, requestWorkspaceId: "ws-a", currentWorkspaceId: "ws-b", aborted: false }), false);
  const task = { task_id: "task-42", status: "completed", updated_at: "2026-07-13T06:00:00Z" };
  assert.deepEqual(terminalTaskNotifications([task], new Map(), true, new Set()), [task]);
  assert.deepEqual(terminalTaskNotifications([task], new Map([["task-42", "task-42:completed:2026-07-13T06:00:00Z"]]), true, new Set()), []);
});

test("keeps a cancelling running task in the stopping state", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  const model = taskViewModel({ status: "running", cancel_requested: true, cancel_requested_at: "2026-07-13T06:00:00Z" });
  assert.equal(model.canCancel, false);
  assert.equal(model.statusLabel, "正在停止");
});

test("keeps drawer focus behavior stable across callback updates", async () => {
  const { createTaskCenterFocusController } = await loadTaskViewModel();
  const listeners = new Map();
  const documentRef = {
    activeElement: null,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type) { listeners.delete(type); },
  };
  const focusable = (name) => ({ name, focusCount: 0, focus() { this.focusCount += 1; documentRef.activeElement = this; } });
  const trigger = focusable("trigger");
  const closeButton = focusable("close");
  const retryButton = focusable("retry");
  documentRef.activeElement = trigger;
  const background = {
    inert: false,
    attrs: new Map(),
    getAttribute(name) { return this.attrs.get(name) ?? null; },
    setAttribute(name, value) { this.attrs.set(name, value); },
    removeAttribute(name) { this.attrs.delete(name); },
  };
  const drawer = { querySelectorAll: () => [closeButton, retryButton] };
  let closeCalls = 0;
  const closeRef = { current: () => { closeCalls += 1; } };
  const controller = createTaskCenterFocusController({
    documentRef,
    getDrawer: () => drawer,
    getCloseButton: () => closeButton,
    getBackground: () => [background],
    onCloseRef: closeRef,
  });

  const cleanup = controller.open();
  assert.equal(closeButton.focusCount, 1);
  const tab = { key: "Tab", shiftKey: false, prevented: false, preventDefault() { this.prevented = true; } };
  listeners.get("keydown")(tab);
  assert.equal(tab.prevented, true);
  assert.equal(documentRef.activeElement, retryButton);
  closeRef.current = () => { closeCalls += 10; };
  listeners.get("keydown")({ key: "Escape", preventDefault() {} });
  assert.equal(closeCalls, 10);
  cleanup();
  assert.equal(trigger.focusCount, 1);
  assert.equal(background.inert, false);
});

test("does not turn a superseded refresh into a task action error", async () => {
  const { performServerTaskAction } = await loadTaskActionController();
  const states = [];
  let rejectFirstRefresh;
  const firstAction = performServerTaskAction({
    task: { task_id: "task-1", workspace_id: "ws-1" },
    workspaceId: "ws-1",
    currentWorkspaceId: () => "ws-1",
    postAction: async () => ({ status: "running" }),
    refreshTasks: () => new Promise((_, reject) => { rejectFirstRefresh = reject; }),
    setActionState: (value) => states.push(value),
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const abort = new Error("superseded by a newer refresh");
  abort.name = "AbortError";
  rejectFirstRefresh(abort);
  await firstAction;
  assert.deepEqual(states.at(-1), { pending: false, error: "" });

  const postStates = [];
  await performServerTaskAction({
    task: { task_id: "task-2", workspace_id: "ws-1" },
    workspaceId: "ws-1",
    currentWorkspaceId: () => "ws-1",
    postAction: async () => { throw new Error("permission denied"); },
    refreshTasks: async () => {},
    setActionState: (value) => postStates.push(value),
  });
  assert.deepEqual(postStates.at(-1), { pending: false, error: "permission denied" });
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

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function loadTaskViewModel() {
  const source = await readFile(new URL("./TaskCenter.jsx", import.meta.url), "utf8");
  const start = source.indexOf("export function taskViewModel");
  const end = source.indexOf("export function TaskCenter", start);
  assert.ok(start >= 0 && end > start, "TaskCenter must export taskViewModel before the component");
  const moduleSource = source.slice(start, end);
  return import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`);
}

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
  assert.equal(taskViewModel({ status: "running", retryable: true }).canRetry, false);
});

test("uses a server task id and update timestamp for completion notification identity", async () => {
  const { taskViewModel } = await loadTaskViewModel();
  const model = taskViewModel({ task_id: "task-42", status: "completed", updated_at: "2026-07-13T06:00:00Z" });
  assert.equal(model.notificationId, "task-42:completed:2026-07-13T06:00:00Z");
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  connectorActionState,
  commitGuardedConnectorAction,
  createConnectorActionController,
  connectorRecordsForWorkspaceResponse,
  connectorViewModel,
  createConnectorListController,
  isCurrentConnectorListResponse,
  replaceConnectorRecord,
} from "./connectorViewModel.js";

test("accepts only the current workspace connector response and clears stale records", () => {
  assert.equal(isCurrentConnectorListResponse({ requestSequence: 2, currentSequence: 2, requestWorkspaceId: "ws-b", currentWorkspaceId: "ws-b" }), true);
  assert.equal(isCurrentConnectorListResponse({ requestSequence: 1, currentSequence: 2, requestWorkspaceId: "ws-a", currentWorkspaceId: "ws-b" }), false);
  assert.deepEqual(connectorRecordsForWorkspaceResponse({ connectors: [{ connector_id: "sql-b", kind: "sql" }] }, "ws-b"), [{ connector_id: "sql-b", kind: "sql" }]);
  assert.deepEqual(connectorRecordsForWorkspaceResponse({ workspace_id: "ws-a", connectors: [{ connector_id: "sql-a" }] }, "ws-b"), []);
});

test("derives kind selections and isolated per-connector operation state from server records", () => {
  const records = [
    { connector_id: "sql-1", kind: "sql", status: "connected", persistence: "key_vault" },
    { connector_id: "sql-2", kind: "sql", status: "error", persistence: "session_only" },
    { connector_id: "blob-1", kind: "blob", status: "expired", persistence: "session_only" },
  ];
  const model = connectorViewModel(records, "sql-2", {
    "sql-1": { pending: "sync" },
    "blob-1": { error: "connector_secret_expired" },
  });

  assert.equal(model.selected.connector_id, "sql-2");
  assert.equal(model.selectedByKind.sql.connector_id, "sql-2");
  assert.equal(model.selectedByKind.blob.connector_id, "blob-1");
  assert.equal(model.cards.find((card) => card.connector.connector_id === "sql-1").pending, "sync");
  assert.equal(model.cards.find((card) => card.connector.connector_id === "blob-1").error, "connector_secret_expired");
  assert.deepEqual(connectorActionState(model, "sql-2"), { pending: "", error: "" });
});

test("updates only the target connector and never preserves credentials in UI records", () => {
  const existing = [
    { connector_id: "sql-1", kind: "sql", status: "connected", metadata: { database: "sales" } },
    { connector_id: "blob-1", kind: "blob", status: "disconnected" },
  ];
  const next = replaceConnectorRecord(existing, {
    connector_id: "sql-1",
    kind: "sql",
    status: "syncing",
    metadata: { database: "sales" },
    password: "never-render",
    connection_string: "never-render",
  });

  assert.equal(next[0].status, "syncing");
  assert.equal(next[1].status, "disconnected");
  assert.equal(JSON.stringify(next).includes("never-render"), false);
});

test("rejects a slow workspace A list after switching to workspace B", async () => {
  const state = [];
  let resolveA;
  const controller = createConnectorListController({
    load: (workspaceId) => workspaceId === "ws-a"
      ? new Promise((resolve) => { resolveA = resolve; })
      : Promise.resolve({ workspace_id: "ws-b", connectors: [{ connector_id: "sql-b", kind: "sql" }] }),
    apply: (records, workspaceId) => state.push({ workspaceId, records }),
  });

  const pendingA = controller.refresh("ws-a");
  await controller.refresh("ws-b");
  resolveA({ workspace_id: "ws-a", connectors: [{ connector_id: "sql-a", kind: "sql" }] });
  await pendingA;

  assert.deepEqual(state, [
    { workspaceId: "ws-a", records: [] },
    { workspaceId: "ws-b", records: [] },
    { workspaceId: "ws-b", records: [{ connector_id: "sql-b", kind: "sql" }] },
  ]);
});

test("action guards reject a late workspace response and superseded same-record action", () => {
  let workspaceId = "ws-a";
  const controller = createConnectorActionController({ currentWorkspaceId: () => workspaceId });
  const slowA = controller.begin({ workspaceId, action: "sync", connectorId: "sql-a" });
  workspaceId = "ws-b";
  assert.equal(slowA.isCurrent(), false);

  const first = controller.begin({ workspaceId, action: "sources", connectorId: "blob-b" });
  const second = controller.begin({ workspaceId, action: "sources", connectorId: "blob-b" });
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), true);

  const createSql = controller.begin({ workspaceId, action: "connect", kind: "sql" });
  const createBlob = controller.begin({ workspaceId, action: "connect", kind: "blob" });
  assert.equal(createSql.isCurrent(), true);
  assert.equal(createBlob.isCurrent(), true);
});

test("guarded import commits cannot write workspace B after a slow workspace A response", async () => {
  let workspaceId = "ws-a";
  const controller = createConnectorActionController({ currentWorkspaceId: () => workspaceId });
  const guard = controller.begin({ workspaceId, action: "sync", connectorId: "sql-a" });
  const groups = [];
  let resolve;
  const slowLoad = new Promise((done) => { resolve = done; });
  workspaceId = "ws-b";
  resolve([{ id: "a-file" }]);
  const response = await slowLoad;

  assert.equal(commitGuardedConnectorAction(guard, () => groups.push(...response)), false);
  assert.deepEqual(groups, []);
});

test("finalizing connectors remain syncing-like and do not expose reconnect as an idle action", () => {
  const model = connectorViewModel([{ connector_id: "sql-final", kind: "sql", status: "finalizing" }], "sql-final");
  assert.equal(model.cards[0].pending, "finalizing");
  assert.equal(connectorActionState(model, "sql-final").pending, "finalizing");
});

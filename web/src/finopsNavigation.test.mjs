import assert from "node:assert/strict";
import test from "node:test";

import {
  finopsIntentHandlers,
  finopsPreloadScope,
  scheduleFinOpsPreload,
} from "./finopsNavigation.js";


test("finops navigation intent handlers preload on hover focus and touch", () => {
  let calls = 0;
  const handlers = finopsIntentHandlers(
    { id: "finops" },
    () => {
      calls += 1;
    },
  );

  handlers.onMouseEnter();
  handlers.onFocus();
  handlers.onTouchStart();

  assert.equal(calls, 3);
  assert.deepEqual(finopsIntentHandlers({ id: "runs" }, () => {}), {});
});


test("finops preload scope is unavailable until summary permission resolves", () => {
  const base = {
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "owner@contoso.com" },
  };

  assert.equal(finopsPreloadScope({ ...base, capabilities: null }), null);
  assert.equal(finopsPreloadScope({
    ...base,
    capabilities: {
      sections: {
        finops: {
          visible: false,
          permissions: { "finops.summary.read": false },
        },
      },
    },
  }), null);
});


test("finops preload scope includes identity workspace permissions and default window", () => {
  const scope = finopsPreloadScope({
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "OWNER@CONTOSO.COM" },
    capabilities: {
      sections: {
        finops: {
          visible: true,
          permissions: {
            "finops.summary.read": true,
            "finops.cost.read": true,
            "finops.trace.read": false,
          },
        },
      },
    },
  });

  assert.equal(scope.workspaceId, "ws-a");
  assert.equal(scope.identityKey, "owner@contoso.com");
  assert.deepEqual(scope.permissions, ["finops.cost.read", "finops.summary.read"]);
  assert.deepEqual(scope.filters, { window: "30d" });
  assert.match(scope.key, /ws-a/);
});


test("scheduleFinOpsPreload prefers idle callback and returns cancellation", () => {
  let scheduled;
  let cancelled = null;
  const host = {
    requestIdleCallback(callback) {
      scheduled = callback;
      return 17;
    },
    cancelIdleCallback(handle) {
      cancelled = handle;
    },
  };
  let calls = 0;

  const cancel = scheduleFinOpsPreload(() => {
    calls += 1;
  }, host);
  scheduled();
  cancel();

  assert.equal(calls, 1);
  assert.equal(cancelled, 17);
});

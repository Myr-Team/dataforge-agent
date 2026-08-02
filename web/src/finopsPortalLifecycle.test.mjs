import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";
import React, { act, useState } from "react";

import { clearFinOpsData } from "./finopsDataStore.js";
import { loadFinOpsTab } from "./finopsNavigation.js";
import {
  useFinOpsComparisonLifecycle,
  useFinOpsIdlePreload,
  useFinOpsRefreshLifecycle,
  useFinOpsTabResource,
} from "./finopsPortalLifecycle.js";


const flush = () => new Promise((resolve) => setImmediate(resolve));


async function mounted(element) {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  const previous = Object.fromEntries(
    ["window", "document", "navigator", "HTMLElement", "Node", "Event", "IS_REACT_ACT_ENVIRONMENT"]
      .map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]),
  );
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Node: dom.window.Node,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  const { createRoot } = await import("react-dom/client");
  const container = dom.window.document.getElementById("root");
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
    await flush();
  });
  return {
    container,
    window: dom.window,
    async render(next) {
      await act(async () => {
        root.render(next);
        await flush();
      });
    },
    async cleanup() {
      await act(async () => {
        root.unmount();
        await flush();
      });
      dom.window.close();
      for (const [key, descriptor] of Object.entries(previous)) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else delete globalThis[key];
      }
    },
  };
}


function fakeHost() {
  const intervals = new Map();
  let next = 1;
  return {
    intervals,
    setInterval(callback) {
      const handle = next++;
      intervals.set(handle, callback);
      return handle;
    },
    clearInterval(handle) {
      intervals.delete(handle);
    },
  };
}


function RefreshHarness({ authorizationFingerprint, currentTab, onRefresh, host, documentRef, now }) {
  const lifecycle = useFinOpsRefreshLifecycle({
    authorizationFingerprint,
    queryScopeKey: "query-a",
    currentTab,
    onRefresh,
    host,
    documentRef,
    now,
  });
  const [result, setResult] = useState("");
  const refresh = lifecycle.refreshRequests[currentTab];
  return React.createElement(
    "section",
    null,
    React.createElement("output", { "data-result": true }, result),
    React.createElement("button", { "data-action": "manual", onClick: lifecycle.manualRefresh }, "manual"),
    React.createElement("button", {
      "data-action": "consume",
      onClick: () => setResult(String(lifecycle.consumeForce(currentTab, "main", refresh))),
    }, "consume"),
    React.createElement("button", {
      "data-action": "mark",
      onClick: () => lifecycle.markSuccessful(currentTab, 1_000),
    }, "mark"),
    React.createElement("button", {
      "data-action": "read",
      onClick: () => setResult(String(lifecycle.lastSuccessfulAt(currentTab))),
    }, "read"),
  );
}


async function click(view, action) {
  await act(async () => {
    view.container.querySelector(`[data-action="${action}"]`).dispatchEvent(
      new view.window.MouseEvent("click", { bubbles: true }),
    );
    await flush();
  });
}


test.afterEach(() => {
  clearFinOpsData();
});


test("mounted refresh lifecycle resets authorization state and never consumes an old force in the new scope", async () => {
  const host = fakeHost();
  const calls = [];
  const view = await mounted(React.createElement(RefreshHarness, {
    authorizationFingerprint: "auth-a",
    currentTab: "cost",
    onRefresh: (tab, request) => calls.push([tab, request.force]),
    host,
    now: () => 1_000,
  }));
  try {
    await click(view, "manual");
    await click(view, "consume");
    assert.equal(view.container.querySelector("[data-result]").textContent, "true");
    await click(view, "mark");

    await view.render(React.createElement(RefreshHarness, {
      authorizationFingerprint: "auth-b",
      currentTab: "cost",
      onRefresh: (tab, request) => calls.push([tab, request.force]),
      host,
      now: () => 1_000,
    }));
    await click(view, "consume");
    assert.equal(view.container.querySelector("[data-result]").textContent, "false");

    await view.render(React.createElement(RefreshHarness, {
      authorizationFingerprint: "auth-a",
      currentTab: "cost",
      onRefresh: (tab, request) => calls.push([tab, request.force]),
      host,
      now: () => 1_000,
    }));
    await click(view, "read");
    assert.equal(view.container.querySelector("[data-result]").textContent, "0");
    assert.deepEqual(calls, [["cost", true]]);
  } finally {
    await view.cleanup();
  }
});


test("manual and visibility refresh invoke a real loader only for the mounted current tab", async () => {
  const host = fakeHost();
  let clock = 1_000;
  let hidden = false;
  const calls = { overview: 0, cost: 0, roi: 0, risk: 0 };
  const refreshHints = [];
  const onRefresh = (tab, request) => loadFinOpsTab({
    tab,
    key: `mounted:${tab}`,
    force: request.force,
    now: clock,
    loader: async ({ refresh }) => {
      calls[tab] += 1;
      refreshHints.push(refresh);
      return { tab, at: clock };
    },
  }).promise;
  const view = await mounted(React.createElement(RefreshHarness, {
    authorizationFingerprint: "auth-a",
    currentTab: "cost",
    onRefresh,
    host,
    now: () => clock,
  }));
  try {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    await click(view, "manual");
    await click(view, "mark");
    clock = 601_000;
    hidden = true;
    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
      await flush();
    });
    hidden = false;
    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
      await flush();
    });

    assert.deepEqual(calls, { overview: 0, cost: 2, roi: 0, risk: 0 });
    assert.deepEqual(refreshHints, [true, true]);
  } finally {
    await view.cleanup();
  }
});


function ComparisonHarness({ enabled, cacheKey, loader }) {
  const state = useFinOpsComparisonLifecycle({
    enabled,
    tab: "cost",
    cacheKey,
    domain: "cost:comparison",
    loader,
  });
  return React.createElement("output", {
    "data-loading": String(state.loading),
    "data-value": state.data?.value || "",
    "data-error": state.error || "",
  });
}


test("mounted comparison ignores late success after disable and scope change even when loader ignores abort", async () => {
  const pending = new Map();
  const signals = new Map();
  const loader = (key) => ({ signal }) => {
    signals.set(key, signal);
    return new Promise((resolve) => pending.set(key, resolve));
  };
  const view = await mounted(React.createElement(ComparisonHarness, {
    enabled: true,
    cacheKey: "comparison:a",
    loader: loader("a"),
  }));
  try {
    await view.render(React.createElement(ComparisonHarness, {
      enabled: false,
      cacheKey: "comparison:a",
      loader: loader("disabled"),
    }));
    assert.equal(signals.get("a").aborted, true);
    await act(async () => {
      pending.get("a")({ value: "late-a" });
      await flush();
    });
    assert.equal(view.container.querySelector("output").dataset.value, "");

    await view.render(React.createElement(ComparisonHarness, {
      enabled: true,
      cacheKey: "comparison:b",
      loader: loader("b"),
    }));
    await view.render(React.createElement(ComparisonHarness, {
      enabled: true,
      cacheKey: "comparison:c",
      loader: loader("c"),
    }));
    assert.equal(signals.get("b").aborted, true);
    await act(async () => {
      pending.get("b")({ value: "late-b" });
      await flush();
    });
    assert.notEqual(view.container.querySelector("output").dataset.value, "late-b");
    await act(async () => {
      pending.get("c")({ value: "current-c" });
      await flush();
    });
    assert.equal(view.container.querySelector("output").dataset.value, "current-c");
  } finally {
    await view.cleanup();
  }
});


function IdleOwner({ host, cacheKey, loader }) {
  useFinOpsIdlePreload({
    enabled: true,
    tab: "roi",
    keys: { roi: cacheKey },
    loaders: { roi: loader },
    host,
  });
  return null;
}


function DetailConsumer({ cacheKey, loader }) {
  const [state] = useFinOpsTabResource({
    enabled: true,
    tab: "roi",
    cacheKey,
    loader,
    scopeKey: "scope-a",
  });
  return React.createElement("output", {
    "data-loading": String(state.loading),
    "data-value": state.data?.value || "",
  });
}


function IdleSharingHarness({ showIdle, showDetail, host, cacheKey, loader }) {
  return React.createElement(
    React.Fragment,
    null,
    showIdle ? React.createElement(IdleOwner, { host, cacheKey, loader }) : null,
    showDetail ? React.createElement(DetailConsumer, { cacheKey, loader }) : null,
  );
}


test("idle ROI cleanup keeps shared work alive so the mounted detail loading state converges", async () => {
  let scheduled;
  let cancelled = false;
  const host = {
    requestIdleCallback(callback) {
      scheduled = callback;
      return 7;
    },
    cancelIdleCallback() {
      cancelled = true;
    },
  };
  let signal;
  let resolveRoi;
  let calls = 0;
  const loader = ({ signal: observed }) => {
    calls += 1;
    signal = observed;
    return new Promise((resolve) => { resolveRoi = resolve; });
  };
  const props = { host, cacheKey: "idle:roi", loader };
  const view = await mounted(React.createElement(IdleSharingHarness, {
    ...props,
    showIdle: true,
    showDetail: false,
  }));
  try {
    await act(async () => {
      scheduled();
      await flush();
    });
    await view.render(React.createElement(IdleSharingHarness, {
      ...props,
      showIdle: false,
      showDetail: true,
    }));
    assert.equal(cancelled, false);
    assert.equal(signal.aborted, false);
    assert.equal(view.container.querySelector("output").dataset.loading, "true");
    await act(async () => {
      resolveRoi({ value: "ready" });
      await flush();
    });
    assert.equal(calls, 1);
    assert.equal(view.container.querySelector("output").dataset.loading, "false");
    assert.equal(view.container.querySelector("output").dataset.value, "ready");
  } finally {
    await view.cleanup();
  }
});

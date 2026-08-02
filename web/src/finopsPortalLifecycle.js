import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelFinOpsDataLoad,
  loadFinOpsData,
  readFinOpsData,
} from "./finopsDataStore.js";
import {
  FINOPS_REFRESH_MS,
  createFinOpsRequestGuard,
  createFinOpsRefreshTracker,
  loadFinOpsTab,
  scheduleFinOpsTabPreload,
  settleFinOpsLoadFailure,
} from "./finopsNavigation.js";


const FINOPS_TABS = ["overview", "cost", "roi", "risk"];
const DEFAULT_NOW = () => Date.now();
const NOOP = () => {};
const NEVER_FORCE = () => false;
const EMPTY_REFRESH_REQUEST = Object.freeze({ version: 0, force: false, scopeKey: "" });


function initialRefreshRequests() {
  return Object.fromEntries(FINOPS_TABS.map((tab) => [
    tab,
    { version: 0, force: false, scopeKey: "" },
  ]));
}


function initialResourceState() {
  return {
    dataScopeKey: "",
    tab: "",
    loading: false,
    updating: false,
    error: "",
    data: {},
  };
}


export function useFinOpsRefreshLifecycle({
  authorizationFingerprint = "",
  queryScopeKey = "",
  currentTab = "overview",
  onRefresh = null,
  host = globalThis.window || globalThis,
  documentRef = globalThis.document,
  now = DEFAULT_NOW,
  refreshIntervalMs = FINOPS_REFRESH_MS,
} = {}) {
  const trackerRef = useRef(createFinOpsRefreshTracker());
  const versionsRef = useRef(Object.fromEntries(FINOPS_TABS.map((tab) => [tab, 0])));
  const [refreshRequests, setRefreshRequests] = useState(initialRefreshRequests);
  const refreshScopeKey = useMemo(
    () => JSON.stringify([String(authorizationFingerprint || ""), String(queryScopeKey || "")]),
    [authorizationFingerprint, queryScopeKey],
  );

  useEffect(() => {
    trackerRef.current.reset();
    versionsRef.current = Object.fromEntries(FINOPS_TABS.map((tab) => [tab, 0]));
    setRefreshRequests(initialRefreshRequests());
  }, [authorizationFingerprint]);

  const requestTabRefresh = useCallback((targetTab, { force = true } = {}) => {
    if (!FINOPS_TABS.includes(targetTab)) return null;
    const request = {
      version: (versionsRef.current[targetTab] || 0) + 1,
      force: Boolean(force),
      scopeKey: refreshScopeKey,
    };
    versionsRef.current[targetTab] = request.version;
    setRefreshRequests((current) => ({ ...current, [targetTab]: request }));
    const observed = onRefresh?.(targetTab, request);
    Promise.resolve(observed).catch((error) => {
      if (error?.name !== "AbortError") console.warn("Operations refresh failed", error);
    });
    return request;
  }, [onRefresh, refreshScopeKey]);

  const manualRefresh = useCallback(
    () => requestTabRefresh(currentTab, { force: true }),
    [currentTab, requestTabRefresh],
  );
  const consumeForce = useCallback((tab, resource, refresh) => (
    trackerRef.current.consumeForce(refreshScopeKey, tab, resource, refresh)
  ), [refreshScopeKey]);
  const markSuccessful = useCallback((tab, at = Date.now()) => {
    trackerRef.current.markSuccessful(refreshScopeKey, tab, at);
  }, [refreshScopeKey]);
  const lastSuccessfulAt = useCallback((tab) => (
    trackerRef.current.lastSuccessfulAt(refreshScopeKey, tab)
  ), [refreshScopeKey]);

  useEffect(() => {
    if (!host?.setInterval || !documentRef?.addEventListener) return undefined;
    const run = () => {
      if (trackerRef.current.isDue(
        refreshScopeKey,
        currentTab,
        { hidden: Boolean(documentRef.hidden), now: now() },
      )) {
        requestTabRefresh(currentTab, { force: true });
      }
    };
    const timer = host.setInterval(run, refreshIntervalMs);
    documentRef.addEventListener("visibilitychange", run);
    return () => {
      host.clearInterval?.(timer);
      documentRef.removeEventListener("visibilitychange", run);
    };
  }, [currentTab, documentRef, host, now, refreshIntervalMs, refreshScopeKey, requestTabRefresh]);

  return {
    consumeForce,
    lastSuccessfulAt,
    manualRefresh,
    markSuccessful,
    refreshRequests,
    refreshScopeKey,
    requestTabRefresh,
  };
}


export function useFinOpsTabResource({
  enabled = true,
  tab,
  cacheKey,
  loader,
  scopeKey = "",
  refreshRequest = EMPTY_REFRESH_REQUEST,
  consumeForce = NEVER_FORCE,
  onSuccess = null,
  initialState = null,
} = {}) {
  const [state, setState] = useState(() => initialState || initialResourceState());
  const guardRef = useRef(createFinOpsRequestGuard());

  useEffect(() => {
    if (!enabled || !cacheKey || typeof loader !== "function") {
      guardRef.current.deactivate();
      setState((current) => (
        current.loading || current.updating
          ? { ...current, loading: false, updating: false }
          : current
      ));
      return undefined;
    }
    const guardedRequest = guardRef.current.begin(cacheKey);
    const force = consumeForce(tab, "main", refreshRequest);
    const lifecycle = loadFinOpsTab({ tab, key: cacheKey, loader, force });
    if (lifecycle.cache.value) {
      setState({
        dataScopeKey: scopeKey,
        tab,
        loading: false,
        updating: lifecycle.requested,
        error: lifecycle.cache.lastError ? "上次后台更新未完成，可稍后重试。" : "",
        data: lifecycle.cache.value,
      });
      onSuccess?.(lifecycle.cache.value, {
        source: "cache",
        storedAt: lifecycle.cache.storedAt || 0,
      });
    } else {
      setState({
        dataScopeKey: "",
        tab,
        loading: true,
        updating: false,
        error: "",
        data: {},
      });
    }
    lifecycle.promise.then((data) => {
      if (!guardRef.current.isActive(guardedRequest)) return;
      setState({
        dataScopeKey: scopeKey,
        tab,
        loading: false,
        updating: false,
        error: "",
        data,
      });
      onSuccess?.(data, { source: "network", storedAt: 0 });
    }).catch((error) => {
      if (!guardRef.current.isActive(guardedRequest)) return;
      setState((current) => settleFinOpsLoadFailure({
        ...current,
        tab,
        data: current.tab === tab && current.dataScopeKey === scopeKey
          ? current.data
          : {},
      }, error));
    });
    return () => {
      guardRef.current.deactivate(guardedRequest);
      if (lifecycle.ownsRequest) {
        cancelFinOpsDataLoad((_entry, entryKey) => entryKey === cacheKey);
      }
    };
  }, [cacheKey, consumeForce, enabled, loader, onSuccess, refreshRequest, scopeKey, tab]);

  return [state, setState];
}


export function useFinOpsComparisonLifecycle({
  enabled = false,
  tab = "overview",
  cacheKey = "",
  domain = "overview:comparison",
  loader,
  force = false,
  refreshRequest = EMPTY_REFRESH_REQUEST,
  consumeForce = NEVER_FORCE,
} = {}) {
  const [state, setState] = useState({ loading: false, error: "", data: null });
  const guardRef = useRef(createFinOpsRequestGuard());

  useEffect(() => {
    if (!enabled || !cacheKey || typeof loader !== "function") {
      guardRef.current.deactivate();
      setState({ loading: false, error: "", data: null });
      return undefined;
    }
    const guardedRequest = guardRef.current.begin(cacheKey);
    const shouldForce = Boolean(force || consumeForce(tab, "comparison", refreshRequest));
    const cached = readFinOpsData(cacheKey);
    setState({ loading: !cached.value, error: "", data: cached.value });
    const request = loadFinOpsData(
      cacheKey,
      ({ signal }) => loader({ signal, refresh: shouldForce }),
      { domain, force: shouldForce },
    );
    request.then((data) => {
      if (!guardRef.current.isActive(guardedRequest)) return;
      setState({ loading: false, error: "", data });
    }).catch((error) => {
      if (!guardRef.current.isActive(guardedRequest)) return;
      setState((current) => settleFinOpsLoadFailure(
        current,
        error,
        { fallbackMessage: "对比数据读取失败" },
      ));
    });
    return () => {
      guardRef.current.deactivate(guardedRequest);
      cancelFinOpsDataLoad((_entry, entryKey) => entryKey === cacheKey);
    };
  }, [cacheKey, consumeForce, domain, enabled, force, loader, refreshRequest, tab]);

  return state;
}


export function useFinOpsIdlePreload({
  enabled = false,
  tab = "roi",
  keys = {},
  loaders = {},
  host = globalThis.window || globalThis,
  onError = NOOP,
} = {}) {
  useEffect(() => {
    if (!enabled) return undefined;
    return scheduleFinOpsTabPreload(tab, { keys, loaders, host, onError });
  }, [enabled, host, keys, loaders, onError, tab]);
}

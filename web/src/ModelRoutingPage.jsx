import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Save, SlidersHorizontal } from "lucide-react";

import {
  loadWorkspaceModelPriceCard,
  loadWorkspaceModelRouting,
  updateWorkspaceModelPriceCard,
  updateWorkspaceModelRouting,
} from "./api.js";
import { MODEL_EXECUTION_KINDS, modelRoutingViewModel } from "./modelRoutingViewModel.js";

function emptyPriceDraft(routes = []) {
  return Object.fromEntries(routes.map((route) => [route.id, { input: "", output: "", source: "" }]));
}

function priceDraftFromCard(routes, priceCard) {
  const entries = Array.isArray(priceCard?.entries) ? priceCard.entries : [];
  return Object.fromEntries(routes.map((route) => {
    const entry = entries.find((item) => String(item?.route_id || "") === route.id) || {};
    return [route.id, {
      input: entry.input_per_million ?? "",
      output: entry.output_per_million ?? "",
      source: entry.source_label || "",
    }];
  }));
}

function numberValue(value) {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function routeOptions(routes, capability) {
  return routes.filter((route) => route.capabilities.includes(capability));
}

function assignmentPayload(assignments) {
  const normalized = {};
  for (const kind of MODEL_EXECUTION_KINDS) {
    const value = assignments[kind.id] || {};
    const primary = String(value.primaryRouteId || "").trim();
    const fallback = String(value.fallbackRouteId || "").trim();
    if (!primary) continue;
    normalized[kind.id] = {
      primary_route_id: primary,
      fallback_route_id: fallback || null,
    };
  }
  return { assignments: normalized };
}

function StateMessage({ state, onRetry }) {
  if (state.loading) return <div className="routing-state"><Loader2 className="spin" size={18} /><span>正在读取模型策略</span></div>;
  if (state.error) return <div className="routing-state routing-state-error"><span>{state.error}</span><button className="ghost-button" type="button" onClick={onRetry}>重试</button></div>;
  return null;
}

export function ModelRoutingPage({ workspaceId = "" }) {
  const [state, setState] = useState({ loading: true, error: "", payload: null, priceCard: null });
  const [assignments, setAssignments] = useState({});
  const [currency, setCurrency] = useState("USD");
  const [priceDraft, setPriceDraft] = useState({});
  const [saving, setSaving] = useState("");
  const [saveError, setSaveError] = useState("");

  const load = useCallback(async () => {
    if (!workspaceId) {
      setState({ loading: false, error: "当前未选中工作区。", payload: null, priceCard: null });
      return;
    }
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const [payload, priceCardPayload] = await Promise.all([
        loadWorkspaceModelRouting(workspaceId),
        loadWorkspaceModelPriceCard(workspaceId),
      ]);
      const view = modelRoutingViewModel(payload || {});
      setAssignments(view.assignments);
      setCurrency(String(priceCardPayload?.price_card?.currency || view.priceCard.currency || "USD"));
      setPriceDraft(priceDraftFromCard(view.routes, priceCardPayload?.price_card));
      setState({ loading: false, error: "", payload, priceCard: priceCardPayload?.price_card || null });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : "模型策略读取失败", payload: null, priceCard: null });
    }
  }, [workspaceId]);

  useEffect(() => { load(); }, [load]);

  const view = useMemo(() => modelRoutingViewModel(state.payload || {}), [state.payload]);
  const updateAssignment = (kindId, field, value) => {
    setAssignments((current) => ({
      ...current,
      [kindId]: { ...(current[kindId] || {}), [field]: value },
    }));
  };
  const updatePrice = (routeId, field, value) => {
    setPriceDraft((current) => ({
      ...current,
      [routeId]: { ...(current[routeId] || {}), [field]: value },
    }));
  };
  const saveRouting = async () => {
    setSaving("routing");
    setSaveError("");
    try {
      const payload = await updateWorkspaceModelRouting(workspaceId, assignmentPayload(assignments));
      setState((current) => ({ ...current, payload: { ...(current.payload || {}), ...payload } }));
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "模型策略保存失败");
    } finally {
      setSaving("");
    }
  };
  const savePriceCard = async () => {
    const entries = [];
    for (const route of view.routes) {
      const draft = priceDraft[route.id] || {};
      const hasAny = String(draft.input ?? "").trim() || String(draft.output ?? "").trim() || String(draft.source ?? "").trim();
      if (!hasAny) continue;
      const input = numberValue(draft.input);
      const output = numberValue(draft.output);
      const source = String(draft.source || "").trim();
      if (input === null || output === null || !source) {
        setSaveError(`请完整填写 ${route.label} 的输入、输出和价格依据。`);
        return;
      }
      entries.push({ route_id: route.id, input_per_million: input, output_per_million: output, source_label: source });
    }
    setSaving("price");
    setSaveError("");
    try {
      const payload = await updateWorkspaceModelPriceCard(workspaceId, { currency: String(currency || "USD").trim().toUpperCase(), entries });
      setCurrency(payload?.price_card?.currency || "USD");
      setPriceDraft(priceDraftFromCard(view.routes, payload?.price_card));
      setState((current) => ({ ...current, priceCard: payload?.price_card || null, payload: { ...(current.payload || {}), price_card: { ...(current.payload?.price_card || {}), state: entries.length ? "configured" : "not_configured", revision: payload?.price_card?.revision || 0, currency: payload?.price_card?.currency || "USD", configured_route_ids: entries.map((item) => item.route_id) } } }));
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "价格参考保存失败");
    } finally {
      setSaving("");
    }
  };

  return (
    <main className="agent-studio routing-stage">
      <section className="routing-page" data-testid="model-routing-page">
        <header className="routing-hero">
          <div>
            <span className="routing-eyebrow">模型路由</span>
            <h1>按执行类型分配模型</h1>
            <p>可用模型由服务端允许列表提供。保存后会在下一次调用时生效，并记录到运行溯源；不会接受浏览器传入的部署名。</p>
          </div>
          <button className="icon-button" type="button" title="刷新模型路由" aria-label="刷新模型路由" onClick={load} disabled={state.loading || Boolean(saving)}><RefreshCw className={state.loading ? "spin" : ""} size={17} /></button>
        </header>

        <StateMessage state={state} onRetry={load} />
        {!state.loading && !state.error ? <>
          <section className="routing-summary" aria-label="模型路由状态">
            <div><span>默认路由</span><b>{view.defaultRouteId || "服务端默认"}</b></div>
            <div><span>策略版本</span><b>v{view.policyRevision}</b></div>
            <div><span>估算价格参考</span><b>{view.priceCard.statusLabel}</b></div>
          </section>

          <section className="routing-section">
            <header className="routing-section-head"><div><h2>执行路由</h2><p>主路由不可用时，仅在兼容能力范围内使用备选路由。</p></div><button className="primary-button" type="button" onClick={saveRouting} disabled={Boolean(saving)}>{saving === "routing" ? <Loader2 className="spin" size={16} /> : <Save size={16} />}保存路由</button></header>
            <div className="routing-assignment-table">
              <div className="routing-assignment-row routing-assignment-head"><span>执行类型</span><span>主路由</span><span>备选路由</span></div>
              {MODEL_EXECUTION_KINDS.map((kind) => {
                const eligible = routeOptions(view.routes, kind.capability);
                const current = assignments[kind.id] || {};
                return <div className="routing-assignment-row" key={kind.id}>
                  <div><b>{kind.label}</b><small>{kind.description}</small></div>
                  <select aria-label={`${kind.label}主路由`} value={current.primaryRouteId || ""} onChange={(event) => updateAssignment(kind.id, "primaryRouteId", event.target.value)}><option value="">使用服务端默认</option>{eligible.map((route) => <option value={route.id} key={route.id}>{route.label}</option>)}</select>
                  <select aria-label={`${kind.label}备选路由`} value={current.fallbackRouteId || ""} onChange={(event) => updateAssignment(kind.id, "fallbackRouteId", event.target.value)}><option value="">不设置备选</option>{eligible.map((route) => <option value={route.id} key={route.id}>{route.label}</option>)}</select>
                </div>;
              })}
            </div>
          </section>

          <section className="routing-section">
            <header className="routing-section-head"><div><h2>估算价格参考</h2><p>仅用于运行消耗估算，不等同于 Azure 账单或已验证 ROI。未配置价格的调用将显示为未计价。</p></div><button className="primary-button" type="button" onClick={savePriceCard} disabled={Boolean(saving)}>{saving === "price" ? <Loader2 className="spin" size={16} /> : <SlidersHorizontal size={16} />}保存价格参考</button></header>
            <div className="routing-price-toolbar"><label><span>币种</span><input value={currency} maxLength="3" onChange={(event) => setCurrency(event.target.value.toUpperCase())} aria-label="价格参考币种" /></label><small>每百万 Tokens；价格依据由工作区所有者维护。</small></div>
            <div className="routing-price-table">
              <div className="routing-price-row routing-price-head"><span>模型</span><span>输入</span><span>输出</span><span>价格依据</span></div>
              {view.routes.map((route) => {
                const draft = priceDraft[route.id] || emptyPriceDraft([route])[route.id];
                return <div className="routing-price-row" key={route.id}>
                  <div><b>{route.label}</b><small>{route.capabilities.join(" · ") || "未记录能力"}</small></div>
                  <input type="number" min="0" step="0.000001" value={draft.input} onChange={(event) => updatePrice(route.id, "input", event.target.value)} aria-label={`${route.label} 输入价格`} />
                  <input type="number" min="0" step="0.000001" value={draft.output} onChange={(event) => updatePrice(route.id, "output", event.target.value)} aria-label={`${route.label} 输出价格`} />
                  <input maxLength="160" value={draft.source} onChange={(event) => updatePrice(route.id, "source", event.target.value)} placeholder="价格依据" aria-label={`${route.label} 价格依据`} />
                </div>;
              })}
            </div>
          </section>
          {saveError ? <p className="routing-error" role="alert">{saveError}</p> : null}
        </> : null}
      </section>
    </main>
  );
}

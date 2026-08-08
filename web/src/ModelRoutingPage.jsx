import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleHelp,
  ExternalLink,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  deleteFinOpsOfficialPriceMapping,
  loadFinOpsOfficialPriceCatalog,
  loadFinOpsOfficialPriceMappings,
  loadWorkspaceModelRouting,
  updateFinOpsOfficialPriceMapping,
  updateWorkspaceModelRouting,
} from "./api.js";
import {
  MODEL_AGENT_ROLES,
  MODEL_EXECUTION_KINDS,
  modelRoutingViewModel,
} from "./modelRoutingViewModel.js";

function routeOptions(routes, capability) {
  return routes.filter((route) => route.capabilities.includes(capability));
}

function routeOptionLabel(route) {
  return `${route.label} · ${route.providerLabel || "Azure Foundry"}`;
}

function assignmentPayload(assignments, agentAssignments, defaultRouteId) {
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
  const normalizedAgents = {};
  for (const agent of MODEL_AGENT_ROLES) {
    const value = agentAssignments[agent.id] || {};
    const primary = String(value.primaryRouteId || "").trim();
    const fallback = String(value.fallbackRouteId || "").trim();
    if (!primary) continue;
    normalizedAgents[agent.id] = {
      primary_route_id: primary,
      fallback_route_id: fallback || null,
    };
  }
  return {
    assignments: normalized,
    agent_assignments: normalizedAgents,
    ...(defaultRouteId ? { default_route_id: defaultRouteId } : {}),
  };
}

function mappingByDeployment(items = []) {
  return Object.fromEntries(
    items
      .filter((item) => item?.deployment)
      .map((item) => [String(item.deployment), item]),
  );
}

function officialPriceLabel(item) {
  if (!item) return "未计价";
  return `${item.display_name} · 输入 $${item.input_per_million} / 输出 $${item.output_per_million}`;
}

function StateMessage({ state, onRetry }) {
  if (state.loading) {
    return (
      <div className="routing-state">
        <Loader2 className="spin" size={18} />
        <span>正在读取模型与官方价格配置</span>
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="routing-state routing-state-error">
        <span>{state.error}</span>
        <button className="ghost-button" type="button" onClick={onRetry}>重试</button>
      </div>
    );
  }
  return null;
}


export async function persistModelSetting(write, onSettingsChanged, kind) {
  const result = await write();
  onSettingsChanged?.(kind);
  return result;
}


export function ModelRoutingPage({
  workspaceId = "",
  embedded = false,
  onSettingsChanged = null,
}) {
  const [state, setState] = useState({
    loading: true,
    error: "",
    payload: null,
    catalog: [],
    catalogRevision: "",
    mappings: [],
  });
  const [assignments, setAssignments] = useState({});
  const [agentAssignments, setAgentAssignments] = useState({});
  const [defaultRouteId, setDefaultRouteId] = useState("");
  const [mappingDraft, setMappingDraft] = useState({});
  const [saving, setSaving] = useState("");
  const [notice, setNotice] = useState("");
  const [saveError, setSaveError] = useState("");

  const load = useCallback(async () => {
    if (!workspaceId) {
      setState((current) => ({ ...current, loading: false, error: "当前未选中工作区。" }));
      return;
    }
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const [payload, catalogPayload, mappingPayload] = await Promise.all([
        loadWorkspaceModelRouting(workspaceId),
        loadFinOpsOfficialPriceCatalog(),
        loadFinOpsOfficialPriceMappings(),
      ]);
      const view = modelRoutingViewModel(payload || {});
      const mappings = Array.isArray(mappingPayload?.items) ? mappingPayload.items : [];
      setAssignments(view.assignments);
      setAgentAssignments(view.agentAssignments);
      setDefaultRouteId(String(payload?.policy?.default_route_id || view.defaultRouteId || ""));
      setMappingDraft(Object.fromEntries(
        view.routes.map((route) => [
          route.deployment,
          mappingByDeployment(mappings)[route.deployment]?.official_price_key || "",
        ]),
      ));
      setState({
        loading: false,
        error: "",
        payload,
        catalog: Array.isArray(catalogPayload?.items) ? catalogPayload.items : [],
        catalogRevision: String(catalogPayload?.revision || ""),
        mappings,
      });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "模型配置读取失败",
      }));
    }
  }, [workspaceId]);

  useEffect(() => { load(); }, [load]);

  const view = useMemo(() => modelRoutingViewModel(state.payload || {}), [state.payload]);
  const mappings = useMemo(() => mappingByDeployment(state.mappings), [state.mappings]);
  const mappedCount = view.routes.filter((route) => mappings[route.deployment]).length;
  const analysisRoutes = routeOptions(view.routes, "analysis");
  const chatRoutes = routeOptions(view.routes, "chat");

  const updateAssignment = (kindId, field, value) => {
    setAssignments((current) => ({
      ...current,
      [kindId]: { ...(current[kindId] || {}), [field]: value },
    }));
  };
  const updateAgentAssignment = (agentId, field, value) => {
    setAgentAssignments((current) => ({
      ...current,
      [agentId]: { ...(current[agentId] || {}), [field]: value },
    }));
  };
  const applyAllAgents = (routeId) => {
    setAgentAssignments(Object.fromEntries(MODEL_AGENT_ROLES.map((agent) => [
      agent.id,
      {
        primaryRouteId: routeId,
        fallbackRouteId: agentAssignments[agent.id]?.fallbackRouteId || "",
      },
    ])));
    setNotice("已应用到全部 Agent，保存后从下一次分析开始生效。");
  };

  const saveRouting = async () => {
    setSaving("routing");
    setSaveError("");
    setNotice("");
    try {
      const payload = await persistModelSetting(
        () => updateWorkspaceModelRouting(workspaceId, {
          ...assignmentPayload(assignments, agentAssignments, defaultRouteId),
          base_revision: view.policyRevision,
        }),
        onSettingsChanged,
        "model",
      );
      setState((current) => ({
        ...current,
        payload: { ...(current.payload || {}), ...payload },
      }));
      setNotice("模型分配已保存；新运行会按 Agent 分别记录模型、Token 与估算成本。");
    } catch (error) {
      if (error && error.status === 409) {
        setSaveError("模型分配已被其他管理员更新，已为你载入最新版本，请复核后重新保存。");
        await load();
      } else {
        setSaveError(error instanceof Error ? error.message : "模型分配保存失败");
      }
    } finally {
      setSaving("");
    }
  };

  const saveMapping = async (deployment) => {
    const officialPriceKey = String(mappingDraft[deployment] || "").trim();
    if (!officialPriceKey) {
      setSaveError("请选择一条官方价格记录；无法可靠匹配时请保留“未计价”。");
      return;
    }
    setSaving(`mapping:${deployment}`);
    setSaveError("");
    setNotice("");
    try {
      const current = mappings[deployment];
      const result = await persistModelSetting(
        () => updateFinOpsOfficialPriceMapping(deployment, {
          officialPriceKey,
          baseRevision: Number(current?.mapping_revision || 0),
        }),
        onSettingsChanged,
        "price",
      );
      const saved = result?.mapping;
      setState((value) => ({
        ...value,
        mappings: [
          ...value.mappings.filter((item) => item.deployment !== deployment),
          saved,
        ],
      }));
      setNotice(`${deployment} 已关联官方价格记录，新请求将按该版本估算。`);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "官方价格关联失败");
    } finally {
      setSaving("");
    }
  };

  const removeMapping = async (deployment) => {
    setSaving(`mapping:${deployment}`);
    setSaveError("");
    setNotice("");
    try {
      await persistModelSetting(
        () => deleteFinOpsOfficialPriceMapping(deployment),
        onSettingsChanged,
        "price",
      );
      setState((value) => ({
        ...value,
        mappings: value.mappings.filter((item) => item.deployment !== deployment),
      }));
      setMappingDraft((current) => ({ ...current, [deployment]: "" }));
      setNotice(`${deployment} 已解除官方价格关联并恢复未计价，历史估算版本保持不变。`);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "解除官方价格关联失败");
    } finally {
      setSaving("");
    }
  };

  return (
    <main className={`agent-studio routing-stage ${embedded ? "routing-embedded" : ""}`}>
      <section className="routing-page" data-testid="model-routing-page">
        {!embedded ? (
          <header className="routing-hero">
            <div>
              <span className="routing-eyebrow">模型与成本</span>
              <h1>模型分配</h1>
              <p>为工作区和每个 Agent 指定允许的模型，并把 deployment 关联到后端维护的官方价格目录。</p>
            </div>
            <button className="icon-button" type="button" title="刷新模型配置" aria-label="刷新模型配置" onClick={load} disabled={state.loading || Boolean(saving)}>
              <RefreshCw className={state.loading ? "spin" : ""} size={17} />
            </button>
          </header>
        ) : null}

        <StateMessage state={state} onRetry={load} />
        {!state.loading && !state.error ? (
          <>
            <section className="routing-summary" aria-label="模型配置状态">
              <div><span>工作区默认</span><b>{view.routes.find((route) => route.id === defaultRouteId)?.label || "服务端默认"}</b></div>
              <div><span>策略版本</span><b>v{view.policyRevision}</b></div>
              <div><span>官方价格关联</span><b>{mappedCount} / {view.routes.length} 个模型</b></div>
            </section>

            <section className="routing-section">
              <header className="routing-section-head">
                <div>
                  <h2>工作区默认模型</h2>
                  <p>没有单独配置的调用使用这里的默认模型；只展示服务端允许的路由。</p>
                </div>
              </header>
              <label className="routing-default-field">
                <span>默认模型</span>
                <select value={defaultRouteId} onChange={(event) => setDefaultRouteId(event.target.value)}>
                  <option value="">使用服务端默认</option>
                  {chatRoutes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                </select>
              </label>
            </section>

            <section className="routing-section routing-assistant-models">
              <header className="routing-section-head">
                <div>
                  <h2>运营 AI 模型</h2>
                  <p>快速回答优先低等待；深入分析使用 FinOps Agent。两种模式分别统计模型、Token 与估算成本。</p>
                </div>
              </header>
              <div className="routing-assignment-table">
                <div className="routing-assignment-row routing-assignment-head"><span>模式</span><span>主要模型</span><span>备用模型</span></div>
                {[
                  {
                    id: "quick",
                    label: "快速回答",
                    description: "解释单个指标和当前证据",
                    value: assignments.direct_reply || {},
                    routes: chatRoutes,
                    update: updateAssignment,
                    target: "direct_reply",
                  },
                  {
                    id: "deep",
                    label: "深入分析",
                    description: "综合证据并形成结构化建议",
                    value: agentAssignments["df-finops-analyst"] || {},
                    routes: analysisRoutes,
                    update: updateAgentAssignment,
                    target: "df-finops-analyst",
                  },
                ].map((item) => (
                  <div className="routing-assignment-row" key={item.id}>
                    <div><b>{item.label}</b><small>{item.description}</small></div>
                    <select aria-label={`${item.label}主要模型`} value={item.value.primaryRouteId || ""} onChange={(event) => item.update(item.target, "primaryRouteId", event.target.value)}>
                      <option value="">继承工作区默认</option>
                      {item.routes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                    </select>
                    <select aria-label={`${item.label}备用模型`} value={item.value.fallbackRouteId || ""} onChange={(event) => item.update(item.target, "fallbackRouteId", event.target.value)}>
                      <option value="">不设置备用</option>
                      {item.routes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                    </select>
                  </div>
                ))}
              </div>
            </section>

            <section className="routing-section">
              <header className="routing-section-head">
                <div>
                  <h2>Agent 模型分配</h2>
                  <p>每个 Agent 的实际调用、Token 和成本会按这里的模型分别进入运营统计。</p>
                </div>
                <label className="routing-apply-all">
                  <span>一键应用到全部</span>
                  <select value="" onChange={(event) => event.target.value && applyAllAgents(event.target.value)}>
                    <option value="">选择模型</option>
                    {analysisRoutes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                  </select>
                </label>
              </header>
              <div className="routing-assignment-table">
                <div className="routing-assignment-row routing-assignment-head"><span>Agent</span><span>主要模型</span><span>备用模型</span></div>
                {MODEL_AGENT_ROLES.map((agent) => {
                  const current = agentAssignments[agent.id] || {};
                  return (
                    <div className="routing-assignment-row" key={agent.id}>
                      <div><b>{agent.label}</b><small>{agent.description}</small></div>
                      <select aria-label={`${agent.label}主要模型`} value={current.primaryRouteId || ""} onChange={(event) => updateAgentAssignment(agent.id, "primaryRouteId", event.target.value)}>
                        <option value="">继承工作区默认</option>
                        {analysisRoutes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                      </select>
                      <select aria-label={`${agent.label}备用模型`} value={current.fallbackRouteId || ""} onChange={(event) => updateAgentAssignment(agent.id, "fallbackRouteId", event.target.value)}>
                        <option value="">不设置备用</option>
                        {analysisRoutes.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                      </select>
                    </div>
                  );
                })}
              </div>
            </section>

            <details className="routing-section routing-advanced">
              <summary>按执行类型微调</summary>
              <p>仅在需要覆盖直接回复、会话跟进或复修路径时调整；Agent 分配优先于执行类型。</p>
              <div className="routing-assignment-table">
                <div className="routing-assignment-row routing-assignment-head"><span>执行类型</span><span>主要模型</span><span>备用模型</span></div>
                {MODEL_EXECUTION_KINDS.map((kind) => {
                  const eligible = routeOptions(view.routes, kind.capability);
                  const current = assignments[kind.id] || {};
                  return (
                    <div className="routing-assignment-row" key={kind.id}>
                      <div><b>{kind.label}</b><small>{kind.description}</small></div>
                      <select aria-label={`${kind.label}主要模型`} value={current.primaryRouteId || ""} onChange={(event) => updateAssignment(kind.id, "primaryRouteId", event.target.value)}>
                        <option value="">继承工作区默认</option>
                        {eligible.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                      </select>
                      <select aria-label={`${kind.label}备用模型`} value={current.fallbackRouteId || ""} onChange={(event) => updateAssignment(kind.id, "fallbackRouteId", event.target.value)}>
                        <option value="">不设置备用</option>
                        {eligible.map((route) => <option value={route.id} key={route.id}>{routeOptionLabel(route)}</option>)}
                      </select>
                    </div>
                  );
                })}
              </div>
            </details>

            <section className="routing-section">
              <header className="routing-section-head">
                <div>
                  <h2>官方价格关联 <CircleHelp size={14} aria-label="价格说明" /></h2>
                  <p>价格仅用于请求级预估，不是 Azure 账单。无法可靠匹配时保持“未计价”，不会填入猜测价格。</p>
                </div>
                <small className="routing-catalog-revision">目录 {state.catalogRevision || "未记录"}</small>
              </header>
              <div className="routing-price-table">
                <div className="routing-official-price-row routing-price-head"><span>模型 deployment</span><span>状态</span><span>官方价格记录</span><span aria-label="操作" /></div>
                {view.routes.map((route) => {
                  const mapping = mappings[route.deployment];
                  const selected = state.catalog.find((item) => item.price_key === mappingDraft[route.deployment]);
                  const busy = saving === `mapping:${route.deployment}`;
                  return (
                    <div className="routing-official-price-row" key={route.id}>
                      <div><b>{route.label}</b><small>{route.providerLabel || "Azure Foundry"} · {route.deployment || "deployment 未记录"}</small></div>
                      <span className={`routing-price-status ${mapping ? "mapped" : "unpriced"}`}>{mapping ? "已计价" : "未计价"}</span>
                      <div className="routing-price-picker">
                        <select value={mappingDraft[route.deployment] || ""} onChange={(event) => setMappingDraft((current) => ({ ...current, [route.deployment]: event.target.value }))} aria-label={`${route.label}官方价格记录`}>
                          <option value="">保留未计价</option>
                          {state.catalog.map((item) => <option key={item.price_key} value={item.price_key}>{officialPriceLabel(item)}</option>)}
                        </select>
                        {selected?.source_url ? <a href={selected.source_url} target="_blank" rel="noreferrer" title="查看官方价格来源"><ExternalLink size={13} />官方来源</a> : null}
                      </div>
                      <div className="routing-price-actions">
                        <button className="routing-map-button" type="button" disabled={busy || !mappingDraft[route.deployment]} onClick={() => saveMapping(route.deployment)}>
                          {busy ? <Loader2 className="spin" size={14} /> : <Sparkles size={14} />}
                          关联
                        </button>
                        {mapping ? (
                          <button
                            className="ghost-button routing-unmap-button"
                            type="button"
                            disabled={busy}
                            onClick={() => removeMapping(route.deployment)}
                            aria-label={`解除${route.label}官方价格关联`}
                          >
                            <Trash2 size={14} />
                            解除关联
                          </button>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <footer className="routing-save-bar">
              <div>
                {notice ? <p className="routing-notice">{notice}</p> : null}
                {saveError ? <p className="routing-error" role="alert">{saveError}</p> : null}
              </div>
              <button className="primary-button" type="button" onClick={saveRouting} disabled={Boolean(saving)}>
                {saving === "routing" ? <Loader2 className="spin" size={16} /> : <Save size={16} />}
                保存模型分配
              </button>
            </footer>
          </>
        ) : null}
      </section>
    </main>
  );
}

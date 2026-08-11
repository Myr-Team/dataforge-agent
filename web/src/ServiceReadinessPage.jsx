import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  RefreshCw,
  ServerCog,
} from "lucide-react";

import { loadServiceReadiness } from "./api.js";
import { serviceReadinessView } from "./serviceReadinessViewModel.js";
import { loadSettingsResource } from "./settingsDataStore.js";
import { settingsResourceKey } from "./settingsNavigation.js";


const DETAIL_LABELS = Object.freeze({
  role: "当前角色",
  state: "连接状态",
  latency_ms: "探测延迟",
  elapsed_ms: "响应时间",
  configured: "已配置",
  persistence: "保存方式",
  catalog_entries: "官方价目",
  mapping_count: "价格关联",
  connected: "连接成功",
  governed: "纳入治理",
  rules_evaluated: "检查规则",
  rules_triggered: "需关注",
  evidence_coverage_pct: "证据覆盖",
  rows_observed: "读取记录",
  rows_written: "更新记录",
  age_seconds: "距上次完成",
});


function formatTime(value) {
  if (!value) return "尚未完成";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间待确认";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}


function formatDetail(key, value) {
  if (key === "latency_ms" || key === "elapsed_ms") return `${value} ms`;
  if (key === "evidence_coverage_pct") return `${value}%`;
  if (key === "age_seconds") {
    const seconds = Number(value || 0);
    if (seconds < 60) return `${seconds} 秒`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
    return `${Math.round(seconds / 3600)} 小时`;
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === "owner") return "负责人";
  if (value === "admin") return "管理员";
  if (value === "entra") return "Microsoft Entra";
  if (value === "durable") return "持久保存";
  if (value === "session") return "当前会话";
  return String(value ?? "—");
}


function visibleDetails(item) {
  return Object.entries(item.details || {})
    .filter(([key]) => Object.hasOwn(DETAIL_LABELS, key))
    .slice(0, 3);
}


export function ServiceReadinessPage({ workspaceId = "", settingsScope = null }) {
  const [state, setState] = useState({ loading: true, refreshing: false, error: "", payload: null });
  const load = useCallback(async ({ refresh = false } = {}) => {
    if (!workspaceId) {
      setState({ loading: false, refreshing: false, error: "请先选择工作区", payload: null });
      return;
    }
    setState((current) => ({
      ...current,
      loading: !current.payload,
      refreshing: Boolean(current.payload),
      error: "",
    }));
    try {
      const key = settingsResourceKey(settingsScope, "readiness");
      const payload = key
        ? await loadSettingsResource(
          key,
          ({ signal }) => loadServiceReadiness(workspaceId, { timeoutMs: 20000, refresh, signal }),
          { force: refresh, freshMs: 15_000, staleUsableMs: 60_000 },
        )
        : await loadServiceReadiness(workspaceId, { timeoutMs: 20000, refresh });
      setState({ loading: false, refreshing: false, error: "", payload });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        refreshing: false,
        error: error instanceof Error ? error.message : "服务状态读取失败",
      }));
    }
  }, [settingsScope, workspaceId]);

  useEffect(() => {
    load();
  }, [load]);

  const view = useMemo(() => serviceReadinessView(state.payload), [state.payload]);
  if (state.loading && !state.payload) {
    return (
      <section className="service-readiness-page" aria-label="正在读取服务状态">
        <div className="service-readiness-loading"><RefreshCw className="spin" size={17} />正在核对当前工作区的关键服务</div>
      </section>
    );
  }

  return (
    <section className="service-readiness-page" aria-labelledby="service-readiness-title">
      <header className="service-readiness-header">
        <div>
          <span className="service-readiness-eyebrow"><Activity size={13} />演示就绪检查</span>
          <h2 id="service-readiness-title">关键服务状态</h2>
          <p>按当前工作区核对身份、数据、模型、成本治理与后台任务，只展示安全的运行状态。</p>
        </div>
        <button type="button" onClick={() => load({ refresh: true })} disabled={state.refreshing}>
          <RefreshCw className={state.refreshing ? "spin" : ""} size={14} />
          {state.refreshing ? "检查中" : "重新检查"}
        </button>
      </header>

      {state.error ? (
        <div className="service-readiness-error" role="alert">
          <AlertTriangle size={15} />
          <span><b>状态读取未完成</b><small>{state.error}</small></span>
          <button type="button" onClick={() => load({ refresh: true })}>重试</button>
        </div>
      ) : null}

      {state.payload ? (
        <>
          <div className="service-readiness-summary">
            <article><CheckCircle2 size={18} /><span><small>当前可用</small><strong>{view.summary.ready}</strong></span></article>
            <article className={view.summary.attention ? "attention" : ""}><AlertTriangle size={18} /><span><small>需要关注</small><strong>{view.summary.attention}</strong></span></article>
            <article><ServerCog size={18} /><span><small>检查项目</small><strong>{view.summary.total}</strong></span></article>
            <article><Clock3 size={18} /><span><small>本次检查</small><strong>{formatTime(view.generatedAt)}</strong></span></article>
          </div>

          <div className="service-readiness-groups">
            {view.groups.map((group) => (
              <section key={group.id} className="service-readiness-group">
                <header><h3>{group.label}</h3><small>{group.items.filter((item) => item.ready).length} / {group.items.length} 可用</small></header>
                <div>
                  {group.items.map((item) => (
                    <article key={item.key || item.label} className={`status-${item.status}`}>
                      <span className="service-readiness-state-icon">
                        {item.ready ? <CheckCircle2 size={16} /> : item.status === "running" ? <RefreshCw className="spin" size={16} /> : <AlertTriangle size={16} />}
                      </span>
                      <span className="service-readiness-copy">
                        <b>{item.label}</b>
                        {item.lastCompletedAt ? <small>最近完成 {formatTime(item.lastCompletedAt)}</small> : null}
                      </span>
                      <span className={`service-readiness-badge status-${item.status}`}>{item.statusLabel}</span>
                      {visibleDetails(item).length ? (
                        <dl>
                          {visibleDetails(item).map(([key, value]) => (
                            <div key={key}><dt>{DETAIL_LABELS[key]}</dt><dd>{formatDetail(key, value)}</dd></div>
                          ))}
                        </dl>
                      ) : null}
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

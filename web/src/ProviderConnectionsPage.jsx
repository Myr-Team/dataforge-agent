import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  KeyRound,
  Loader2,
  PlugZap,
  Power,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";

import {
  createModelProvider,
  disableModelProvider,
  loadModelProviders,
  rotateModelProviderSecret,
  testModelProvider,
} from "./api.js";
import { AwsBedrockConnectionForm } from "./AwsBedrockConnectionForm.jsx";
import { providerConnectionsViewModel } from "./providerConnectionsViewModel.js";

const DEEPSEEK_ENDPOINT = "https://api.deepseek.com";

function formatTime(value) {
  if (!value) return "尚未检测";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未检测";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function ProviderState({ item }) {
  const healthy = item.connectionState === "connected";
  return (
    <div className="provider-state-row">
      <span className={`governance-status ${healthy ? "success" : "warning"}`}>
        {healthy ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}
        {item.connectionLabel}
      </span>
      <span className={`governance-status ${item.governanceState === "governed" ? "success" : "neutral"}`}>
        <ShieldCheck size={13} />
        {item.governanceLabel}
      </span>
    </div>
  );
}

function safeConnectionMessage(error) {
  if (error?.status === 409) {
    return "配置已被其他管理员更新，已重新加载最新版本，请复核后再试。";
  }
  return "无法完成连接操作，请检查配置后重试。";
}

export function ProviderConnectionsPage() {
  const [state, setState] = useState({ loading: true, error: "", payload: null });
  const [draft, setDraft] = useState({
    displayName: "DeepSeek 原厂",
    apiKey: "",
  });
  const [rotateDraft, setRotateDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const payload = await loadModelProviders();
      setState({ loading: false, error: "", payload });
    } catch (error) {
      setState({
        loading: false,
        error: "无法读取模型提供商配置，请重试。",
        payload: null,
      });
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const view = useMemo(() => providerConnectionsViewModel(state.payload || {}), [state.payload]);

  const runAction = async (key, action, successMessage) => {
    setBusy(key);
    setActionError("");
    setNotice("");
    try {
      await action();
      setNotice(successMessage);
      await load();
      return true;
    } catch (error) {
      if (error?.status === 409) {
        setActionError(safeConnectionMessage(error));
        await load();
      } else {
        setActionError(safeConnectionMessage(error));
      }
      return false;
    } finally {
      setBusy("");
    }
  };

  const submitProvider = async (event) => {
    event.preventDefault();
    const apiKey = String(draft.apiKey || "").trim();
    if (apiKey.length < 8) {
      setActionError("请输入有效的 DeepSeek API Key。");
      return;
    }
    setBusy("create");
    setActionError("");
    setNotice("");
    try {
      await createModelProvider({
        provider_type: "deepseek",
        display_name: String(draft.displayName || "").trim() || "DeepSeek 原厂",
        base_url: DEEPSEEK_ENDPOINT,
        api_key: apiKey,
      });
      setNotice("DeepSeek 已保存到安全凭据存储，并完成首次连通性检测。");
      await load();
    } catch (error) {
      setActionError(safeConnectionMessage(error));
    } finally {
      setDraft((current) => ({ ...current, apiKey: "" }));
      setBusy("");
    }
  };

  const submitBedrockProvider = (payload) => runAction(
    "create-bedrock",
    () => createModelProvider(payload),
    "AWS Bedrock 凭据已安全保存，配置测试可用。",
  );

  const rotateSecret = async (item) => {
    const apiKey = String(rotateDraft[item.providerId] || "").trim();
    if (apiKey.length < 8) {
      setActionError("请输入新的 DeepSeek API Key。");
      return;
    }
    await runAction(
      `rotate:${item.providerId}`,
      () => rotateModelProviderSecret(item.providerId, apiKey, item.revision),
      `${item.name} 的凭据已更新并重新检测。`,
    );
    setRotateDraft((current) => ({ ...current, [item.providerId]: "" }));
  };

  const rotateBedrockCredentials = (item, payload) => runAction(
    `rotate:${item.providerId}`,
    () => rotateModelProviderSecret(item.providerId, payload, item.revision),
    "AWS Bedrock 凭据已安全更新，配置测试可用。",
  );

  return (
    <section className="provider-connections" data-testid="provider-connections-page">
      <header className="governance-panel-head">
        <div>
          <span className="governance-eyebrow">组织级连接</span>
          <h2>模型提供商</h2>
          <p>在后台保存原厂凭据并检测连通性。只有已连接、已纳管且已计价的模型才会进入 Agent 可选范围。</p>
        </div>
        <button className="icon-button" type="button" onClick={load} disabled={state.loading || Boolean(busy)} aria-label="刷新模型提供商">
          <RefreshCw size={16} className={state.loading ? "spin" : ""} />
        </button>
      </header>

      <div className="governance-summary-strip" aria-label="模型提供商状态">
        <div><span>已接入</span><b>{view.items.length}</b></div>
        <div><span>连接正常</span><b>{view.summary.connected}</b></div>
        <div><span>已纳管</span><b>{view.summary.governed}</b></div>
        <div><span>需要处理</span><b>{view.summary.actionRequired}</b></div>
      </div>

      {state.loading ? (
        <div className="governance-empty"><Loader2 className="spin" size={18} />正在读取提供商配置</div>
      ) : state.error ? (
        <div className="governance-empty error">
          <CircleAlert size={18} />
          <span>{state.error}</span>
          <button type="button" className="ghost-button" onClick={load}>重试</button>
        </div>
      ) : (
        <div className="provider-card-list">
          {view.items.length === 0 ? (
            <div className="governance-empty compact">尚未接入外部模型提供商。下方可直接添加 DeepSeek 原厂接口。</div>
          ) : null}
          {view.items.map((item) => (
            <article className="provider-card" key={item.providerId}>
              <div className="provider-card-main">
                <div className="provider-mark" aria-hidden="true">{item.isBedrock ? "AWS" : "DS"}</div>
                <div>
                  <div className="provider-title-line">
                    <h3>{item.name}</h3>
                    <ProviderState item={item} />
                  </div>
                  {item.isBedrock ? (
                    <>
                      <p>{item.providerLabel} · 区域：{item.region || "由服务端管理"}</p>
                      <small>凭据：{item.secretStored ? "仅安全保存" : "未保存"}</small>
                    </>
                  ) : (
                    <>
                      <p>{item.providerLabel} · {item.baseUrl}</p>
                      <small>最近检测：{formatTime(item.lastTestedAt)} · 凭据：{item.secretStored ? "已安全保存" : "未保存"}</small>
                    </>
                  )}
                </div>
              </div>
              {item.safeErrorCategory ? (
                <div className="provider-safe-error"><CircleAlert size={14} />连接分类：{item.safeErrorCategory}</div>
              ) : null}
              {item.isBedrock ? (
                <>
                  <div className="bedrock-provider-status">
                    <span className={`governance-status ${item.connectionState === "connected" ? "success" : "warning"}`}>
                      {item.connectionState === "connected" ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}
                      {item.connectionState === "connected" ? "配置测试可用" : item.connectionLabel}
                    </span>
                    <span className="governance-status neutral">尚未进入 Agent 路由</span>
                  </div>
                  <AwsBedrockConnectionForm
                    busy={Boolean(busy)}
                    displayName={item.name}
                    region={item.region || "ap-southeast-1"}
                    title="更新 AWS Bedrock 凭据"
                    description="更新后会重新进行配置测试；凭据仅安全保存。"
                    submitLabel="更新并测试连接"
                    onSubmit={(payload) => rotateBedrockCredentials(item, payload)}
                  />
                </>
              ) : (
                <>
                  <div className="provider-model-grid">
                    {item.models.map((model) => (
                      <div key={model.id}>
                        <b>{model.name}</b>
                        <span>{model.capabilities.join(" · ")}</span>
                        <em className={model.priceKey ? "priced" : "unpriced"}>{model.pricingLabel}</em>
                      </div>
                    ))}
                    {item.models.length === 0 ? <p>完成有效连接检测后显示可用模型。</p> : null}
                  </div>
                  <div className="provider-actions">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={Boolean(busy) || item.connectionState === "disabled"}
                  onClick={() => runAction(
                    `test:${item.providerId}`,
                    () => testModelProvider(item.providerId),
                    `${item.name} 连通性检测完成。`,
                  )}
                >
                  {busy === `test:${item.providerId}` ? <Loader2 className="spin" size={14} /> : <PlugZap size={14} />}
                  检测连接
                </button>
                <label className="provider-secret-field">
                  <span>更新 Key</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={rotateDraft[item.providerId] || ""}
                    onChange={(event) => setRotateDraft((current) => ({ ...current, [item.providerId]: event.target.value }))}
                    placeholder="输入新 Key，不会回显"
                    disabled={Boolean(busy) || item.connectionState === "disabled"}
                  />
                </label>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={Boolean(busy) || item.connectionState === "disabled"}
                  onClick={() => rotateSecret(item)}
                >
                  {busy === `rotate:${item.providerId}` ? <Loader2 className="spin" size={14} /> : <RotateCcw size={14} />}
                  更换凭据
                </button>
                <button
                  type="button"
                  className="ghost-button danger"
                  disabled={Boolean(busy) || item.connectionState === "disabled"}
                  onClick={() => runAction(
                    `disable:${item.providerId}`,
                    () => disableModelProvider(item.providerId, item.revision),
                    `${item.name} 已停用。`,
                  )}
                >
                  {busy === `disable:${item.providerId}` ? <Loader2 className="spin" size={14} /> : <Power size={14} />}
                  停用
                </button>
                  </div>
                </>
              )}
            </article>
          ))}
        </div>
      )}

      <form className="provider-create-card" onSubmit={submitProvider}>
        <header>
          <div className="provider-mark muted" aria-hidden="true"><KeyRound size={17} /></div>
          <div>
            <h3>接入 DeepSeek 原厂</h3>
            <p>Endpoint 固定为官方地址，Key 仅写入后端安全凭据存储，页面不会保存或再次展示。</p>
          </div>
        </header>
        <div className="provider-create-grid">
          <label>
            <span>显示名称</span>
            <input value={draft.displayName} maxLength={120} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
          </label>
          <label>
            <span>Endpoint</span>
            <input value={DEEPSEEK_ENDPOINT} readOnly aria-readonly="true" />
          </label>
          <label className="provider-key-input">
            <span>API Key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={draft.apiKey}
              onChange={(event) => setDraft((current) => ({ ...current, apiKey: event.target.value }))}
              placeholder="输入 Key 后直接保存"
              required
              minLength={8}
            />
          </label>
          <button className="primary-button" type="submit" disabled={Boolean(busy)}>
            {busy === "create" ? <Loader2 className="spin" size={15} /> : <PlugZap size={15} />}
            保存并检测
          </button>
        </div>
      </form>

      <AwsBedrockConnectionForm busy={Boolean(busy)} onSubmit={submitBedrockProvider} />

      {notice ? <p className="governance-notice" role="status">{notice}</p> : null}
      {actionError ? <p className="governance-error" role="alert">{actionError}</p> : null}
    </section>
  );
}

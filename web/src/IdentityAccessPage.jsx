import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Loader2,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  UserRoundCog,
  UserRoundX,
} from "lucide-react";

import {
  createIdentityGroupMapping,
  disableIdentityGroupMapping,
  loadIdentityGovernance,
  searchIdentityGovernanceGroups,
  updateIdentityGroupMapping,
} from "./api.js";
import { identityAccessViewModel, identityGroupSearchViewModel, identitySessionViewModel } from "./identityAccessViewModel.js";
import { invalidateSettingsResource, loadSettingsResource, peekSettingsResource } from "./settingsDataStore.js";
import { settingsResourceKey } from "./settingsNavigation.js";

function IdentityAccessPageContent({ workspaceId = "", settingsScope = null, user = {}, authState = "unavailable", workspaceAccess = null }) {
  const [state, setState] = useState({ key: "", loading: true, error: "", payload: null });
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState({ loading: false, error: "", items: [], connected: null, permissionState: "" });
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [role, setRole] = useState("viewer");
  const [priority, setPriority] = useState(100);
  const [roleDraft, setRoleDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const loadGeneration = useRef(0);
  const currentKey = settingsResourceKey(settingsScope, "identity");
  const visiblePayload = state.key === currentKey ? state.payload : null;

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    const key = settingsResourceKey(settingsScope, "identity");
    const snapshot = key ? peekSettingsResource(key) : null;
    if (snapshot?.value) setState({ key, loading: false, error: snapshot.lastError ? "更新失败，正在显示上次可用配置。" : "", payload: snapshot.value });
    else setState({ key, loading: true, error: "", payload: null });
    try {
      const payload = key
        ? await loadSettingsResource(key, ({ signal }) => loadIdentityGovernance({ signal }))
        : await loadIdentityGovernance();
      if (generation !== loadGeneration.current) return;
      setState({ key, loading: false, error: "", payload });
    } catch (error) {
      if (generation !== loadGeneration.current || error?.name === "AbortError") return;
      setState({
        key,
        loading: false,
        error: error instanceof Error ? error.message : "身份治理读取失败",
        payload: snapshot?.value || null,
      });
    }
  }, [settingsScope]);
  const invalidate = () => {
    const key = settingsResourceKey(settingsScope, "identity");
    if (key) invalidateSettingsResource(key);
  };

  useEffect(() => {
    load();
    return () => { loadGeneration.current += 1; };
  }, [load]);
  const view = useMemo(() => identityAccessViewModel(visiblePayload || {}, workspaceId), [visiblePayload, workspaceId]);
  const session = useMemo(
    () => identitySessionViewModel({ user, authState, access: workspaceAccess }),
    [authState, user, workspaceAccess],
  );

  useEffect(() => {
    setRoleDraft(Object.fromEntries(view.mappings.map((item) => [item.mappingId, item.role])));
  }, [visiblePayload, workspaceId]);

  const searchGroups = async (event) => {
    event?.preventDefault();
    const safeQuery = String(query || "").trim();
    if (safeQuery.length < 2) {
      setSearchState({ loading: false, error: "至少输入 2 个字符。", items: [], connected: null, permissionState: "" });
      return;
    }
    setSearchState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const payload = await searchIdentityGovernanceGroups(safeQuery);
      const permissionState = String(payload?.permission_state || "").trim();
      const permissionMessage = permissionState === "denied"
        ? "目录令牌已取得，但缺少组读取权限；需要租户管理员同意 GroupMember.Read.All。"
        : payload?.connected === false
          ? "尚未取得 Microsoft Graph 登录令牌；请启用 Easy Auth 令牌存储后重新登录。"
          : "";
      setSearchState({
        loading: false,
        error: permissionMessage,
        items: identityGroupSearchViewModel(payload),
        connected: payload?.connected !== false,
        permissionState,
      });
    } catch (error) {
      setSearchState({
        loading: false,
        error: error instanceof Error ? error.message : "Entra 组搜索失败",
        items: [],
        connected: false,
        permissionState: "unavailable",
      });
    }
  };

  const runAction = async (key, action, successMessage) => {
    setBusy(key);
    setActionError("");
    setNotice("");
    try {
      await action();
      invalidate();
      setNotice(successMessage);
      await load();
    } catch (error) {
      if (error?.status === 409) {
        invalidate();
        setActionError("映射已被其他管理员更新，已重新加载最新版本，请复核后再试。");
        await load();
      } else {
        setActionError(error instanceof Error ? error.message : "操作失败，请稍后重试");
      }
    } finally {
      setBusy("");
    }
  };

  const createMapping = async () => {
    if (!selectedGroup || !workspaceId) {
      setActionError("请先选择 Entra 组和当前工作区。");
      return;
    }
    await runAction(
      "create",
      () => createIdentityGroupMapping({
        group_id: selectedGroup.id,
        display_name: selectedGroup.name,
        role,
        workspace_ids: [workspaceId],
        priority: Number(priority),
      }),
      `${selectedGroup.name} 已关联到当前工作区。`,
    );
    setSelectedGroup(null);
    setQuery("");
    setSearchState({ loading: false, error: "", items: [], connected: null, permissionState: "" });
  };

  return (
    <section className="identity-access" data-testid="identity-access-page">
      <header className="governance-panel-head">
        <div>
          <span className="governance-eyebrow">Microsoft Entra ID</span>
          <h2>身份与访问</h2>
          <p>用 Entra 组授予当前工作区角色。页面只展示友好名称，内部 ID 在建立关联后不会再返回客户端。</p>
        </div>
        <button className="icon-button" type="button" onClick={load} disabled={state.loading || Boolean(busy)} aria-label="刷新身份治理">
          <RefreshCw size={16} className={state.loading ? "spin" : ""} />
        </button>
      </header>

      <section className={`identity-session-card ${session.trusted ? "trusted" : "unavailable"}`} data-testid="identity-session-card">
        <div className="identity-session-person">
          <span aria-hidden="true">{session.trusted ? (session.displayName.trim()[0] || "ID") : "?"}</span>
          <div>
            <small>当前登录身份</small>
            <h3>{session.displayName}</h3>
            <p>{session.email || "请刷新登录状态后再进行权限操作"}</p>
          </div>
          <em>{session.trusted ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}{session.identitySourceLabel}</em>
        </div>
        <dl className="identity-session-facts">
          <div><dt>身份提供方</dt><dd>{session.identityLabel}</dd></div>
          <div><dt>当前角色</dt><dd>{session.roleLabel}</dd></div>
          <div><dt>授权来源</dt><dd>{session.authorizationLabel}</dd></div>
        </dl>
        <p className="identity-demo-note"><ShieldCheck size={14} />演示权限切换时，请在无痕窗口登录另一个已授权 Entra 用户；页面只展示服务端实际判定的角色，不提供前端伪切换。</p>
      </section>

      <div className="identity-trust-strip">
        <div>
          <span className={view.connection.ready ? "ready" : "pending"}>
            {view.connection.ready ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
          </span>
          <p><b>目录连接</b><small>{view.connection.label}</small></p>
        </div>
        <div>
          <span className={view.permissions.userRead.ready ? "ready" : "pending"}>
            {view.permissions.userRead.ready ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
          </span>
          <p><b>目录基础读取</b><small>User.ReadBasic.All · {view.permissions.userRead.label}</small></p>
        </div>
        <div>
          <span className={view.permissions.groupMembership.ready ? "ready" : "pending"}>
            {view.permissions.groupMembership.ready ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
          </span>
          <p><b>组成员解析</b><small>GroupMember.Read.All · {view.permissions.groupMembership.label}</small></p>
        </div>
        <div>
          <span className={view.membership.overageFallbackEnabled ? "ready" : "pending"}><ShieldCheck size={14} /></span>
          <p><b>超量组回退</b><small>{view.membership.overageFallbackEnabled ? "已启用 Graph 回退" : "仅使用登录声明"}</small></p>
        </div>
      </div>

      {state.loading ? (
        <div className="governance-empty"><Loader2 className="spin" size={18} />正在读取 Entra 治理配置</div>
      ) : state.error ? (
        <div className="governance-empty error">
          <CircleAlert size={18} />
          <span>{state.error}</span>
          <button type="button" className="ghost-button" onClick={load}>重试</button>
        </div>
      ) : (
        <>
          <section className="identity-mapping-section">
            <header>
              <div>
                <h3>当前工作区组映射</h3>
                <p>{view.mappings.length} 个关联 · 显式成员权限优先于组映射</p>
              </div>
            </header>
            <div className="identity-mapping-list">
              {view.mappings.length === 0 ? <div className="governance-empty compact">当前工作区尚未关联 Entra 组。</div> : null}
              {view.mappings.map((item) => (
                <div className={`identity-mapping-row ${item.enabled ? "" : "disabled"}`} key={item.mappingId}>
                  <div className="identity-group-mark"><UserRoundCog size={16} /></div>
                  <div className="identity-group-copy">
                    <b>{item.name}</b>
                    <span>{item.enabled ? "已生效" : "已停用"} · 优先级 {item.priority} · 版本 v{item.revision}</span>
                  </div>
                  <label>
                    <span>工作区角色</span>
                    <select
                      value={roleDraft[item.mappingId] || item.role}
                      onChange={(event) => setRoleDraft((current) => ({ ...current, [item.mappingId]: event.target.value }))}
                      disabled={!item.enabled || Boolean(busy)}
                    >
                      <option value="admin">管理员</option>
                      <option value="editor">编辑者</option>
                      <option value="viewer">查看者</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={!item.enabled || Boolean(busy) || roleDraft[item.mappingId] === item.role}
                    onClick={() => runAction(
                      `save:${item.mappingId}`,
                      () => updateIdentityGroupMapping(item.mappingId, {
                        base_revision: item.revision,
                        role: roleDraft[item.mappingId],
                      }),
                      `${item.name} 的角色已更新。`,
                    )}
                  >
                    {busy === `save:${item.mappingId}` ? <Loader2 className="spin" size={14} /> : <Save size={14} />}
                    保存
                  </button>
                  <button
                    type="button"
                    className="ghost-button danger"
                    disabled={!item.enabled || Boolean(busy)}
                    onClick={() => runAction(
                      `disable:${item.mappingId}`,
                      () => disableIdentityGroupMapping(item.mappingId, item.revision),
                      `${item.name} 已停用。`,
                    )}
                  >
                    {busy === `disable:${item.mappingId}` ? <Loader2 className="spin" size={14} /> : <UserRoundX size={14} />}
                    停用
                  </button>
                </div>
              ))}
            </div>
          </section>

          <section className="identity-add-section">
            <header>
              <h3>关联 Entra 组</h3>
              <p>从组织目录搜索现有组，然后为当前工作区分配角色；DataForge 不会代替管理员创建 Entra 组。</p>
            </header>
            <form className="identity-search" onSubmit={searchGroups}>
              <label>
                <Search size={15} />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索组名称，例如 Finance" />
              </label>
              <button className="secondary-button" type="submit" disabled={searchState.loading}>
                {searchState.loading ? <Loader2 className="spin" size={14} /> : <Search size={14} />}
                搜索
              </button>
            </form>
            {searchState.error ? <p className="governance-error">{searchState.error}</p> : null}
            {searchState.items.length > 0 ? (
              <div className="identity-search-results" role="listbox" aria-label="Entra 组搜索结果">
                {searchState.items.map((item) => (
                  <button
                    type="button"
                    role="option"
                    aria-selected={selectedGroup?.id === item.id}
                    className={selectedGroup?.id === item.id ? "selected" : ""}
                    key={item.id}
                    onClick={() => setSelectedGroup(item)}
                  >
                    <UserRoundCog size={15} />
                    <span><b>{item.name}</b><small>Microsoft Entra 组</small></span>
                    {selectedGroup?.id === item.id ? <CheckCircle2 size={15} /> : null}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="identity-mapping-form">
              <div>
                <span>已选择</span>
                <b>{selectedGroup?.name || "尚未选择组"}</b>
              </div>
              <label>
                <span>工作区角色</span>
                <select value={role} onChange={(event) => setRole(event.target.value)}>
                  <option value="viewer">查看者</option>
                  <option value="editor">编辑者</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
              <label>
                <span>优先级</span>
                <input type="number" min="0" max="1000" value={priority} onChange={(event) => setPriority(event.target.value)} />
              </label>
              <button className="primary-button" type="button" disabled={!selectedGroup || Boolean(busy)} onClick={createMapping}>
                {busy === "create" ? <Loader2 className="spin" size={15} /> : <ShieldCheck size={15} />}
                建立组映射
              </button>
            </div>
          </section>
        </>
      )}
      {notice ? <p className="governance-notice" role="status">{notice}</p> : null}
      {actionError ? <p className="governance-error" role="alert">{actionError}</p> : null}
    </section>
  );
}

export function IdentityAccessPage(props) {
  return <IdentityAccessPageContent key={String(props.settingsScope?.key || "")} {...props} />;
}

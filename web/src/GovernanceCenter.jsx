import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, SlidersHorizontal, Trash2, UserPlus, UsersRound } from "lucide-react";

import {
  inviteWorkspaceMember,
  loadEnterpriseIdentityPolicy,
  loadGovernanceLineage,
  loadWorkspaceMembers,
  removeWorkspaceMember,
  updateEnterpriseIdentityPolicy,
  updateWorkspaceMemberRole,
} from "./api.js";
import { EnterpriseIdentityPolicyModal } from "./EnterpriseIdentityPolicyModal.jsx";
import { memberDirectoryViewModel } from "./governanceViewModel.js";
import { resolveLineageScope } from "./governanceCenterModel.js";
import { MonitorPage } from "./MonitorPage.jsx";
import { ModelRoutingPage } from "./ModelRoutingPage.jsx";

const MEMBER_ROLES = ["admin", "editor", "viewer"];

function PageState({ loading, error, empty, onRetry }) {
  if (loading) return <div className="governance-state"><Loader2 className="spin" size={18} /><span>正在加载</span></div>;
  if (error) return <div className="governance-state governance-state-error"><span>{error}</span><button type="button" className="ghost-button" onClick={onRetry}>重试</button></div>;
  if (empty) return <div className="governance-state"><span>暂无可显示的记录。</span></div>;
  return null;
}

function SectionHeader({ kicker, title, description, actions = null }) {
  return (
    <header className="governance-page-head">
      <div>
        <p className="governance-kicker">{kicker}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="governance-page-actions">{actions}</div> : null}
    </header>
  );
}

function InviteMemberModal({ open, busy, error, onClose, onSubmit }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("editor");
  useEffect(() => {
    if (open) {
      setEmail("");
      setName("");
      setRole("editor");
    }
  }, [open]);
  if (!open) return null;
  return (
    <div className="governance-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="governance-modal governance-modal-compact" role="dialog" aria-modal="true" aria-labelledby="workspace-invite-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="governance-modal-head">
          <div>
            <p className="governance-kicker">Collaboration</p>
            <h2 id="workspace-invite-title">邀请成员</h2>
            <p>邀请会记录到当前工作区；目录与邮件能力由已配置的 Entra 服务决定。</p>
          </div>
        </header>
        <label className="governance-field"><span>企业邮箱</span><input value={email} type="email" onChange={(event) => setEmail(event.target.value)} placeholder="name@corp.example" disabled={busy} /></label>
        <label className="governance-field"><span>显示名称（可选）</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="成员名称" disabled={busy} /></label>
        <label className="governance-field"><span>工作区角色</span><select value={role} onChange={(event) => setRole(event.target.value)} disabled={busy}>{MEMBER_ROLES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        {error ? <p className="governance-form-error" role="alert">{error}</p> : null}
        <footer className="governance-modal-actions">
          <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="primary-button" type="button" onClick={() => onSubmit({ email, name, role })} disabled={busy || !email.trim()}>{busy ? <Loader2 className="spin" size={16} /> : null}发送邀请</button>
        </footer>
      </section>
    </div>
  );
}

function MembersPage({ workspaceId, capabilities }) {
  const canWrite = capabilities?.sections?.members?.write === true;
  const [state, setState] = useState({ loading: true, error: "", payload: null });
  const [policy, setPolicy] = useState({ open: false, busy: false, error: "", domains: [] });
  const [invite, setInvite] = useState({ open: false, busy: false, error: "" });
  const loadMembers = useCallback(async () => {
    if (!workspaceId) return;
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const payload = await loadWorkspaceMembers(workspaceId);
      setState({ loading: false, error: "", payload });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : "成员目录读取失败", payload: null });
    }
  }, [workspaceId]);
  useEffect(() => { loadMembers(); }, [loadMembers]);
  const members = useMemo(() => memberDirectoryViewModel(state.payload?.members || []), [state.payload]);
  const openPolicy = async () => {
    setPolicy({ open: true, busy: true, error: "", domains: [] });
    try {
      const payload = await loadEnterpriseIdentityPolicy(workspaceId);
      setPolicy({ open: true, busy: false, error: "", domains: payload?.trusted_email_domains || [] });
    } catch (error) {
      setPolicy((current) => ({ ...current, busy: false, error: error instanceof Error ? error.message : "身份策略读取失败" }));
    }
  };
  const savePolicy = async (domains) => {
    setPolicy((current) => ({ ...current, busy: true, error: "" }));
    try {
      await updateEnterpriseIdentityPolicy(workspaceId, domains);
      setPolicy({ open: false, busy: false, error: "", domains });
      await loadMembers();
    } catch (error) {
      setPolicy((current) => ({ ...current, busy: false, error: error instanceof Error ? error.message : "身份策略保存失败" }));
    }
  };
  const inviteMember = async (payload) => {
    setInvite((current) => ({ ...current, busy: true, error: "" }));
    try {
      await inviteWorkspaceMember(workspaceId, payload);
      setInvite({ open: false, busy: false, error: "" });
      await loadMembers();
    } catch (error) {
      setInvite((current) => ({ ...current, busy: false, error: error instanceof Error ? error.message : "邀请发送失败" }));
    }
  };
  const changeRole = async (member, role) => {
    if (!member.actionRef || member.owner) return;
    try {
      await updateWorkspaceMemberRole(workspaceId, member.actionRef, role);
      await loadMembers();
    } catch (error) {
      setState((current) => ({ ...current, error: error instanceof Error ? error.message : "成员角色更新失败" }));
    }
  };
  const removeMember = async (member) => {
    if (!member.actionRef || member.owner || !window.confirm(`移除成员 ${member.label}？`)) return;
    try {
      await removeWorkspaceMember(workspaceId, member.actionRef);
      await loadMembers();
    } catch (error) {
      setState((current) => ({ ...current, error: error instanceof Error ? error.message : "移除成员失败" }));
    }
  };
  return (
    <main className="governance-page">
      <SectionHeader kicker="Collaboration" title="成员与协作" description="企业成员目录、角色与邀请记录均以工作区授权为准。" actions={<><button className="icon-button" type="button" title="刷新成员目录" aria-label="刷新成员目录" onClick={loadMembers}><RefreshCw size={17} /></button>{canWrite ? <button className="icon-button" type="button" title="企业身份展示策略" aria-label="企业身份展示策略" onClick={openPolicy}><SlidersHorizontal size={17} /></button> : null}{canWrite ? <button className="primary-button" type="button" onClick={() => setInvite({ open: true, busy: false, error: "" })}><UserPlus size={16} />邀请成员</button> : null}</>} />
      <div className="governance-data-frame">
        <PageState loading={state.loading} error={state.error} empty={!state.loading && !state.error && !members.length} onRetry={loadMembers} />
        {!state.loading && !state.error && members.length ? <div className="governance-table-scroll"><table className="governance-table"><thead><tr><th>成员</th><th>角色</th><th>状态</th><th>最近活动</th>{canWrite ? <th aria-label="操作" /> : null}</tr></thead><tbody>{members.map((member) => <tr key={member.actionRef || member.subjectLabel}><td><div className="governance-member-cell"><span className="governance-member-avatar">{member.label.slice(0, 1).toUpperCase()}</span><div><b>{member.label}</b><small>{member.detail || member.subjectLabel}</small></div></div></td><td>{canWrite && !member.owner ? <select value={member.role} onChange={(event) => changeRole(member, event.target.value)}>{MEMBER_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}</select> : member.role}</td><td><span className={`governance-status ${member.status}`}>{member.status === "active" ? "活跃" : "待接受"}</span></td><td>{member.lastSeenAt ? new Date(member.lastSeenAt).toLocaleString("zh-CN") : "未记录"}</td>{canWrite ? <td>{!member.owner ? <button className="icon-button danger" type="button" title="移除成员" aria-label="移除成员" onClick={() => removeMember(member)}><Trash2 size={16} /></button> : null}</td> : null}</tr>)}</tbody></table></div> : null}
      </div>
      <EnterpriseIdentityPolicyModal open={policy.open} initialDomains={policy.domains} busy={policy.busy} error={policy.error} onSave={savePolicy} onClose={() => !policy.busy && setPolicy((current) => ({ ...current, open: false }))} />
      <InviteMemberModal open={invite.open} busy={invite.busy} error={invite.error} onSubmit={inviteMember} onClose={() => !invite.busy && setInvite((current) => ({ ...current, open: false }))} />
    </main>
  );
}

function LineagePage({ workspaceId, capabilities }) {
  const scope = resolveLineageScope(capabilities);
  const [state, setState] = useState({ loading: true, error: "", payload: null });
  const loadLineage = useCallback(async () => {
    if (!workspaceId) return;
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      setState({ loading: false, error: "", payload: await loadGovernanceLineage(workspaceId, { scope }) });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : "溯源记录读取失败", payload: null });
    }
  }, [workspaceId, scope]);
  useEffect(() => { loadLineage(); }, [loadLineage]);
  const rows = state.payload?.items || [];
  return <main className="governance-page"><SectionHeader kicker="Lineage" title="审计与溯源" description={scope === "workspace" ? "显示当前工作区的安全操作脉络。" : "仅显示由当前身份发起的安全操作脉络。"} actions={<button className="icon-button" type="button" title="刷新溯源记录" aria-label="刷新溯源记录" onClick={loadLineage}><RefreshCw size={17} /></button>} /><div className="governance-data-frame"><PageState loading={state.loading} error={state.error} empty={!state.loading && !state.error && !rows.length} onRetry={loadLineage} />{!state.loading && !state.error && rows.length ? <div className="governance-timeline">{rows.map((row) => <article className="governance-lineage-row" key={row.correlation_ref}><div className="governance-lineage-dot" /><div><b>{row.title}</b><p>{row.route} · {row.status} · 数据版本 {row.data_revision ?? "未记录"}</p><small>{row.timestamp ? new Date(row.timestamp).toLocaleString("zh-CN") : "未记录"} · {row.initiator?.subject_label || "成员（已脱敏）"}</small></div><code>{row.correlation_ref}</code></article>)}</div> : null}</div></main>;
}

export function GovernanceCenter({ section, workspaceId, capabilities, dashboard, workspaceAccess }) {
  if (section === "monitor") return <MonitorPage workspaceId={workspaceId} workspaceAccess={workspaceAccess} />;
  if (section === "members") return <MembersPage workspaceId={workspaceId} capabilities={capabilities} />;
  if (section === "lineage") return <LineagePage workspaceId={workspaceId} capabilities={capabilities} />;
  if (section === "model-routing") return <ModelRoutingPage workspaceId={workspaceId} />;
  return <main className="governance-page"><PageState empty /></main>;
}

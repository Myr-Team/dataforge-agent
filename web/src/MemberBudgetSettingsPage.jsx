import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ArrowLeft,
  BellRing,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  HelpCircle,
  Loader2,
  Mail,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";

import {
  loadMemberBudgetAlerts,
  loadMemberBudgetMembers,
  loadMemberBudgets,
  loadMemberBudgetNotification,
  disableMemberBudget,
  saveMemberBudget,
  saveMemberBudgetNotification,
  sendMemberBudgetTestEmail,
} from "./api.js";
import { memberBudgetViewModel, safeTestEmailResult } from "./memberBudgetViewModel.js";

const EMPTY_VIEW = memberBudgetViewModel();

function safeFailureState(error) {
  if (error?.status === 403) return "permission_required";
  if (error?.status === 404 && error?.message === "email_configuration_disabled") return "disabled";
  if (error?.status === 404) return "not_configured";
  return "unavailable";
}

async function loadBudgetView() {
  const [budgetsResult, membersResult, notificationResult, alertsResult] = await Promise.allSettled([
    loadMemberBudgets(),
    loadMemberBudgetMembers(),
    loadMemberBudgetNotification(),
    loadMemberBudgetAlerts(),
  ]);
  const budgetsState = budgetsResult.status === "fulfilled"
    ? ["complete", "partial", "unavailable"].includes(budgetsResult.value?.data_status)
      ? budgetsResult.value.data_status
      : "partial"
    : "unavailable";
  const notificationState = notificationResult.status === "rejected"
    ? safeFailureState(notificationResult.reason)
    : notificationResult.value?.data_status === "unavailable"
      ? "unavailable"
      : "configured";
  const alertsState = alertsResult.status === "fulfilled" && alertsResult.value?.data_status !== "unavailable"
    ? "available"
    : "unavailable";
  const view = memberBudgetViewModel({
    budgets: budgetsResult.status === "fulfilled" ? budgetsResult.value : {},
    budgetsState,
    members: membersResult.status === "fulfilled" ? membersResult.value : {},
    notification: notificationResult.status === "fulfilled" ? notificationResult.value : null,
    notificationState,
    alerts: alertsResult.status === "fulfilled" ? alertsResult.value : {},
    alertsState,
  });
  return {
    state: budgetsState === "unavailable"
      ? safeFailureState(budgetsResult.reason)
      : membersResult.status === "rejected"
        || ["unavailable", "permission_required"].includes(notificationState)
        || alertsState === "unavailable"
        ? "partial"
        : view.rows.length
          ? "available"
          : "empty",
    view,
  };
}

function MetricHelp({ label, children }) {
  return (
    <span className="member-budget-label">
      <span>{label}</span>
      <span className="member-budget-help" tabIndex={0} role="button" aria-label={`说明：${children}`}>
        <HelpCircle size={13} aria-hidden="true" />
        <span className="member-budget-tooltip" role="tooltip">{children}</span>
      </span>
    </span>
  );
}

function CompactModal({ title, description, onClose, children }) {
  const dialogRef = useRef(null);
  const restoreFocusRef = useRef(typeof document === "undefined" ? null : document.activeElement);

  useEffect(() => {
    const root = document.getElementById("root");
    const previousAriaHidden = root?.getAttribute("aria-hidden");
    root?.setAttribute("inert", "");
    root?.setAttribute("aria-hidden", "true");

    const focusable = () => [...(dialogRef.current?.querySelectorAll(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    ) || [])];
    const initialFocus = () => dialogRef.current?.querySelector("[data-modal-initial-focus]") || focusable()[0];
    const focusFrame = window.requestAnimationFrame(() => initialFocus()?.focus());
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", onKeyDown);
      root?.removeAttribute("inert");
      if (previousAriaHidden === null) root?.removeAttribute("aria-hidden");
      else root?.setAttribute("aria-hidden", previousAriaHidden);
      restoreFocusRef.current?.focus?.();
    };
  }, [onClose]);

  return createPortal(
    <div className="member-budget-modal-overlay" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="member-budget-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <strong>{title}</strong>
            {description ? <p>{description}</p> : null}
          </div>
          <button type="button" className="member-budget-icon-button" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </button>
        </header>
        {children}
      </section>
    </div>,
    document.body,
  );
}

function SummaryCard({ icon: Icon, label, help, value, note, tone = "" }) {
  return (
    <article className={`member-budget-summary-card ${tone}`}>
      <div className="member-budget-summary-icon"><Icon size={17} /></div>
      <div>
        <MetricHelp label={label}>{help}</MetricHelp>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </article>
  );
}

function memberSubtitle(row, { includeDepartment = true } = {}) {
  return [row.identityLabel, row.lifecycleLabel, includeDepartment ? row.departmentLabel : ""]
    .filter(Boolean)
    .join(" · ");
}

function BudgetProgress({ row }) {
  return (
    <div className="member-budget-progress">
      <div className="member-budget-progress-copy">
        <b>{row.statusLabel}</b>
        <span>{row.coverageLabel}</span>
      </div>
      <div className="member-budget-progress-track" aria-label={`${row.memberLabel} 预算进度 ${row.statusLabel}`}>
        {row.progressWidth === null
          ? <span className="unavailable" />
          : <span className={row.severity} style={{ width: `${row.progressWidth}%` }} />}
      </div>
    </div>
  );
}

function BudgetForm({ row, members, busy, error, onClose, onSave, onDisable }) {
  const [memberRef, setMemberRef] = useState(row?.memberRef || members.find((item) => item.identityState === "active")?.memberRef || "");
  const [amount, setAmount] = useState(row?.budgetAmount === null || row?.budgetAmount === undefined ? "" : String(row.budgetAmount));
  const [thresholds, setThresholds] = useState(row?.thresholdsPct?.join(", ") || "80, 95, 100");
  const [enabled, setEnabled] = useState(row ? row.enabled : true);
  const [validation, setValidation] = useState("");
  const [confirmDisable, setConfirmDisable] = useState(false);

  const submit = (event) => {
    event.preventDefault();
    const amountValue = Number(amount);
    const thresholdValues = thresholds.split(",").map((item) => Number(item.trim()));
    const validThresholds = thresholdValues.length > 0
      && thresholdValues.every((item) => Number.isInteger(item) && item >= 1 && item <= 100)
      && thresholdValues.every((item, index) => index === 0 || item > thresholdValues[index - 1]);
    if (!Number.isFinite(amountValue) || amountValue <= 0) {
      setValidation("请输入大于 0 的预算金额");
      return;
    }
    if (!validThresholds) {
      setValidation("提醒阈值需为 1–100 的升序唯一整数");
      return;
    }
    setValidation("");
    onSave({
      budgetId: row?.budgetId || "",
      memberRef,
      amountUsd: amountValue,
      thresholdsPct: thresholdValues,
      enabled,
      baseRevision: row?.revision || 0,
    });
  };

  return (
    <form className="member-budget-form" onSubmit={submit}>
      <label>
        <span>Entra 成员</span>
        <select data-modal-initial-focus={!row ? "" : undefined} aria-label="Entra 成员" value={memberRef} onChange={(event) => setMemberRef(event.target.value)} disabled={Boolean(row)}>
          {members.filter((item) => item.identityState === "active").map((item) => (
            <option value={item.memberRef} key={item.memberRef}>{item.memberLabel} · {item.roleLabel}</option>
          ))}
        </select>
      </label>
      <label>
        <span>月度预算（USD）</span>
        <input data-modal-initial-focus={row ? "" : undefined} aria-label="月度预算（USD）" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
      </label>
      <label>
        <span>提醒阈值</span>
        <input aria-label="提醒阈值" value={thresholds} onChange={(event) => setThresholds(event.target.value)} />
        <small>使用英文逗号分隔，例如 80, 95, 100。</small>
      </label>
      <label className="member-budget-checkbox">
        <input aria-label="启用本月预算" type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        <span>启用本月预算</span>
      </label>
      {validation || error ? <div className="member-budget-inline-error">{validation || error}</div> : null}
      <footer>
        {row?.canDisable ? (
          <button
            type="button"
            className="member-budget-danger-button"
            disabled={busy}
            onClick={() => {
              if (confirmDisable) onDisable(row);
              else setConfirmDisable(true);
            }}
          >
            {confirmDisable ? "确认停用" : "停用预算"}
          </button>
        ) : null}
        <button type="button" className="member-budget-secondary-button" onClick={onClose}>取消</button>
        <button type="submit" className="member-budget-primary-button" disabled={busy || !memberRef}>
          {busy ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />}保存预算
        </button>
      </footer>
    </form>
  );
}

function MailForm({ notification, members, busy, error, onClose, onSave }) {
  const admins = members.filter((item) => item.identityState === "active" && ["管理员", "所有者"].includes(item.roleLabel));
  const [recipientMemberRef, setRecipientMemberRef] = useState(notification.recipientMemberRef || admins[0]?.memberRef || "");
  const [senderDisplayName, setSenderDisplayName] = useState(notification.senderDisplayName);
  const [subjectTemplate, setSubjectTemplate] = useState(notification.subjectTemplate);
  const [bodyTemplate, setBodyTemplate] = useState(notification.bodyTemplate);
  const [enabled, setEnabled] = useState(notification.configured ? notification.enabled : true);

  const submit = (event) => {
    event.preventDefault();
    onSave({
      recipientMemberRef,
      senderDisplayName,
      subjectTemplate,
      bodyTemplate,
      enabled,
      baseRevision: notification.revision,
    });
  };

  return (
    <form className="member-budget-form mail" onSubmit={submit}>
      <div className="member-budget-mail-safety"><ShieldCheck size={14} />收件地址由 Entra 管理，浏览器不保存或显示邮箱明文。</div>
      <label>
        <span>管理员</span>
        <select data-modal-initial-focus="" aria-label="管理员" value={recipientMemberRef} onChange={(event) => setRecipientMemberRef(event.target.value)}>
          {admins.map((item) => <option value={item.memberRef} key={item.memberRef}>{item.memberLabel} · {item.roleLabel}</option>)}
        </select>
      </label>
      <label>
        <span>发件人显示名称</span>
        <input value={senderDisplayName} onChange={(event) => setSenderDisplayName(event.target.value)} maxLength={120} />
      </label>
      <label>
        <span>邮件主题</span>
        <input value={subjectTemplate} onChange={(event) => setSubjectTemplate(event.target.value)} maxLength={200} />
      </label>
      <label>
        <span>纯文本正文</span>
        <textarea value={bodyTemplate} onChange={(event) => setBodyTemplate(event.target.value)} rows={5} maxLength={4000} />
      </label>
      <label className="member-budget-checkbox">
        <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        <span>启用阈值提醒</span>
      </label>
      {error ? <div className="member-budget-inline-error">{error}</div> : null}
      <footer>
        <button type="button" className="member-budget-secondary-button" onClick={onClose}>取消</button>
        <button type="submit" className="member-budget-primary-button" disabled={busy || !recipientMemberRef}>
          {busy ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />}保存邮件设置
        </button>
      </footer>
    </form>
  );
}

export function MemberBudgetSettingsPage({ onBack = () => {}, onChanged = () => {} }) {
  const [state, setState] = useState("loading");
  const [view, setView] = useState(EMPTY_VIEW);
  const [query, setQuery] = useState("");
  const [department, setDepartment] = useState("");
  const [budgetModal, setBudgetModal] = useState(null);
  const [mailModal, setMailModal] = useState(false);
  const [busy, setBusy] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState(null);

  const reload = async ({ preserveNotice = false } = {}) => {
    setState("loading");
    if (!preserveNotice) setNotice(null);
    const loaded = await loadBudgetView();
    setView(loaded.view);
    setState(loaded.state);
  };

  useEffect(() => {
    let cancelled = false;
    loadBudgetView().then((loaded) => {
      if (cancelled) return;
      setView(loaded.view);
      setState(loaded.state);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const departments = useMemo(
    () => [...new Set(view.rows.map((row) => row.departmentLabel).filter(Boolean))],
    [view.rows],
  );
  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return view.rows.filter((row) => (
      (!needle || `${row.memberLabel} ${row.departmentLabel} ${row.primaryModel}`.toLowerCase().includes(needle))
      && (!department || row.departmentLabel === department)
    ));
  }, [department, query, view.rows]);
  const emailConfigurationDisabled = ["disabled", "permission_required"].includes(view.notification.state);
  const emailConfigurationDisabledReason = view.notification.state === "permission_required"
    ? "需要租户邮件管理员角色"
    : "邮件配置功能未启用";

  const saveBudget = async (payload) => {
    setBusy("budget");
    setFormError("");
    try {
      await saveMemberBudget(payload);
      setBudgetModal(null);
      setNotice({ tone: "success", text: "预算已保存" });
      onChanged();
      await reload({ preserveNotice: true });
    } catch (error) {
      if (error?.status === 409) {
        setBudgetModal(null);
        setNotice({ tone: "warning", text: "配置已更新，正在重新载入" });
        await reload({ preserveNotice: true });
      } else if (error?.status === 403) {
        setFormError("当前账户没有修改预算的权限");
      } else {
        setFormError("预算暂时无法保存，请稍后重试");
      }
    } finally {
      setBusy("");
    }
  };

  const disableBudget = async (row) => {
    setBusy("budget");
    setFormError("");
    try {
      await disableMemberBudget(row.budgetId, row.revision);
      setBudgetModal(null);
      setNotice({ tone: "success", text: "预算已停用" });
      onChanged();
      await reload({ preserveNotice: true });
    } catch (error) {
      if (error?.status === 409) {
        setBudgetModal(null);
        setNotice({ tone: "warning", text: "配置已更新，正在重新载入" });
        await reload({ preserveNotice: true });
      } else if (error?.status === 403) {
        setFormError("当前账户没有停用预算的权限");
      } else {
        setFormError("预算暂时无法停用，请稍后重试");
      }
    } finally {
      setBusy("");
    }
  };

  const saveMail = async (payload) => {
    setBusy("mail");
    setFormError("");
    try {
      await saveMemberBudgetNotification(payload);
      setMailModal(false);
      setNotice({ tone: "success", text: "邮件设置已保存" });
      onChanged();
      await reload({ preserveNotice: true });
    } catch (error) {
      if (error?.status === 409) {
        setMailModal(false);
        setNotice({ tone: "warning", text: "配置已更新，正在重新载入" });
        await reload({ preserveNotice: true });
      } else if (error?.status === 403) {
        setFormError("请选择当前组织中启用的管理员");
      } else {
        setFormError("邮件设置暂时无法保存，请稍后重试");
      }
    } finally {
      setBusy("");
    }
  };

  const sendTest = async () => {
    setBusy("test");
    setNotice(null);
    try {
      const result = safeTestEmailResult(await sendMemberBudgetTestEmail());
      setNotice({ tone: result.state === "sent" ? "success" : "warning", text: result.label });
    } catch (error) {
      const stateValue = error?.status === 404
        ? { state: "failed", safe_error_category: "not_configured" }
        : error?.status === 403
          ? { state: "failed", safe_error_category: "permission_required" }
          : { state: "failed", safe_error_category: "service_unavailable" };
      const result = safeTestEmailResult(stateValue);
      setNotice({ tone: "warning", text: result.label });
    } finally {
      setBusy("");
    }
  };

  const skeleton = state === "loading";
  return (
    <main className="member-budget-page">
      <header className="member-budget-page-head">
        <div>
          <button type="button" className="member-budget-back" onClick={onBack}><ArrowLeft size={15} />返回设置</button>
          <span className="eyeless-label">FinOps · Entra</span>
          <h1>成员成本预算</h1>
          <p>按 Entra 成员查看本月请求级估算成本与计价覆盖，并为管理员配置阈值邮件提醒。</p>
        </div>
        <div className="member-budget-page-actions">
          <button
            type="button"
            className="member-budget-secondary-button"
            onClick={() => { setFormError(""); setMailModal(true); }}
            disabled={emailConfigurationDisabled}
            title={emailConfigurationDisabled ? emailConfigurationDisabledReason : undefined}
          >
            <Settings2 size={14} />配置邮件
          </button>
          <button type="button" className="member-budget-primary-button" onClick={() => { setFormError(""); setBudgetModal({}); }} disabled={!view.createMembers.length}>
            <Plus size={14} />设置成员预算
          </button>
        </div>
      </header>

      {notice ? (
        <div className={`member-budget-notice ${notice.tone}`} role="status">
          {notice.tone === "success" ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
          <span>{notice.text}</span>
        </div>
      ) : null}

      <section className="member-budget-summary" aria-label="预算摘要">
        {skeleton ? Array.from({ length: 4 }, (_, index) => <div className="member-budget-summary-skeleton" key={index} />) : (
          <>
            <SummaryCard icon={CircleDollarSign} label="本月估算成本" help="仅汇总有可靠价目表匹配的请求；未计价请求不会被当作 0。" value={view.summary.estimatedSpendLabel} note={view.summary.dataStatus === "partial" ? "部分请求尚未计价" : "请求级估算 · USD"} />
            <SummaryCard icon={Users} label="已配置成员" help="当前组织中已保存预算的 Entra 成员数量，含已停用预算。" value={view.summary.configuredCount === null ? "不可用" : String(view.summary.configuredCount)} note="UTC 自然月预算" />
            <SummaryCard icon={BellRing} label="接近预算" help="实际预算进度已达到该成员最低提醒阈值的数量。" value={view.summary.nearBudgetCount === null ? "不可用" : String(view.summary.nearBudgetCount)} note="按真实进度计算" tone={view.summary.nearBudgetCount ? "warning" : ""} />
            <SummaryCard icon={Mail} label="已发送提醒" help="当前保留窗口内状态为已发送的阈值提醒数量。" value={view.summary.sentAlertCount === null ? "不可用" : String(view.summary.sentAlertCount)} note="自动发送默认关闭" />
          </>
        )}
      </section>

      {state === "unavailable" || state === "permission_required" || state === "not_configured" ? (
        <section className="member-budget-state">
          <AlertCircle size={20} />
          <strong>{state === "permission_required" ? "需要管理员权限" : state === "not_configured" ? "成员预算尚未启用" : "成员预算暂时不可用"}</strong>
          <p>没有使用示例金额填充当前状态。请检查服务配置后重试。</p>
          <button type="button" className="member-budget-secondary-button" onClick={() => reload()}><RefreshCw size={14} />重试</button>
        </section>
      ) : (
        <>
          <section className="member-budget-members">
            <div className="member-budget-section-head">
              <div>
                <h2>成员预算</h2>
                <p>成本为估算值；计价覆盖不足时会单独标明。</p>
              </div>
              <span>{filteredRows.length} 位成员</span>
            </div>
            <div className="member-budget-filters">
              <label>
                <Search size={14} />
                <input aria-label="搜索成员预算" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索成员、部门或模型" />
              </label>
              <select aria-label="筛选部门" value={department} onChange={(event) => setDepartment(event.target.value)}>
                <option value="">全部部门</option>
                {departments.map((item) => <option value={item} key={item}>{item}</option>)}
              </select>
            </div>
            {view.rows.length === 0 ? (
              <div className="member-budget-empty">
                <CircleDollarSign size={22} />
                <strong>尚未设置成员预算</strong>
                <p>选择一位启用中的 Entra 成员，为其设置 UTC 自然月预算。</p>
              </div>
            ) : filteredRows.length === 0 ? (
              <div className="member-budget-empty compact"><Search size={20} /><strong>没有匹配的成员</strong></div>
            ) : (
              <>
                <div className="member-budget-table-wrap">
                  <table className="member-budget-table">
                    <thead>
                      <tr>
                        <th>Entra 成员</th>
                        <th><MetricHelp label="当月成本">已可靠计价请求的估算成本；0 与缺失严格区分。</MetricHelp></th>
                        <th>预算</th>
                        <th><MetricHelp label="进度">柱长按真实估算成本除以预算计算，最高显示为 100%。</MetricHelp></th>
                        <th>主要模型</th>
                        <th><span className="sr-only">操作</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRows.map((row) => (
                        <tr key={row.budgetId}>
                          <td>
                            <div className="member-budget-member-cell">
                              <span>{row.memberInitial}</span>
                              <div>
                                <b>{row.memberLabel}</b>
                                <small title={memberSubtitle(row)} aria-label={memberSubtitle(row)}>{memberSubtitle(row)}</small>
                              </div>
                            </div>
                          </td>
                          <td><b>{row.spendLabel}</b><small className={row.dataStatus === "partial" ? "partial" : ""}>{row.coverageLabel}</small></td>
                          <td><b>{row.budgetLabel}</b><small>{row.thresholdsPct.join(" / ")}%</small></td>
                          <td><BudgetProgress row={row} /></td>
                          <td><b className="member-budget-model">{row.primaryModel}</b><small>{row.workspaceLabel}</small></td>
                          <td>
                            <button
                              type="button"
                              className="member-budget-icon-button"
                              aria-label={`编辑 ${row.memberLabel} 预算`}
                              disabled={!row.canEdit}
                              onClick={() => { setFormError(""); setBudgetModal(row); }}
                            >
                              <Pencil size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="member-budget-mobile-list">
                  {filteredRows.map((row) => (
                    <article className="member-budget-mobile-card" key={row.budgetId}>
                      <header>
                        <div className="member-budget-member-cell">
                          <span>{row.memberInitial}</span>
                          <div>
                            <b>{row.memberLabel}</b>
                            <small title={memberSubtitle(row)} aria-label={memberSubtitle(row)}>{memberSubtitle(row, { includeDepartment: false })}</small>
                          </div>
                        </div>
                        <button type="button" className="member-budget-icon-button" aria-label={`编辑 ${row.memberLabel} 预算`} disabled={!row.canEdit} onClick={() => { setFormError(""); setBudgetModal(row); }}><Pencil size={14} /></button>
                      </header>
                      <dl>
                        <div><dt>当月成本</dt><dd>{row.spendLabel}<small>{row.coverageLabel}</small></dd></div>
                        <div><dt>预算</dt><dd>{row.budgetLabel}<small>{row.thresholdsPct.join(" / ")}%</small></dd></div>
                        <div><dt>主要模型</dt><dd>{row.primaryModel}</dd></div>
                      </dl>
                      <BudgetProgress row={row} />
                    </article>
                  ))}
                </div>
              </>
            )}
          </section>

          <section className="member-budget-mail-strip">
            <div className="member-budget-mail-icon"><Mail size={17} /></div>
            <div>
              <span>管理员邮件提醒</span>
              <strong>
                {view.notification.state === "disabled"
                  ? "邮件配置未启用"
                  : view.notification.configured
                    ? `${view.notification.recipientLabel} · 已安全保存`
                    : view.notification.state === "permission_required"
                      ? "需要租户邮件管理员角色"
                      : view.notification.state === "unavailable"
                        ? "邮件状态不可用"
                        : "尚未配置"}
              </strong>
              <small>
                {view.notification.state === "disabled"
                  ? "成员预算仍可查看；邮件配置入口由独立功能开关控制。"
                  : view.notification.configured
                    ? "收件地址由 Entra 目录解析，不在页面中显示。"
                    : view.notification.state === "permission_required"
                      ? "预算仍可管理；请由具备租户邮件管理应用角色的管理员配置提醒。"
                      : view.notification.state === "unavailable"
                      ? "预算数据仍可查看；邮件服务恢复后可继续配置。"
                      : "配置后可发送一封测试邮件；自动阈值提醒仍由独立开关控制。"}
              </small>
            </div>
            <div className="member-budget-mail-actions">
              <button type="button" className="member-budget-secondary-button" onClick={sendTest} disabled={busy === "test" || !view.notification.configured}>
                {busy === "test" ? <Loader2 size={14} className="spin" /> : <Mail size={14} />}发送测试邮件
              </button>
              <button
                type="button"
                className="member-budget-config-button"
                onClick={() => { setFormError(""); setMailModal(true); }}
                disabled={emailConfigurationDisabled}
                title={emailConfigurationDisabled ? emailConfigurationDisabledReason : undefined}
              ><Settings2 size={13} />配置</button>
            </div>
          </section>

          <section className="member-budget-alerts">
            <div className="member-budget-section-head">
              <div><h2>最近提醒</h2><p>只显示安全投递状态，不显示邮箱、服务请求 ID 或错误正文。</p></div>
              <span>{view.alerts.length} 条</span>
            </div>
            {view.alertsState === "unavailable" ? (
              <div className="member-budget-empty compact"><AlertCircle size={19} /><strong>提醒记录暂时不可用</strong></div>
            ) : view.alerts.length ? (
              <div className="member-budget-alert-list">
                {view.alerts.map((alert, index) => (
                  <article key={`${alert.memberLabel}-${alert.thresholdLabel}-${index}`}>
                    <span className={`member-budget-alert-dot ${alert.deliveryState}`} />
                    <div><b>{alert.memberLabel} · {alert.thresholdLabel}</b><small>{alert.spendLabel} · {alert.coverageLabel}</small></div>
                    <span>{alert.deliveryLabel}</span>
                    <time>{alert.triggeredAt ? new Date(alert.triggeredAt).toLocaleString("zh-CN", { hour12: false }) : "时间未记录"}</time>
                    <ChevronRight size={14} />
                  </article>
                ))}
              </div>
            ) : <div className="member-budget-empty compact"><BellRing size={19} /><strong>暂无提醒记录</strong></div>}
          </section>
        </>
      )}

      {budgetModal ? (
        <CompactModal title={budgetModal.budgetId ? "编辑成员预算" : "设置成员预算"} description="预算按 UTC 自然月计算，币种固定为 USD。" onClose={() => setBudgetModal(null)}>
          <BudgetForm
            row={budgetModal.budgetId ? budgetModal : null}
            members={budgetModal.budgetId ? view.eligibleMembers : view.createMembers}
            busy={busy === "budget"}
            error={formError}
            onClose={() => setBudgetModal(null)}
            onSave={saveBudget}
            onDisable={disableBudget}
          />
        </CompactModal>
      ) : null}
      {mailModal ? (
        <CompactModal title="邮件提醒设置" description="第一版仅向一位启用中的 Owner / Admin 发送。" onClose={() => setMailModal(false)}>
          <MailForm notification={view.notification} members={view.eligibleMembers} busy={busy === "mail"} error={formError} onClose={() => setMailModal(false)} onSave={saveMail} />
        </CompactModal>
      ) : null}
    </main>
  );
}

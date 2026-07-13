import { useEffect, useMemo, useRef } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Loader2,
  RotateCcw,
  Square,
  X,
} from "lucide-react";

const TERMINAL_STATUSES = new Set(["partial", "completed", "failed", "cancelled"]);

export function isCurrentWorkspaceTaskResponse({ requestSequence, currentSequence, requestWorkspaceId, currentWorkspaceId, aborted }) {
  return !aborted && requestSequence === currentSequence && requestWorkspaceId === currentWorkspaceId;
}

export function terminalTaskNotifications(tasks, previousSnapshot, hydrated, dismissed) {
  if (!hydrated) return [];
  return tasks.filter((task) => {
    const model = taskViewModel(task);
    return TERMINAL_STATUSES.has(model.status)
      && Boolean(model.notificationId)
      && previousSnapshot.get(model.taskId) !== model.notificationId
      && !dismissed.has(model.notificationId);
  });
}

export function taskViewModel(task = {}) {
  const status = String(task.status || "queued").toLowerCase();
  const isCancelling = status === "running" && Boolean(task.cancel_requested || task.cancel_requested_at);
  const result = task.result && typeof task.result === "object" ? task.result : {};
  const taskId = String(task.task_id || task.id || "");
  const updatedAt = String(task.updated_at || task.completed_at || task.created_at || "");
  const hasResult = Object.keys(result).length > 0;
  const destination = result.run_id || result.conversation_id
    ? "runs"
    : result.file_ids?.length || result.file_id || result.ingest_job_id
      ? "data"
      : result.artifact_job_id || result.artifact_url || result.artifact_urls
        ? "artifacts"
        : "";
  const isTerminal = ["partial", "completed", "failed", "cancelled"].includes(status);
  const hasError = status === "failed" || Boolean(task.error) || (Array.isArray(task.errors) && task.errors.length);
  const severity = status === "partial" ? "warning" : hasError ? "error" : status === "completed" ? "success" : status === "cancelled" ? "cancelled" : "progress";

  return {
    taskId,
    status,
    result,
    isTerminal,
    severity,
    canCancel: status === "queued" || (status === "running" && !isCancelling),
    isCancelling,
    statusLabel: isCancelling ? "正在停止" : "",
    canRetry: ["failed", "partial", "cancelled"].includes(status) && task.retryable === true,
    canOpenResult: hasResult,
    destination,
    notificationId: taskId && updatedAt ? `${taskId}:${status}:${updatedAt}` : "",
  };
}

export function createTaskCenterFocusController({ documentRef, getDrawer, getCloseButton, getBackground, onCloseRef }) {
  let returnFocus = null;
  let backgroundState = [];
  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onCloseRef.current?.();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...(getDrawer()?.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])];
    if (!focusable.length) return;
    const index = focusable.indexOf(documentRef.activeElement);
    const next = event.shiftKey ? (index <= 0 ? focusable.length - 1 : index - 1) : (index === focusable.length - 1 ? 0 : index + 1);
    event.preventDefault();
    focusable[next].focus();
  };
  return {
    open() {
      returnFocus = documentRef.activeElement;
      getCloseButton()?.focus();
      backgroundState = getBackground().map((node) => ({ node, inert: node.inert, ariaHidden: node.getAttribute("aria-hidden") }));
      backgroundState.forEach(({ node }) => {
        node.inert = true;
        node.setAttribute("aria-hidden", "true");
      });
      documentRef.addEventListener("keydown", onKeyDown);
      return () => {
        documentRef.removeEventListener("keydown", onKeyDown);
        backgroundState.forEach(({ node, inert, ariaHidden }) => {
          node.inert = inert;
          if (ariaHidden === null) node.removeAttribute("aria-hidden");
          else node.setAttribute("aria-hidden", ariaHidden);
        });
        returnFocus?.focus?.();
      };
    },
  };
}

export function TaskCenter({
  open,
  tasks = [],
  notifications = [],
  actions = {},
  onClose,
  onCancel,
  onRetry,
  onOpenResult,
  onDismissNotification,
}) {
  const closeButtonRef = useRef(null);
  const drawerRef = useRef(null);
  const layerRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const orderedTasks = useMemo(
    () => [...tasks].sort((left, right) => String(right.updated_at || right.created_at || "").localeCompare(String(left.updated_at || left.created_at || ""))),
    [tasks],
  );

  useEffect(() => {
    if (!open) return undefined;
    const controller = createTaskCenterFocusController({
      documentRef: document,
      getDrawer: () => drawerRef.current,
      getCloseButton: () => closeButtonRef.current,
      getBackground: () => {
        const shell = layerRef.current?.closest(".app-shell");
        return shell ? [...shell.children].filter((node) => node !== layerRef.current) : [];
      },
      onCloseRef,
    });
    return controller.open();
  }, [open]);

  if (!open && !notifications.length) return null;

  const statusLabel = (status) => ({
    preparing: "Preparing",
    queued: "Queued",
    running: "Running",
    cancel_requested: "Cancel requested",
    completed: "Completed",
    partial: "Partial result",
    failed: "Failed",
    cancelled: "Cancelled",
  }[status] || status);
  const titleFor = (task) => ({
    "workspace.ingest": "Ingest data",
    "analysis.ingest": "Prepare analysis data",
    "artifact.generate": "Generate artifacts",
    "connector.blob.import": "Import Blob data",
    "connector.sql.import": "Import SQL data",
  }[task.task_type] || task.action || task.task_type || "Background task");
  const iconFor = (model) => {
    if (model.status === "running" || model.status === "queued") return <Loader2 size={16} className="spin" />;
    if (model.status === "cancelled") return <X size={16} />;
    if (model.severity === "error" || model.severity === "warning") return <AlertTriangle size={16} />;
    return <CheckCircle2 size={16} />;
  };

  return (
    <>
      {notifications.length ? (
        <div className="task-toast-stack" aria-live="polite" aria-label="Task notifications">
          {notifications.map((task) => {
            const model = taskViewModel(task);
            return (
              <div className={`task-toast ${model.severity}`} key={model.notificationId}>
                <span>{iconFor(model)}</span>
                <div>
                  <strong>{titleFor(task)}</strong>
                  <small>{statusLabel(model.status)}</small>
                </div>
                {model.canOpenResult ? <button type="button" className="task-icon-button" title="Open result" aria-label="Open task result" onClick={() => onOpenResult(task)}><ArrowUpRight size={16} /></button> : null}
                <button type="button" className="task-icon-button" title="Dismiss notification" aria-label="Dismiss task notification" onClick={() => onDismissNotification(model.notificationId)}><X size={16} /></button>
              </div>
            );
          })}
        </div>
      ) : null}
      {open ? (
        <div ref={layerRef} className="task-center-layer" role="presentation" onMouseDown={onClose}>
          <aside ref={drawerRef} className="task-center-drawer" role="dialog" aria-modal="true" aria-label="Task center" onMouseDown={(event) => event.stopPropagation()}>
            <header className="task-center-head">
              <div><span>Operations</span><h2>Task center</h2></div>
              <button ref={closeButtonRef} type="button" className="task-icon-button" title="Close task center" aria-label="Close task center" onClick={onClose}><X size={18} /></button>
            </header>
            <div className="task-center-list">
              {orderedTasks.length ? orderedTasks.map((task) => {
                const model = taskViewModel(task);
                const action = actions[model.taskId] || {};
                return (
                  <article className={`task-center-row ${model.severity}`} key={model.taskId}>
                    <span className="task-center-status">{iconFor(model)}</span>
                    <div className="task-center-copy">
                      <strong>{titleFor(task)}</strong>
                      <span>{model.statusLabel || statusLabel(model.status)}{typeof task.progress === "number" ? ` · ${task.progress}%` : ""}</span>
                      {task.error?.message ? <small>{task.error.message}</small> : null}
                      {action.error ? <small className="task-action-error">{action.error}</small> : null}
                    </div>
                    <div className="task-center-actions">
                      {model.canOpenResult ? <button type="button" className="task-icon-button" title={`Open ${model.destination || "task"} result`} aria-label="Open task result" onClick={() => onOpenResult(task)}><ArrowUpRight size={16} /></button> : null}
                      {model.canCancel ? <button type="button" className="task-icon-button danger" title="Cancel task" aria-label="Cancel task" disabled={action.pending} onClick={() => onCancel(task)}>{action.pending ? <Loader2 size={16} className="spin" /> : <Square size={14} />}</button> : null}
                      {model.canRetry ? <button type="button" className="task-icon-button" title="Retry task" aria-label="Retry task" disabled={action.pending} onClick={() => onRetry(task)}>{action.pending ? <Loader2 size={16} className="spin" /> : <RotateCcw size={16} />}</button> : null}
                    </div>
                  </article>
                );
              }) : <p className="task-center-empty"><Clock3 size={18} />No server tasks recorded for this workspace.</p>}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}

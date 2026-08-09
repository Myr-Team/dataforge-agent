const GROUP_ORDER = ["identity", "data", "ai", "finops", "background_jobs"];

const STATUS_LABELS = Object.freeze({
  ready: "可用",
  degraded: "需关注",
  not_configured: "未配置",
  not_run: "尚未运行",
  stale: "需要更新",
  running: "运行中",
  failed: "运行失败",
  session_only: "临时保存",
});

const SAFE_DETAIL_KEYS = new Set([
  "role",
  "source",
  "state",
  "latency_ms",
  "observed_at",
  "status",
  "elapsed_ms",
  "configured",
  "persistence",
  "catalog_revision",
  "catalog_entries",
  "mapping_count",
  "connected",
  "governed",
  "scan_status",
  "rules_evaluated",
  "rules_triggered",
  "evidence_coverage_pct",
  "last_completed_at",
  "expected_interval_seconds",
  "rows_observed",
  "rows_written",
  "age_seconds",
  "source_freshness_at",
  "error_category",
]);


function cleanText(value, maximum = 120) {
  return typeof value === "string" ? value.trim().slice(0, maximum) : "";
}


function safeDetails(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([key, item]) => {
    if (!SAFE_DETAIL_KEYS.has(key)) return [];
    if (!["string", "number", "boolean"].includes(typeof item) && item !== null) return [];
    return [[key, typeof item === "string" ? item.slice(0, 160) : item]];
  }));
}


function itemView(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const status = Object.hasOwn(STATUS_LABELS, source.status) ? source.status : "degraded";
  return {
    key: cleanText(source.key, 64),
    label: cleanText(source.label, 80) || "服务项",
    status,
    statusLabel: STATUS_LABELS[status],
    ready: status === "ready",
    lastCompletedAt: cleanText(source.last_completed_at, 40),
    details: safeDetails(source.details),
  };
}


export function serviceReadinessView(payload) {
  const source = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const rawGroups = source.groups && typeof source.groups === "object" && !Array.isArray(source.groups)
    ? source.groups
    : {};
  const groups = GROUP_ORDER.flatMap((id) => {
    const group = rawGroups[id];
    if (!group || typeof group !== "object" || Array.isArray(group)) return [];
    const items = Array.isArray(group.items) ? group.items.map(itemView) : [];
    return [{ id, label: cleanText(group.label, 80) || id, items }];
  });
  const items = groups.flatMap((group) => group.items);
  return {
    generatedAt: cleanText(source.generated_at, 40),
    groups,
    summary: {
      total: items.length,
      ready: items.filter((item) => item.ready).length,
      attention: items.filter((item) => !item.ready).length,
    },
  };
}


export const serviceReadinessStatusLabels = STATUS_LABELS;

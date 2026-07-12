function formatTokenCount(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return "0";
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
  return String(Math.round(num));
}

export function deriveGovernanceUsageView(usage) {
  const value = usage?.total_tokens;
  const unknownRuns = Number(usage?.unknown_usage_runs || 0);
  const knownRuns = Number(usage?.known_usage_runs || 0);
  if (value === null || value === undefined) {
    return { tokenText: "未记录", tokenLabel: "未记录", coverage: "unknown", knownRuns, unknownRuns };
  }
  const formatted = formatTokenCount(value);
  const partial = usage?.usage_status === "partial" || unknownRuns > 0;
  const tokenText = partial ? `${formatted}（部分已记录）` : formatted;
  const tokenLabel = partial ? `${formatted} tokens（部分已记录）` : `${formatted} tokens`;
  return { tokenText, tokenLabel, coverage: partial ? "partial" : "complete", knownRuns, unknownRuns };
}

export function formatGovernanceTokens(usage) {
  return deriveGovernanceUsageView(usage).tokenText;
}

export function formatGovernanceTokenLabel(usage) {
  return deriveGovernanceUsageView(usage).tokenLabel;
}

import assert from "node:assert/strict";
import test from "node:test";

import { monitoringSnapshotViewModel } from "./monitoringViewModel.js";

test("configured gateway stays unverified until a governed call is observed", () => {
  assert.deepEqual(
    monitoringSnapshotViewModel({
      evidence_source: "run_store",
      usage: { status: "partial", total_tokens: 150, input_tokens: 100, output_tokens: 50, known_runs: 1, unknown_runs: 1 },
      gateway: { state: "configured_unverified", governed_calls: null, provenance: "apim_correlation_pending" },
      reliability: { completed_runs: 2, failed_runs: 0, audit_events: 3, provenance: "run_store_and_audit_store" },
      models: { state: "available", default_route: "primary-analysis", routes: [{ id: "primary-analysis", deployment: "gpt-5.1" }] },
    }),
    {
      evidenceSource: "run_store",
      tokenLabel: "150",
      tokenState: "partial",
      inputLabel: "100",
      outputLabel: "50",
      knownRuns: 1,
      unknownRuns: 1,
      gateway: {
        state: "configured_unverified",
        label: "网关已配置，待验证实际调用",
        tone: "warn",
        governedCalls: null,
      },
      reliability: { completedRuns: 2, failedRuns: 0, auditEvents: 3 },
      models: { state: "available", defaultRoute: "primary-analysis", routeCount: 1, label: "gpt-5.1" },
    },
  );
});

test("unconfigured monitoring never invents token or gateway evidence", () => {
  const view = monitoringSnapshotViewModel({
    usage: { status: "unknown", total_tokens: null, input_tokens: null, output_tokens: null, known_runs: 0, unknown_runs: 0 },
    gateway: { state: "not_configured", governed_calls: null },
    reliability: { completed_runs: 0, failed_runs: 0, audit_events: 0 },
  });

  assert.equal(view.tokenLabel, "未记录");
  assert.equal(view.gateway.label, "网关未启用");
  assert.equal(view.gateway.governedCalls, null);
});

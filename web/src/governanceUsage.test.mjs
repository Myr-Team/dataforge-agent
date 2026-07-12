import assert from "node:assert/strict";
import test from "node:test";

import { deriveGovernanceUsageView, formatGovernanceTokenLabel, formatGovernanceTokens } from "./governanceUsage.js";

test("unknown governance usage is displayed as unrecorded", () => {
  assert.equal(formatGovernanceTokens({
    total_tokens: null,
    known_usage_runs: 0,
    unknown_usage_runs: 2,
    usage_status: "unknown",
  }), "未记录");
});

test("mixed governance usage identifies the known total as partial", () => {
  const view = deriveGovernanceUsageView({
    total_tokens: 1200,
    known_usage_runs: 1,
    unknown_usage_runs: 1,
    usage_status: "partial",
  });
  assert.equal(view.tokenText, "1.2K（部分已记录）");
  assert.equal(view.tokenLabel, "1.2K tokens（部分已记录）");
  assert.equal(view.coverage, "partial");
  assert.equal(view.knownRuns, 1);
  assert.equal(view.unknownRuns, 1);
});

test("unknown token labels never append a misleading token value", () => {
  assert.equal(formatGovernanceTokenLabel({ total_tokens: null, usage_status: "unknown" }), "未记录");
});

test("known measured zero remains zero", () => {
  assert.equal(formatGovernanceTokens({
    total_tokens: 0,
    known_usage_runs: 1,
    unknown_usage_runs: 0,
    usage_status: "complete",
  }), "0");
});

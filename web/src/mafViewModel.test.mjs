import assert from "node:assert/strict";
import test from "node:test";

import { deriveMafViewModel, mafStatusTone } from "./mafViewModel.js";

const COORDINATOR = "df-coordinator";
const CORPUS = "df-corpus-analyst";
const MARKET = "df-market-researcher";
const FEASIBILITY = "df-feasibility-analyst";
const AUDITOR = "df-auditor";
const PRODUCER = "df-producer";

test("live maf_plan renders its mode and full roster before agents start", () => {
  const persisted = {
    mode: "direct",
    selected_agents: [COORDINATOR],
    skipped_agents: [CORPUS, MARKET],
    max_revisions: 0,
  };
  const trace = [{
    event: "maf_plan",
    data: {
      status: "completed",
      mode: "concurrent_research",
      pattern: "concurrent_research",
      selected_agents: [CORPUS, MARKET],
      skipped_agents: [COORDINATOR, FEASIBILITY, AUDITOR, PRODUCER],
      max_revisions: 2,
      reason_codes: ["workspace_evidence_required", "external_signal_required"],
    },
  }];

  const model = deriveMafViewModel(trace, persisted);

  assert.equal(model.mode, "concurrent_research");
  assert.deepEqual(model.selectedAgents, [CORPUS, MARKET]);
  assert.deepEqual(model.agents.map((agent) => agent.id), [CORPUS, MARKET]);
  assert.deepEqual(model.skippedAgents, [COORDINATOR, FEASIBILITY, AUDITOR, PRODUCER]);
  assert.equal(model.maxRevisions, 2);
  assert.deepEqual(model.reasonCodes, ["workspace_evidence_required", "external_signal_required"]);
});

test("trace facts override persisted summary and expose failure collaboration details", () => {
  const persisted = {
    mode: "direct",
    selected_agents: [COORDINATOR],
    skipped_agents: [FEASIBILITY],
    agents: [
      { agent_id: COORDINATOR, status: "completed", metadata: { duration_ms: 777 } },
      { agent_id: FEASIBILITY, status: "completed", metadata: { duration_ms: 888 } },
    ],
  };
  const trace = [
    {
      event: "maf_plan",
      mode: "direct",
      detail: {
        mode: "specialist_handoff",
        selected_agents: [COORDINATOR, FEASIBILITY],
        skipped_agents: [CORPUS, MARKET, AUDITOR, PRODUCER],
        max_revisions: 2,
      },
    },
    { event: "maf_agent_started", duration_ms: 900, detail: { agent_id: COORDINATOR, status: "running" } },
    { event: "maf_agent_completed", duration_ms: 700, detail: { agent_id: COORDINATOR, status: "completed", duration_ms: 12 } },
    { event: "maf_branch_started", duration_ms: 800, detail: { agent_id: FEASIBILITY, branch_id: "analysis", status: "running", duration_ms: 300 } },
    { event: "maf_agent_failed", duration_ms: 600, detail: { agent_id: FEASIBILITY, status: "failed", error_category: "transient" } },
    { event: "maf_branch_joined", duration_ms: 500, detail: { agent_id: FEASIBILITY, branch_id: "analysis", status: "failed", duration_ms: 25, error_category: "transient" } },
    { event: "maf_handoff", detail: { source_agent_id: COORDINATOR, target_agent_id: FEASIBILITY, status: "completed", reason_codes: ["intent:feasibility_analysis"] } },
    { event: "maf_review", duration_ms: 400, detail: { agent_id: AUDITOR, revision: 0, status: "running", duration_ms: 90 } },
    { event: "maf_review", duration_ms: 300, detail: { agent_id: AUDITOR, revision: 0, status: "failed", verdict: "revise", error_category: "contract_validation", duration_ms: 80 } },
    { event: "maf_fallback", detail: { status: "recorded", error_category: "permanent" } },
  ];

  const model = deriveMafViewModel(trace, persisted);
  const coordinator = model.agents.find((agent) => agent.id === COORDINATOR);
  const feasibility = model.agents.find((agent) => agent.id === FEASIBILITY);

  assert.equal(model.mode, "specialist_handoff");
  assert.deepEqual(model.selectedAgents, [COORDINATOR, FEASIBILITY, AUDITOR]);
  assert.equal(coordinator.durationMs, 12);
  assert.equal(coordinator.status, "completed");
  assert.equal(feasibility.durationMs, null);
  assert.equal(feasibility.status, "failed");
  assert.deepEqual(model.branches, [{ id: "analysis", agentId: FEASIBILITY, required: undefined, status: "failed", durationMs: 25, error: "transient" }]);
  assert.equal(model.handoffs.length, 1);
  assert.equal(model.handoffs[0].target, FEASIBILITY);
  assert.deepEqual(model.reviews.map(({ round, status, verdict }) => ({ round, status, verdict })), [{ round: 1, status: "failed", verdict: "revise" }]);
  assert.equal(model.fallback.error_category, "permanent");
});

test("agent latency sums completed-event detail only and ignores inferred intervals", () => {
  const trace = [
    { event: "maf_plan", data: { mode: "direct", selected_agents: [COORDINATOR], skipped_agents: [] } },
    {
      event: "maf_agent_completed",
      duration_ms: 1000,
      detail: {
        agent_id: COORDINATOR,
        status: "completed",
        duration_ms: 11,
        input_tokens: 12,
        output_tokens: 5,
        total_tokens: 17,
      },
    },
    { event: "maf_branch_joined", duration_ms: 900, detail: { agent_id: COORDINATOR, branch_id: "ignored", status: "completed", duration_ms: 51 } },
    { event: "maf_review", duration_ms: 800, detail: { agent_id: COORDINATOR, revision: 0, status: "completed", duration_ms: 63 } },
    { event: "maf_agent_completed", duration_ms: 700, data: { agent_id: COORDINATOR, status: "completed", duration_ms: 13 } },
  ];

  const model = deriveMafViewModel(trace);

  assert.equal(model.agents[0].durationMs, 24);
  assert.deepEqual(model.agents[0].tokens, {
    input_tokens: 12,
    output_tokens: 5,
    total_tokens: 17,
  });
  assert.equal(model.agents[0].tone, "completed");
});

test("persisted summary remains usable when dynamic trace facts are absent", () => {
  const model = deriveMafViewModel([], {
    mode: "bounded_review",
    selected_agents: [FEASIBILITY, AUDITOR],
    skipped_agents: [COORDINATOR, CORPUS, MARKET, PRODUCER],
    max_revisions: 2,
    agents: [{ agent_id: FEASIBILITY, status: "failed", metadata: { duration_ms: 31 } }],
    fallback: { status: "recorded", error_category: "transient" },
  });

  assert.equal(model.mode, "bounded_review");
  assert.equal(model.agents[0].durationMs, 31);
  assert.equal(model.agents[0].tone, "failed");
  assert.equal(model.maxRevisions, 2);
  assert.equal(model.fallback.error_category, "transient");
});

test("status tones are truthful and legacy maf_workflow remains outside the dynamic model", () => {
  assert.equal(mafStatusTone("completed"), "completed");
  assert.equal(mafStatusTone("running"), "running");
  assert.equal(mafStatusTone("failed"), "failed");
  assert.equal(mafStatusTone("unknown"), "neutral");
  assert.equal(deriveMafViewModel([{ event: "maf_workflow", data: { pattern: "conditional" } }]), null);
});

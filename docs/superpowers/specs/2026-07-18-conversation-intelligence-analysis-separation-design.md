# DataForge Conversation Intelligence 2.0: Analysis And Conversation Separation

## Goal

Make automatic analysis feel like an autonomous workspace capability instead of a synthetic chat turn. A workspace analysis is a task and a run that produces a versioned analysis result. A conversation exists only after a person sends a message. The two can reference each other without sharing an identity or impersonating one another.

This is the first delivery slice of Conversation Intelligence 2.0. It intentionally focuses on identity and persistence boundaries before changing prompts, routing quality, or multi-agent collaboration.

## Current Problem

The current automatic-analysis endpoint constructs a `ChatRequest` and calls the chat orchestrator. The orchestrator then:

1. creates or reuses a `conversation_id`;
2. uses that identifier as the run identifier;
3. emits a synthetic user event;
4. persists the automatic-analysis prompt as a user message;
5. persists the generated answer as an assistant message;
6. returns the conversation identifier to the frontend, which selects it as the active conversation.

This implementation preserves one execution path, but it creates the wrong product model. The conversation list contains activity the user did not initiate, automatic analysis looks like a hidden chat prompt, and run, version, artifact, and conversation lineage become difficult to explain.

## Product Principles

- A task represents requested work and its lifecycle.
- A run represents one execution attempt and its technical evidence.
- An analysis version represents the durable workspace conclusion produced by a successful analysis run.
- A conversation represents messages exchanged by people and the assistant.
- These records use distinct identifiers and explicit references.
- The product never creates a user message unless a person actually submitted it.
- Automatic analysis remains fully traceable through tasks, runs, audit events, and analysis versions.
- Existing records are preserved. Compatibility cleanup must not delete conversation or run evidence.

## Chosen Approach

Implement strict separation in the backend and expose it through an additive API contract. Do not rely on frontend-only filtering.

Two alternatives were rejected:

- Keeping a dedicated system conversation still mixes autonomous work with human discussion.
- Hiding automatic-analysis conversations only in the UI leaves incorrect persistence, search, usage, audit, and multi-user attribution semantics.

## Domain Model

### Execution Context

Introduce a typed execution context passed into orchestration:

```text
run_id: required unique execution identifier
workspace_id: required workspace identifier
origin: conversation | workspace_auto_analysis | data_send_analysis
conversation_id: optional human conversation identifier
persist_messages: true only for origin=conversation
task_id: optional background task identifier
source_run_id: optional parent analysis run
```

The backend, not the frontend, owns these invariants:

- `origin=conversation` requires `conversation_id` and permits message persistence.
- automatic-analysis origins must not persist user or assistant messages.
- `run_id` never defaults to `conversation_id`.
- a conversation-triggered deep analysis has both identifiers: the conversation keeps the human exchange while the linked run owns execution evidence.

### Record Relationships

```text
Workspace
  -> Task (optional)
      -> Run
          -> Analysis Version
          -> Artifacts
          -> Trace / audit evidence

Workspace
  -> Conversation
      -> Human and assistant messages
      -> linked_run_ids[] when a message triggers analysis
```

## Backend Behavior

### Automatic Analysis

`POST /api/workspaces/{workspace_id}/auto-analyze` creates a task when asynchronous execution is requested, always creates a new `run_id`, and executes with `origin=workspace_auto_analysis`.

It records `analysis.run` audit activity but does not record `message.create`. It does not create or update a conversation. Its response contains:

```json
{
  "workspace_id": "...",
  "run_id": "...",
  "conversation_id": null,
  "analysis_version_id": "...",
  "status": "completed"
}
```

The synthetic instruction used internally to define the analysis goal is stored as bounded run metadata such as `goal` or `trigger`, never as a user message.

### Data Workbench: Send To Analysis

Sending a file or imported connector table to analysis uses `origin=data_send_analysis`. The selected asset versions and connector lineage are attached to the run. No conversation is created. The frontend navigates to the workspace Agent Flow using `run_id`.

### Human Conversation

The first real user message creates a conversation. Follow-up messages reuse that conversation. Lightweight answers remain inside the conversation and may also create a run for observability, but the run identifier remains separate.

When a human asks for deep reanalysis, the submitted message remains in the conversation, a linked analysis run is created, and the assistant response includes a task/result reference. The conversation stores `linked_run_ids`; the run stores `conversation_id` as an optional foreign reference.

### Latest Analysis And Artifact Generation

Latest-analysis records expose `run_id` and `analysis_version_id`. Artifact generation uses `source_run_id` or `analysis_version_id` as its canonical source. `conversation_id` is optional attribution context and cannot be the only source identifier.

### Orchestrator Boundary

Refactor the current chat-only entrypoint into a shared execution core with two adapters:

- conversation adapter: emits and persists user/assistant message events;
- analysis adapter: emits task/run/analysis events without message persistence.

The shared core keeps content safety, routing, evidence retrieval, MAF execution, audit/revision, answer composition, and analysis persistence. This avoids duplicating intelligence while enforcing correct product semantics at the adapters.

## Event Contract

The `ready` SSE event becomes additive:

```json
{
  "run_id": "run_...",
  "conversation_id": null,
  "workspace_id": "...",
  "origin": "workspace_auto_analysis"
}
```

Conversation executions return both identifiers. Existing clients may continue reading `conversation_id`, but analysis clients must use `run_id`. The frontend must never set the active conversation from an event whose `origin` is not `conversation`.

## Frontend Experience

- Clicking automatic analysis keeps the user on the workspace and shows task/Agent Flow progress.
- Completion updates the workspace result and run history without adding a conversation.
- Entering the conversation page after analysis shows a non-message context banner: `已加载工作区最新分析 vN`.
- The banner links to the source run and analysis version but is not copied into message history.
- The conversation list contains only conversations with at least one human-submitted message.
- A deep-analysis request inside a conversation renders as a task card associated with the initiating message and final assistant response.

## Existing Data Compatibility

No historical record is deleted.

New records receive explicit `origin`, `run_id`, and optional `conversation_id` fields. Historical automatic-analysis conversations may be classified as `system_activity` only when all of the following are true:

- the corresponding run contains a trusted automatic-analysis entrypoint marker;
- every stored user message matches a known internal automatic-analysis request from that run;
- no later human-authored turn exists.

Confident matches are excluded from the default conversation list but remain retrievable through run history and audit evidence. Ambiguous records remain visible. The compatibility classifier is idempotent and reports counts; it does not rewrite or delete source blobs during initial rollout.

## Error Handling

- A failed automatic analysis updates task and run status and emits a nonblocking workspace notification.
- A failure cannot create an empty conversation.
- Partial model output remains attached to the run when safe, but is not promoted to a completed analysis version.
- A conversation-triggered analysis failure leaves the human message intact and adds a recoverable task failure card.
- Persistence failures remain explicit; the system cannot report completion when the run or required audit evidence is unavailable.

## Observability And Audit

Every execution records `run_id`, `origin`, optional `conversation_id`, task ID, actor, selected agents, tools, token usage, latency, cache status, evidence fingerprint, and terminal status.

Audit semantics are separated:

- automatic analysis: `analysis.run`, task transitions, result/version publication;
- human message: `message.create` plus any linked `analysis.run`;
- no automatic path emits a fabricated human action.

## Evaluation And Acceptance

### Automated Contracts

- Automatic analysis creates a run and analysis version without creating a conversation.
- Automatic analysis does not emit or audit `message.create`.
- Data Workbench analysis follows the same separation.
- A first human message creates exactly one conversation.
- A conversation-triggered deep analysis produces distinct `conversation_id` and `run_id` values with reciprocal links.
- Latest analysis and artifact generation resolve canonical source runs without depending on a conversation ID.
- Historical compatibility classification never hides an ambiguous or subsequently human-used conversation.
- SSE events remain backward compatible and expose `origin` and `run_id`.

### Runtime Acceptance

1. Start automatic analysis in a clean workspace.
2. Observe Agent Flow and a completed run.
3. Confirm the conversation list is unchanged.
4. Open the conversation page and confirm only the latest-analysis context banner appears.
5. Send a real message and confirm a conversation is created at that point.
6. Ask for deep reanalysis and confirm the conversation links to a distinct run.
7. Refresh and verify all records and links remain stable.
8. Verify run trace, token usage, audit events, artifacts, and analysis version lineage.

## Rollout

1. Add the additive identifiers and execution context behind `DF_SEPARATE_ANALYSIS_CONVERSATIONS`.
2. Update backend adapters and persistence contracts.
3. Update the frontend to consume `run_id` for automatic analysis and ignore non-conversation IDs.
4. Run unit, contract, build, and browser end-to-end verification.
5. Deploy a zero-traffic backend candidate, then a matching frontend candidate.
6. Validate with a signed-in production-equivalent workspace.
7. Enable the flag for the candidate, then production.
8. Keep the previous revisions available for rollback.

## Out Of Scope

- Foundry ROI and Key Vault integration.
- Prompt redesign and response-style tuning beyond what is required for the new adapters.
- Replacing the existing MAF runtime.
- Destructive migration of historical conversations.
- Changing authentication or Easy Auth configuration.

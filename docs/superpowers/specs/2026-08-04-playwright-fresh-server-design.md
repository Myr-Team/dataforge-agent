# Playwright Fresh Server Gate Design

## Goal

Ensure local and CI acceptance tests never silently validate a Vite preview from another worktree or an older revision.

## Decision

- Default `webServer.reuseExistingServer` to `false`.
- Allow reuse only when `DF_PLAYWRIGHT_REUSE_SERVER=1` is explicitly supplied.
- Keep `DF_PLAYWRIGHT_PORT` as the port-isolation mechanism.
- Keep the loopback `NO_PROXY` protection.
- Ignore `web/test-results/` as generated output. Do not broadly ignore workspace fixtures.

## Acceptance

- A Node contract test must fail against the current unconditional reuse configuration.
- The contract test must pass after reuse becomes explicit opt-in.
- Python, Node, Vite and Playwright suites must pass on the updated branch.
- Playwright must run against a newly started preview on an isolated port.
- The pull request may be merged only after the full verification gate passes.
- This change does not deploy or switch production traffic.

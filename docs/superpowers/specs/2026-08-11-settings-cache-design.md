# Settings Cache and Re-entry Performance Design

## Goal

Make repeat navigation into Settings render the most recent authorized data immediately and refresh it in the background. Cloudflare/CDN caching is not part of this change.

## Current failure

`SettingsCenter` unmounts whenever the primary view changes. A new mount starts unconditional reads for budgets, notifications, alerts, system status, workspace members, workspace settings, and model routing. Child pages repeat several of the same reads. Component cleanup suppresses late `setState` but does not abort or deduplicate requests.

## Safe caching model

Add a settings-specific in-memory store; do not persist member, permission, provider, budget, notification, or identity data in local storage or shared caches. Each entry is keyed by an opaque authorization boundary plus workspace, resource, query, and schema revision. The boundary incorporates tenant/session actor/effective permissions without storing raw identity in the key.

An entry contains the last successful value, timestamp, in-flight promise, last refresh error, and abort controller. Fresh entries are returned without a network request. Stale-but-usable values render immediately while one shared revalidation runs. Failed revalidation retains the prior value. Clearing an authorization scope aborts and evicts only that scope.

Default policy:

- normal setting summaries: 30 seconds fresh, 5 minutes stale-usable;
- service readiness: 15 seconds fresh, 60 seconds stale-usable;
- no background refresh after the stale-usable window without a visible loading state.

## Loading changes

- Use the workspace settings response as the first system-status snapshot instead of issuing a separate status GET on mount.
- Load the full members/permission contract only when the members tab or a member action is opened.
- Share budget, notification, alert, model-routing, provider, identity, and readiness entries between Settings home and child pages.
- Preload the bounded Settings home resources on Settings navigation hover/focus/idle.
- A manual refresh forces one revalidation but still preserves the prior snapshot until success.

## Writes and invalidation

Writes invalidate only their resource family within the current scope. A revision conflict does not clear unrelated successful snapshots. Logout, tenant/workspace change, or effective-permission change clears the old authorization scope before any old response can render.

## Acceptance

- First entry displays a skeleton only for resources without a successful snapshot.
- Repeat entry within the fresh window is interactive within 200ms and issues no duplicate GET for the same key.
- Concurrent home/child consumers share one in-flight promise.
- A failed refresh leaves the previous data visible with an update error.
- Different actors, tenants, workspaces, or permission fingerprints never share an entry.
- Cloudflare and public/shared API caching remain disabled.

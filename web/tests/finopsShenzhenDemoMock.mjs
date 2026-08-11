import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { installFinOpsDemoCompletenessApi } from "./finopsMockApi.mjs";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(testDirectory, "../..");
const projection = JSON.parse(execFileSync(
  process.env.DATAFORGE_PYTHON || "python",
  ["-m", "backend.finops.synthetic_demo_projection"],
  { cwd: repositoryRoot, encoding: "utf8", maxBuffer: 2 * 1024 * 1024 },
));

export const SHENZHEN_REFS = Object.freeze(projection.refs);
export const SHENZHEN_POLICY_REFS = Object.freeze(projection.policy_refs);
export const SHENZHEN_SUMMARY = Object.freeze(projection.summary);
export const SHENZHEN_CANONICAL_DIGEST = projection.canonical_digest;

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function requestList(endpoints) {
  return {
    ...endpoints.bootstrap,
    count: projection.summary.requests,
    next_cursor: null,
    items: Object.entries(endpoints.requests).slice(0, 8).map(([requestRef, detail]) => ({
      request_ref: requestRef,
      occurred_at: detail.display?.occurred_at,
      workspace_id: "demo-corpus",
      status: detail.status,
      model: detail.metrics?.estimated_cost?.official_price_key || "unpriced",
      estimated_cost: detail.metrics?.estimated_cost,
    })),
  };
}

export async function installFinOpsShenzhenDemoApi(page, calls = []) {
  await installFinOpsDemoCompletenessApi(page, calls);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathName = new URL(request.url()).pathname;
    const bodyText = request.postData() || "";
    calls.push({ path: pathName, method: request.method(), body: bodyText });
    const endpoints = projection.endpoints;

    if (pathName === "/api/workspaces/demo-corpus/dashboard") return json(route, endpoints.dashboard);
    const runMatch = pathName.match(/^\/api\/runs\/([^/]+)\/(summary|trace)$/);
    if (runMatch?.[2] === "summary" && endpoints.run_summaries[runMatch[1]]) {
      return json(route, endpoints.run_summaries[runMatch[1]]);
    }
    if (runMatch?.[2] === "trace" && endpoints.traces[runMatch[1]]) {
      return json(route, endpoints.traces[runMatch[1]]);
    }
    if (pathName === "/api/finops/bootstrap") return json(route, endpoints.bootstrap);
    if (pathName === "/api/finops/breakdowns") return json(route, endpoints.breakdowns);
    if (pathName === "/api/finops/agents") return json(route, endpoints.agents);
    if (pathName === "/api/finops/roi/decision") return json(route, endpoints.roi);
    if (pathName === "/api/finops/risk/decision") return json(route, endpoints.risk);
    if (pathName === "/api/finops/risk/scans/latest") return json(route, endpoints.risk_scan);
    if (pathName === "/api/finops/risk/scans" && request.method() === "GET") {
      return json(route, endpoints.risk_scan_history);
    }
    if (pathName === "/api/finops/risk/scans" && request.method() === "POST") {
      return json(route, endpoints.risk_scan, 201);
    }
    if (pathName === `/api/finops/risk/scans/${endpoints.risk_scan.scan_ref}`) {
      return json(route, endpoints.risk_scan);
    }
    if (pathName === "/api/finops/requests") return json(route, requestList(endpoints));
    const requestMatch = pathName.match(/^\/api\/finops\/requests\/(req_[A-Za-z0-9_-]+)$/);
    if (requestMatch && endpoints.requests[requestMatch[1]]) {
      return json(route, { ...endpoints.bootstrap, ...endpoints.requests[requestMatch[1]] });
    }
    if (pathName === "/api/finops/pricing/catalog") return json(route, { ...endpoints.bootstrap, ...endpoints.pricing });
    if (pathName === "/api/finops/pricing/mappings") return json(route, { ...endpoints.bootstrap, ...endpoints.price_mappings });
    if (pathName === "/api/finops/assistant/query") {
      let policyType = "unpriced_requests";
      try {
        policyType = JSON.parse(bodyText).metric_context?.policy_type || policyType;
      } catch {
        // Invalid JSON remains on a safe deterministic fallback response.
      }
      return json(route, projection.assistant_by_policy[policyType] || projection.assistant_by_policy.unpriced_requests);
    }
    return route.fallback();
  });
}

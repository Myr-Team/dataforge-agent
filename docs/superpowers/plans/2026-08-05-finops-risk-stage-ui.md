# FinOps Risk Stage UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the misleading connector line from the selected-risk stage summary, make the four stages compact and responsive, and replace visible internal FinOps enums with customer-readable Chinese labels.

**Architecture:** Keep the backend contract and risk decision data flow unchanged. Normalize the small set of approved technical terms in `finopsDecisionViewModel.js`, then render the existing four stages as independent status summaries in `RiskDecisionPage.jsx`; CSS owns density and breakpoints without connector pseudo-elements.

**Tech Stack:** React, plain CSS, Node test runner, Vite SSR tests, Playwright.

## Global Constraints

- The stage numbers `1` through `4` remain, but no line or replacement connector may appear between them.
- Desktop uses four equal columns, widths below `980px` use two columns, and widths below `640px` use one column.
- `gateway_coverage`, `app_observed`, `unmanaged`, and `unknown` must not appear in the primary customer-facing risk view.
- Backend APIs, evidence association, risk rules, authentication, Entra, and governance action flags are unchanged.
- Unknown values remain honest and are not converted into observed or verified states.

---

### Task 1: Normalize customer-facing risk evidence text

**Files:**
- Modify: `web/src/finopsDecisionViewModel.js:1-40, 580-605, 680-708`
- Test: `web/src/finopsDecisionViewModel.test.mjs:450-510`

**Interfaces:**
- Consumes: raw priority recommendations, evidence signal metrics, and error categories returned by `/api/finops/risk/decision`.
- Produces: the existing `riskDecisionView(payload)` shape with localized `priorities[].summary`, `evidence[].signal.metric`, and `evidence[].errorCategory` strings.

- [ ] **Step 1: Write a failing view-model test**

Add a `riskDecisionView` fixture whose recommendation contains `app_observed`, `unmanaged`, and `unknown`, and whose evidence uses `gateway_coverage` plus `provider_5xx`:

```js
test("risk decision view localizes internal evidence terms for customer-facing cards", () => {
  const view = riskDecisionView({
    priorities: [{
      opportunity_id: "opp-coverage",
      policy_type: "apim_coverage",
      risk_domain: "governance",
      recommendation: "定位 app_observed、unmanaged 或 unknown 调用链。",
      impact: "high",
      confidence: "high",
      effort: "medium",
      evidence_refs: ["req_coverage_001"],
    }],
    selected_evidence_summaries: [{
      request_ref: "req_coverage_001",
      signal: { metric: "gateway_coverage", value: null, unit: "" },
      status: "failed",
      error_category: "provider_5xx",
    }],
  });

  assert.equal(view.priorities[0].summary, "定位应用侧已观测、未纳入统一入口或来源待确认调用链。");
  assert.equal(view.evidence[0].signal.metric, "入口治理覆盖");
  assert.equal(view.evidence[0].errorCategory, "模型服务异常");
  assert.doesNotMatch(JSON.stringify(view), /gateway_coverage|app_observed|unmanaged|unknown|provider_5xx/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run from `web/`:

```powershell
node --test src/finopsDecisionViewModel.test.mjs --test-name-pattern "localizes internal evidence terms"
```

Expected: FAIL because the existing view model returns raw internal terms.

- [ ] **Step 3: Add the minimal display-only normalization**

Add a closed mapping and apply it only to the three customer-facing fields:

```js
const CUSTOMER_FACING_FINOPS_TERMS = Object.freeze({
  gateway_coverage: "入口治理覆盖",
  app_observed: "应用侧已观测",
  unmanaged: "未纳入统一入口",
  unknown: "来源待确认",
  cache_state: "缓存状态",
  tokens_total: "Token 总量",
  provider_5xx: "模型服务异常",
});

function customerFacingFinOpsText(value, maximum) {
  const text = boundedText(value, maximum);
  return text.replace(
    /gateway_coverage|app_observed|unmanaged|unknown|cache_state|tokens_total|provider_5xx/g,
    (term) => CUSTOMER_FACING_FINOPS_TERMS[term] || term,
  );
}
```

Use `customerFacingFinOpsText` for `safePriorities(...).summary`, `safeEvidenceSummaries(...).signal.metric`, and `safeEvidenceSummaries(...).errorCategory`. Do not change identifiers, policy types, evidence refs, or technical detail values.

- [ ] **Step 4: Run the focused and complete view-model tests**

```powershell
node --test src/finopsDecisionViewModel.test.mjs --test-name-pattern "localizes internal evidence terms"
node --test src/finopsDecisionViewModel.test.mjs
```

Expected: both commands PASS.

- [ ] **Step 5: Commit the view-model change**

```powershell
git add web/src/finopsDecisionViewModel.js web/src/finopsDecisionViewModel.test.mjs
git commit -m "fix(finops): localize risk evidence terms"
```

---

### Task 2: Replace the connected stepper with independent compact summaries

**Files:**
- Modify: `web/src/finops/RiskDecisionPage.jsx:253-288`
- Modify: `web/src/styles.css:6276-6285, 6414-6443`
- Test: `web/tests/finops-operations-management.spec.mjs:430-445`

**Interfaces:**
- Consumes: the existing selected priority, selected request evidence, and four derived stage values.
- Produces: an ordered list named `治理判断阶段` containing four independent `<li data-state>` summaries; no pseudo-element connector.

- [ ] **Step 1: Write a failing Playwright assertion for the production defect**

Extend the risk-page acceptance block:

```js
const stageList = page.getByRole("list", { name: "治理判断阶段" });
const stageItems = stageList.locator("li");
await expect(stageItems).toHaveCount(4);
const connectors = await stageItems.evaluateAll((nodes) => nodes.map((node) => (
  getComputedStyle(node, "::after").content
)));
expect(connectors).toEqual(["none", "none", "none", "none"]);
await expectNoOverlap(stageItems);
const stageBox = await stageList.boundingBox();
expect(stageBox.height).toBeLessThanOrEqual(86);
```

Also assert that the visible risk chain text does not contain the mapped raw terms:

```js
await expect(page.locator(".finops-decision-risk-chain")).not.toContainText(
  /gateway_coverage|app_observed|unmanaged|unknown|provider_5xx/,
);
```

- [ ] **Step 2: Run the focused Playwright test and verify RED**

```powershell
$env:DF_PLAYWRIGHT_PORT="5217"
npx playwright test tests/finops-operations-management.spec.mjs --grep "demo workspace exposes complete"
```

Expected: FAIL because the current `::after` connector reports a quoted empty string and the list has no accessible name.

- [ ] **Step 3: Render explicit stage states without a connector structure**

Change the tuple list to objects so visual state is explicit:

```jsx
const stages = [
  { id: "signal", label: "信号", value: "已识别", note: `${priority.policyLabel} · ${priority.domainLabel}`, state: "observed" },
  { id: "impact", label: "影响范围", value: priority.sampleCount === null ? "未记录" : `${priority.sampleCount} 次请求`, note: `业务影响 ${priority.impactLevelLabel}`, state: priority.sampleCount === null ? "partial" : "observed" },
  { id: "evidence", label: "代表证据", value: selectedEvidence.length ? `${selectedEvidence.length} 条请求证据` : "暂无可下钻请求", note: "仅请求证据可打开详情", state: selectedEvidence.length ? "observed" : "partial" },
  { id: "verification", label: "改善验证", value: "待验证", note: "保存整改草案后复核", state: "pending" },
];
```

Render them with an accessible list name and state attribute:

```jsx
<ol className="finops-decision-risk-chain-stages" aria-label="治理判断阶段">
  {stages.map((stage, index) => (
    <li key={stage.id} data-state={stage.state}>
      <span aria-hidden="true">{index + 1}</span>
      <div><small>{stage.label}</small><b>{stage.value}</b><p>{stage.note}</p></div>
    </li>
  ))}
</ol>
```

- [ ] **Step 4: Implement compact responsive CSS and delete the connector rule**

Replace the stage rules with independent cards and do not define `li::after`:

```css
.finops-decision-risk-chain-stages { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0; padding: 10px 14px 12px; list-style: none; }
.finops-decision-risk-chain-stages > li { display: grid; min-width: 0; min-height: 58px; grid-template-columns: 24px minmax(0, 1fr); align-items: start; gap: 8px; border: 1px solid var(--line); border-radius: 8px; background: #fbfcfe; padding: 9px 10px; }
.finops-decision-risk-chain-stages > li > span { display: grid; width: 22px; height: 22px; border: 1px solid #bdd0ef; border-radius: 50%; place-items: center; background: #fff; color: var(--blue); font-size: 9px; font-weight: 800; }
.finops-decision-risk-chain-stages > li[data-state="pending"] > span { border-color: #d7dde7; color: var(--muted); }
.finops-decision-risk-chain-stages > li > div { display: grid; min-width: 0; align-content: start; gap: 2px; }
```

At `max-width: 980px`, set `grid-template-columns: repeat(2, minmax(0, 1fr))`. Keep the existing single-column rule at `max-width: 640px`. Remove the old `li:not(:last-child)::after` rule completely.

- [ ] **Step 5: Run the focused Playwright test and verify GREEN**

```powershell
$env:DF_PLAYWRIGHT_PORT="5217"
npx playwright test tests/finops-operations-management.spec.mjs --grep "demo workspace exposes complete"
```

Expected: PASS with four non-overlapping stage summaries, no connector, and no raw mapped enums.

- [ ] **Step 6: Commit the component and CSS change**

```powershell
git add web/src/finops/RiskDecisionPage.jsx web/src/styles.css web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): compact selected risk stages"
```

---

### Task 3: Full frontend and visual acceptance

**Files:**
- Verify: `web/src/finops/RiskDecisionPage.jsx`
- Verify: `web/src/finopsDecisionViewModel.js`
- Verify: `web/src/styles.css`
- Verify: `web/tests/finops-operations-management.spec.mjs`
- Output only: `output/playwright/finops-risk-stage-desktop.png`
- Output only: `output/playwright/finops-risk-stage-mobile.png`

**Interfaces:**
- Consumes: the completed Task 1 and Task 2 commits.
- Produces: reproducible test and screenshot evidence suitable for PR review; no deployment or production traffic change.

- [ ] **Step 1: Run the complete Node suite**

```powershell
Set-Location web
node --test
```

Expected: all Node tests PASS with zero failures.

- [ ] **Step 2: Build the production bundle**

```powershell
npm run build
```

Expected: Vite build succeeds; an existing chunk-size warning is acceptable if no new warning appears.

- [ ] **Step 3: Run the complete Playwright suite on an isolated port**

```powershell
$env:DF_PLAYWRIGHT_PORT="5217"
npx playwright test
```

Expected: all Playwright tests PASS; no stale preview server is reused.

- [ ] **Step 4: Capture desktop and mobile risk screenshots**

Add screenshot capture to the focused acceptance test or use its existing authenticated mock flow:

```js
await page.setViewportSize({ width: 1440, height: 1000 });
await page.screenshot({ path: "../output/playwright/finops-risk-stage-desktop.png", fullPage: true });
await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({ path: "../output/playwright/finops-risk-stage-mobile.png", fullPage: true });
```

Inspect both images and confirm: no horizontal connector, no overlap, four/two/one-column breakpoint behavior as applicable, and customer-readable evidence text.

- [ ] **Step 5: Run final repository checks**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional source/test changes are tracked and screenshots remain untracked output artifacts.

- [ ] **Step 6: Create the review handoff**

Push `codex/finops-risk-stage-ui` and open a PR against `Myr-Team/dataforge-agent:main`. Report Node, Vite, Playwright, screenshot, and credential-scan evidence. Do not build Azure images, create Container App revisions, or switch production traffic without a separate explicit production approval.

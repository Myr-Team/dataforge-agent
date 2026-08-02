# Operations Management Executive Dashboard Design

Date: 2026-08-02
Status: Approved visual direction; implementation pending

## Objective

Turn Operations Management into an executive dashboard that answers four
questions at a glance:

1. How much did AI usage cost?
2. Is the service operating effectively?
3. What measurable efficiency or value has appeared?
4. What needs attention now?

The design retains the existing FinOps, ROI, risk, evidence, filtering, and AI
capabilities. It changes their hierarchy so that the overview supports a quick
decision and the other tabs provide explanation and drill-down.

## Current problem

The current overview and cost page repeat the same eight KPI cards. Cost,
cache savings, usage, trust, ROI investment, and risk are all presented as
primary information. As a result, the product contains more visible material
than the decision requires, even though the individual components are usable.

The main ambiguity is semantic rather than visual:

- Estimated model usage cost appears on both the overview and cost page.
- ROI monthly total cost includes scenario implementation, fixed, and model
  costs, but its name is too close to model usage cost.
- Overview, cost, ROI, and risk pages each mix summary, explanation, and action.

## Chosen direction

Use the approved **single-screen executive briefing with one cost-composition
donut**.

The overview should fit its primary decision content within one desktop
viewport at 1440 x 900 under normal browser chrome. Additional detail remains
available below the fold or through the existing tabs, but must not be required
to understand the current operating state.

## Page responsibilities

### Operations overview

The overview answers what is happening now. It contains:

- Four KPI cards.
- One combined trend panel.
- One compact department cost-composition donut.
- One attention panel with no more than three ranked items.
- Three navigation cards that explain what each detailed page answers.

It does not repeat full attribution tables, detailed evidence coverage,
multiple cost donuts, request lists, or the complete risk queue.

### Cost analysis

Cost analysis answers where cost comes from. It opens directly with cost trend
and attribution instead of repeating the overview's eight-card KPI block.

It retains department, workspace, Agent, and model attribution, Token context,
pricing coverage, unpriced handling, CSV export, and price-mapping entry points.
Detailed composition charts may remain here because this page is the explicit
drill-down destination.

### Efficiency and ROI

ROI answers whether the investment is producing value. Rename the existing
monthly total cost label to **AI operating investment** in customer-facing copy.
The associated help text must state that it can include implementation
amortization, fixed operating cost, and current model cost. It must not imply
that this value equals the overview's request-level estimated model cost.

Scenario estimates and verified business results remain visibly separate.

### Risk and optimization

Risk answers what should be handled first. It retains priority, impact,
evidence, recommendation, and governed-draft detail. It does not introduce
generic cost or usage KPI cards.

## Overview layout

### Header and filters

Keep the current title, description, date range, department, Agent, and model
filters. Show a compact update indicator with the ten-minute automatic refresh
description. The filter row and tab row remain stable across all four pages.

### Four primary KPIs

Display exactly these four cards:

1. **AI usage cost**: request-level price-card estimate and pricing coverage.
2. **Effective calls**: successful calls over total calls and success rate.
3. **Cache benefit**: estimated avoided cost and observed cache hit rate.
4. **Value assessment**: verified, needs validation, insufficient evidence, or
   unavailable, accompanied by evidence maturity when recorded.

Each card keeps its help tooltip, evidence entry point, and metric-aware AI
entry. Secondary numbers are supporting text, not additional KPI cards.

### Decision row

Use a three-column desktop row:

- **Trend, approximately 52%**: one chart with explicit Cost, Calls, Token, and
  P95 switches. Axis scale and bar height continue to derive from real data.
- **Cost composition, approximately 24%**: one donut showing the top department cost
  shares for the selected scope and window.
- **Needs attention, approximately 24%**: no more than three ranked items drawn
  from available cost completeness, service quality, cache, value, and risk
  signals.

The donut is a summary, not a second cost-analysis page:

- Use estimated cost as the slice value.
- Show at most the top three departments plus `Other` when needed. Preserve the
  existing `Unassigned` category instead of silently reallocating it.
- Use the same currency and price-card status as the overview.
- A slice tooltip shows department name, estimated cost, percentage, and evidence
  state.
- Clicking the chart or its `View cost analysis` affordance opens Cost Analysis
  with the current filters preserved.
- If no comparable cost is available, show a compact honest empty state instead
  of drawing equal or fabricated slices.

### Drill-down navigation

Place three concise cards below the decision row:

- Cost Analysis: Where does the cost come from?
- Efficiency and ROI: Is the investment producing value?
- Risk and Optimization: What should be handled first?

These cards navigate to existing tabs and preserve the selected date,
department, Agent, and model filters.

### Operations AI

Keep the single floating Operations AI launcher. Do not add another consultation
banner, footer, or persistent drawer. Metric-level `Ask AI` actions continue to
open the same compact assistant with the selected metric context. Existing
workspace history preload and server-side cross-device history remain unchanged.

## Data and component boundaries

Prefer existing read models and APIs. This redesign must not require a backend
schema or authentication change.

- Four KPIs derive from the existing overview/bootstrap, cache, and ROI status
  projections.
- Trend uses the existing trend projection and selected metric state.
- The department donut derives from the department breakdown already returned
  by the overview bootstrap.
- Attention items reuse bounded anomaly, coverage, cache, pricing, and ROI
  evidence already returned to the portal.

Create presentation-focused frontend projections rather than calculating new
business truth inside React components. The overview projection should expose
one bounded object for KPIs, trend, donut slices, attention items, and drill-down
links. Detailed cost and ROI pages keep their own projections.

## Truthfulness and error handling

- Observed zero remains zero; missing data remains unavailable.
- Partial pricing or evidence remains explicitly partial.
- The overview must not use infrastructure product names in customer-facing
  labels.
- Cached data remains visible during a refresh. A refresh failure shows a small
  bounded status without replacing the page with a full-screen error.
- One unavailable source must degrade only its dependent card or panel.
- No prompt, response body, raw identity, secret, or provider response ID is
  introduced by this redesign.

## Responsive behavior

### Desktop

- Four KPI cards in one row.
- Trend, donut, and attention in one decision row.
- Three drill-down cards in one row.
- Tooltips render at viewport level and cannot be clipped by panels.

### Intermediate widths

- KPI cards use a two-by-two grid.
- Trend spans the full row.
- Donut and attention share the following row when space permits.

### Mobile

- KPI cards use a single column or two-column compact grid based on available
  width.
- Trend, donut, attention, and drill-down cards stack in that order.
- Donut legends remain visible without horizontal scrolling.
- Operations AI remains a compact popover and never becomes a full-screen
  permanent drawer.

## Refresh and performance

- Keep the current ten-minute visible-page refresh interval.
- Pause automatic refresh when the page is hidden.
- Reuse the existing cache-first FinOps store and workspace-scoped keys.
- Tab navigation must render cached overview data immediately when available.
- Do not issue an additional overview request solely for the donut; derive it
  from the already-loaded department breakdown.

## Acceptance criteria

1. Overview presents exactly four primary KPI cards.
2. Cost Analysis no longer repeats the overview's eight-card KPI grid.
3. The overview donut uses real estimated cost proportions; non-equal source
   values produce visibly non-equal slices.
4. The donut shows at most three named departments plus `Other` and has bounded,
   unclipped tooltips.
5. AI usage cost and AI operating investment are visually and semantically
   distinct.
6. Desktop 1440, desktop 1366, intermediate mobile, and narrow mobile layouts
   have no overlap or horizontal page overflow.
7. Existing filters, evidence links, metric-aware AI, CSV export, price mapping,
   ROI scenario, risk selection, and remediation draft flows remain reachable.
8. All visible demo-workspace charts and cards contain meaningful values while
   real missing data remains honestly unavailable.
9. Node tests, Vite build, and the full Playwright suite pass.
10. New Playwright acceptance covers KPI deduplication, donut proportions,
    filter-preserving drill-down, responsive layout, tooltip boundaries, and
    cache-first navigation.

## Non-goals

- No backend, SQL, Easy Auth, APIM, or price-card schema change.
- No new polling service or shorter refresh interval.
- No new autonomous governance action.
- No duplicate AI chat surface.
- No fabricated data for non-demo workspaces.

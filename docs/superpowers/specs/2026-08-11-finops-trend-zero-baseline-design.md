# FinOps Trend Zero-Baseline Design

## Scope

Fix only the presentation layer of the existing `TrendBars` chart in `web/src/FinOpsPortal.jsx` and its CSS/tests. Do not change backend APIs, response fields, view-model calculations, metric formulas, filters, or introduce a charting dependency.

## Root cause

The current Y-axis and grid lines are absolutely positioned with independent `top` and `bottom` offsets, while each column owns a plot track plus a separate 24px date row. Therefore the `$0` tick, the visual baseline, and the bar origin do not share one layout track. The current hover transform also applies `translateY(-2px)`, visually detaching a bar from zero.

## Layout

`TrendBars` will render a legend followed by one chart body with two columns: a Y-axis and a horizontally scrollable plot viewport. Both sides use the same two rows:

1. a fixed plot track containing ticks/grid lines/bars;
2. a 24px X-axis label track.

The zero tick and the common X-axis baseline are the bottom edge of the plot track. Date labels sit immediately below it. Native horizontal overflow is below the label track and appears only when content width exceeds the viewport.

Columns remain CSS/React-rendered. A CSS custom property records the visible point count; the columns use `min-width: max(100%, calc(count * minimum-column-width))`. Short series fill the panel without a scrollbar; long series and narrow mobile viewports overflow naturally.

## Visual behavior

- Cost remains orange (`#f79009`) on the white theme.
- Bars become narrower and spacing increases.
- Hover/focus increases saturation and horizontal emphasis without vertical translation.
- A subtle vertical guide is shown for the active column.
- Horizontal grid lines are lighter than the zero baseline.
- Cached token segments remain green and existing token-series colors are retained.
- Comparison markers remain anchored to the zero baseline.

## Tooltip contract

Keep the existing tooltip data and calculations unchanged. It must continue to display the date, selected metric, cache hit, cache miss, cache bypass, avoided tokens, estimated savings, and applicable event count. Only spacing, typography, and alignment may change.

## Accessibility and responsive behavior

Keyboard focus receives the same active-column guide and tooltip as pointer hover. The viewport-level tooltip remains clamped to the browser viewport. Reduced-motion behavior is preserved. On small screens, long ranges may scroll horizontally; short ranges must not reserve a fake scrollbar track.

## Acceptance

- `$0`, the X-axis baseline, and every bar origin share the same pixel Y coordinate.
- A zero value has zero bar height.
- Hover/focus never changes the bar bottom coordinate.
- Seven points do not overflow a desktop chart; a sufficiently long series does.
- Existing tooltip fields remain present.
- Node tests, Vite build, and focused desktop/mobile Playwright acceptance pass.

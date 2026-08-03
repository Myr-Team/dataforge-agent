import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("operations assistant is a compact anchored popover rather than a dashboard drawer", async () => {
  const [component, portal, styles] = await Promise.all([
    readFile(new URL("./FinOpsAssistant.jsx", import.meta.url), "utf8"),
    readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8"),
    readFile(new URL("./styles.css", import.meta.url), "utf8"),
  ]);

  assert.match(component, /queryFinOpsAssistant/);
  assert.doesNotMatch(component, /loadFinOpsAssistantConversations/);
  assert.doesNotMatch(component, /loadFinOpsAssistantMessages/);
  assert.match(component, /prefetchFinOpsAssistantHistory/);
  assert.match(component, /peekFinOpsAssistantHistory/);
  assert.match(component, /正在同步历史/);
  assert.doesNotMatch(component, /if \(!open \|\| !workspaceId\)/);
  assert.match(component, /clearFinOpsAssistantConversation/);
  assert.match(component, /conversation_ref/);
  assert.match(component, /清空历史/);
  assert.match(component, /evidenceLabels/);
  assert.match(component, /相关证据/);
  assert.match(component, /publicAssistantContent/);
  assert.match(component, /req_/);
  assert.match(component, /slice\(0,\s*600\)/);
  assert.match(component, /className="finops-ai-launcher"/);
  assert.match(component, /className="finops-ai-popover"/);
  assert.match(component, /aria-expanded=\{open\}/);
  assert.match(component, /正在询问/);
  assert.match(component, /contextualAssistantQuestion/);
  assert.match(component, /lastOpenRequestRef/);
  assert.match(component, /message\.sections/);
  assert.match(component, /结论/);
  assert.match(component, /依据/);
  assert.match(component, /影响/);
  assert.match(component, /建议/);
  assert.match(component, /判断边界/);
  assert.doesNotMatch(component, /backdrop/i);
  assert.match(portal, /<FinOpsAssistant/);
  assert.doesNotMatch(portal, /title="FinOps Agent"/);
  assert.doesNotMatch(portal, /title="ROI Agent"/);
  assert.match(styles, /\.finops-ai-popover\s*\{[^}]*position:\s*fixed/s);
});


test("metric cards expose keyboard tooltip and ask-ai affordances", async () => {
  const source = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");

  assert.match(source, /finops-metric-tooltip-content/);
  assert.match(source, /tabIndex=\{0\}/);
  assert.match(source, />问 AI</);
  assert.match(source, /metricTooltip\(/);
  assert.match(source, /metricContext\(/);
});

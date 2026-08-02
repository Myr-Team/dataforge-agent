# FinOps UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精修 ROI、风险与运营 AI，使演示页面无重叠、无浮层裁切并能即时恢复 AI 历史。

**Architecture:** 保留现有 React 页面和服务端 view model。新增一个视口级 Tooltip 组件；把风险散点图替换为由同一真实数据生成的四象限分组，把优化散点替换为排序条形；运营 AI 增加按 workspace 缓存的后台预取层。

**Tech Stack:** React 19、Vite、Node test、Playwright、CSS、Azure Container Apps。

## Global Constraints

- 不修改后端 FinOps 公共接口和 Easy Auth。
- 不伪造或改写任何成本、ROI、风险或请求证据数值。
- `DF_FINOPS_ACTIONS_ENABLED=0`。
- 所有新增交互支持鼠标、键盘和 390px 移动端。
- 生产部署使用不可变镜像，先零流量候选验收，后切流量。

---

### Task 1: ROI 与风险信息层级

**Files:**
- Modify: `web/src/finops/RoiDecisionPage.jsx`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/finops/DecisionCharts.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/finopsLayout.test.mjs`
- Test: `web/src/finopsDecisionViewModel.test.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `roiDecisionView(payload)`、`riskDecisionView(payload)`，不改变返回结构。
- Produces: `riskQuadrants(points)`，返回四个固定象限及原始风险点；`OpportunityPortfolio` 输出非重叠条形列表。

- [ ] **Step 1: 写失败测试**

```js
assert.doesNotMatch(roiSource, /finops-decision-roi-ai/);
assert.doesNotMatch(riskSource, /咨询当前判断|继续询问/);
assert.match(charts, /finops-decision-risk-quadrants/);
assert.doesNotMatch(charts, /finops-decision-matrix-point/);
```

- [ ] **Step 2: 运行失败测试**

Run: `node --test src/finopsLayout.test.mjs src/finopsDecisionViewModel.test.mjs`

Expected: FAIL，指出旧咨询横幅和气泡节点仍存在。

- [ ] **Step 3: 实现最小布局改动**

```js
export function riskQuadrants(points = []) {
  return [
    { id: "priority", label: "优先处置", items: points.filter((p) => p.xConfidence >= 50 && p.yImpact >= 50) },
    { id: "validate", label: "重点验证", items: points.filter((p) => p.xConfidence < 50 && p.yImpact >= 50) },
    { id: "improve", label: "计划改善", items: points.filter((p) => p.xConfidence >= 50 && p.yImpact < 50) },
    { id: "observe", label: "持续观察", items: points.filter((p) => p.xConfidence < 50 && p.yImpact < 50) },
  ];
}
```

删除 ROI 底部咨询横幅、ROI 头部咨询按钮、重复价值明细表、风险头部咨询按钮和风险分析卡的继续询问按钮；保留指标卡上下文入口与全局运营 AI。

- [ ] **Step 4: 运行测试并检查桌面/移动布局**

Run: `node --test src/finopsLayout.test.mjs src/finopsDecisionViewModel.test.mjs`

Expected: PASS。

### Task 2: 视口级 Tooltip

**Files:**
- Create: `web/src/finops/ViewportTooltip.jsx`
- Create: `web/src/finopsTooltip.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/finopsTooltip.test.mjs`
- Test: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Produces: `placeViewportTooltip(anchorRect, tooltipSize, viewportSize, options)` 返回 `{left, top, placement}`。
- Produces: `<ViewportTooltip anchorRef open id>{children}</ViewportTooltip>`。

- [ ] **Step 1: 写失败的边界测试**

```js
const right = placeViewportTooltip({ left: 990, right: 1010, top: 300, bottom: 330 }, { width: 220, height: 160 }, { width: 1024, height: 768 });
assert.ok(right.left + 220 <= 1012);
const top = placeViewportTooltip({ left: 10, right: 30, top: 6, bottom: 26 }, { width: 220, height: 160 }, { width: 1024, height: 768 });
assert.equal(top.placement, "below");
```

- [ ] **Step 2: 运行失败测试**

Run: `node --test src/finopsTooltip.test.mjs`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现定位函数和 portal 组件**

定位函数使用 12px 视口边距和 8px 间隔；优先显示在触发元素上方，空间不足时显示下方，并在 resize/scroll 时重新定位。指标问号和趋势柱使用该组件，旧的卡片内绝对定位 Tooltip 删除。

- [ ] **Step 4: 运行单元与浏览器边界测试**

Run: `node --test src/finopsTooltip.test.mjs src/finopsInteraction.test.mjs`

Run: `npx playwright test tests/finops-portal-acceptance.spec.mjs --grep "tooltip"`

Expected: PASS，首列、中间列、末列浮层 bounding box 全部在视口内。

### Task 3: 运营 AI 历史预取

**Files:**
- Create: `web/src/finopsAssistantHistory.js`
- Modify: `web/src/FinOpsAssistant.jsx`
- Test: `web/src/finopsAssistantHistory.test.mjs`
- Test: `web/tests/finops-insight-agents.spec.mjs`

**Interfaces:**
- Produces: `preloadAssistantHistory(workspaceId, loaders)`，按 workspace 去重请求并缓存 `{conversationRef, messages, loadedAt}`。
- Produces: `readAssistantHistory(workspaceId)`、`clearAssistantHistory(workspaceId)`。

- [ ] **Step 1: 写失败测试**

验证同一 workspace 两次预取只执行一组网络调用，读缓存同步返回，清空后不存在缓存；Playwright 验证按钮点击后 300ms 内弹层可见且打开前已有历史请求。

- [ ] **Step 2: 运行失败测试**

Run: `node --test src/finopsAssistantHistory.test.mjs`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现预取与即时渲染**

组件在 `workspaceId` 可用时立即调用预取，不依赖 `open`；先同步读取缓存，再静默刷新。打开弹层只切换本地状态，不启动重复串行请求。清空成功后同步清理缓存。

- [ ] **Step 4: 运行 AI 单元和浏览器测试**

Run: `node --test src/finopsAssistantHistory.test.mjs src/finopsAssistant.test.mjs`

Run: `npx playwright test tests/finops-insight-agents.spec.mjs`

Expected: PASS。

### Task 4: 全量验收与部署

**Files:**
- Modify: `docs/validation/2026-08-02-finops-ui-polish-production.md`

**Interfaces:**
- Consumes: 前三项实现及现有部署脚本。
- Produces: 不可变 backend/web 镜像、候选 revision、截图、健康和流量证据。

- [ ] **Step 1: 完整验证**

Run: `node --test`

Run: `npm run build`

Run: `npx playwright test`

Run: `git diff --check`

Expected: 全部退出码 0。

- [ ] **Step 2: 构建并部署零流量候选**

复用仓库既有不可变镜像与 Container Apps 部署流程；backend 仅在前端镜像依赖同一版本标签时重建，否则保持现有后端 revision。候选 revision 初始流量为 0。

- [ ] **Step 3: 候选验收**

检查 `/health`、前端入口、四页数据、ROI、风险、Tooltip、AI 历史、桌面和移动截图；确认 `DF_FINOPS_ACTIONS_ENABLED=0`。

- [ ] **Step 4: 切换并复核**

候选全部通过后将 Web 流量切换到新 revision，旧 revision 保留为回滚目标；再次核对 Healthy、Running、100% traffic 和生产页面。

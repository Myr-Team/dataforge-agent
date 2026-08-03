# FinOps 前端决策优先精修实施计划

> 已批准方案：B（决策优先压缩）。执行边界为前端与测试，不改后端、认证或生产。

## Task 1：建立页面结构保护测试

**Files:**

- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify or add: `web/src/finops/*.test.js`

1. 增加风险页测试：扫描摘要可见、七条规则默认收起、风险矩阵与优先事项无需展开规则即可到达。
2. 增加规则披露测试：展开后显示所有规则，扫描与证据选择仍可用。
3. 增加成本页测试：保留 Agent/模型排名，删除重复的 Agent/模型成本结构环形图。
4. 先运行目标测试并确认因尚未实现而失败。

## Task 2：实现风险页决策优先布局

**Files:**

- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/styles.css`

1. 将扫描概况与详细规则拆分为始终可见摘要和默认折叠披露区。
2. 将风险矩阵、优先事项与选中证据链提前到风险组合之前。
3. 保留原有扫描、选择、证据和上下文 AI 回调，不改数据契约。
4. 运行目标 Node/Playwright 测试至通过。

## Task 3：压缩成本页重复内容

**Files:**

- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`

1. 移除 Agent 和模型成本结构环形图块。
2. 保留趋势、归因表、Agent 排名和模型排名。
3. 调整剩余网格使桌面两列、窄屏单列，并验证数值比例仍来自真实数据。
4. 运行目标测试至通过。

## Task 4：统一可读性和浮层安全区

**Files:**

- Modify: `web/src/styles.css`
- Modify as needed: `web/src/finops/DecisionCharts.jsx`
- Modify as needed: `web/src/FinOpsAssistant.jsx`

1. 将 FinOps 决策页的正文和辅助文字调整到可读字号层级。
2. 为悬浮 AI 预留页面底部和右侧空间，确保不遮挡内容。
3. 验证图表 tooltip 在桌面和 390px 视口内不越界、不被相邻卡片裁切。
4. 不改变 AI 会话、历史加载和上下文提问接口。

## Task 5：视觉与全量验收

**Files:**

- Update tests only if acceptance reveals a genuine uncovered regression.
- Create screenshots under `output/playwright/` but do not commit generated artifacts.

1. 运行 `python -m pytest -q`。
2. 在 `web/` 运行 `node --test`、`npm run build`、`npx playwright test`。
3. 生成总览、成本、ROI、风险的桌面与移动端截图并逐张检查。
4. 运行 `git diff --check`，确认不包含测试输出、工作区数据或密钥。
5. 提交有意图的 commits，推送分支并创建 PR；不合并、不部署。

# FinOps BI / ROI / RAG 实施计划

> 在隔离分支中按 TDD 执行；先建立失败证据，再做最小实现和视觉验收。

## Task 1：修正趋势桶与坐标契约

- 修改 `backend/finops/query.py`：为趋势桶增加完成状态。
- 修改 `web/src/finopsViewModel.js`：保留桶状态并改进自适应坐标。
- 修改 `web/src/FinOpsPortal.jsx`、`web/src/styles.css`：增加网格线、完整日期、进行中桶和可读 tooltip。
- 测试：`tests/test_finops_query.py`、`web/src/finopsViewModel.test.mjs`、运营 Playwright。

## Task 2：精修成本摘要

- 扩展 `executiveCostSummary()`，分别返回金额、覆盖率、已计价和未计价请求。
- 将成本页顶部改为紧凑 KPI + 操作分区，桌面/移动端保持一致视觉层次。
- 测试：view-model、布局静态测试和 Playwright 截图。

## Task 3：增加 ROI 回归误差证据

- 在 `backend/finops/decision_service.py` 增加有界的留出回归计算。
- 扩展 `RoiDecision` 响应契约和前端安全投影。
- 在 ROI 页面增加“趋势回归校验”卡，明确 MSE 边界和样本状态。
- 测试：正常样本、样本不足、观测零值、情景状态不得升级为 verified。

## Task 4：升级运营 AI 内部知识检索

- 新建四份中文内部方法文档：成本与计价、缓存与性能、ROI 与回归、风险证据。
- 将 `assistant_knowledge.py` 改为白名单文档切块检索，返回文档/章节引用。
- 在模型任务说明中要求使用内部知识引用，同时维持请求证据白名单。
- 测试：不同问题命中不同章节、结果有界、无文件路径、知识不充当当前数值证据。

## Task 5：完整验收与发布

- 同步工作区路由到设置摘要，补齐 DeepSeek 提供商、模型和策略版本的浏览器验收。
- 扩展调用遥测与运行 Trace，分别展示结果缓存、提供商 Token 缓存和真实入口治理状态。
- 增加按模型的请求、成本和互斥 Token 构成，避免缓存 Token 与输入 Token 重复统计。

- 运行目标测试，再运行全量 Python、Node、Vite 与 Playwright。
- 生成桌面和移动端成本/ROI/AI 截图并人工查看遮挡、留白、刻度和 tooltip。
- 提交、推送、创建并合并 PR。
- 以不可变镜像部署 backend/web 候选，完成健康与接口验收后先 backend 后 web 切换流量；保留上一健康修订作为回滚目标。

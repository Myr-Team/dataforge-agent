# 运营管理 ROI / 风险决策上线验收

日期：2026-08-02

分支：`codex/operations-demo-readiness`

## 上线范围

- 运营总览：调用、Token、估算成本、成功率、P50/P95、缓存命中与节省、治理覆盖及数据可信度。
- 成本分析：成本趋势、部门/工作区/Agent/模型归因、比例图和价格映射入口。
- 效能与 ROI：情景测算、价值桥、证据成熟度、单位经济趋势和验证状态。
- 风险与优化：风险矩阵、四类独立请求证据、优化优先级、建议草案与复核流程。
- 请求证据：业务名称优先，技术 ID 折叠显示；可回到运行记录或 Foundry Trace。
- 缓存：5 分钟新鲜、30 分钟可复用，当前可见页签每 10 分钟自动刷新；隐藏页签暂停刷新。
- 安全边界：`DF_FINOPS_ACTIONS_ENABLED=0`，本次没有启用自动生产治理执行。

## 生产修订

| 服务 | 当前生产修订 | 镜像摘要 | 流量 | 回滚修订 |
|---|---|---|---:|---|
| Backend | `ca-dataforge-backend--opsaug3cd8d44` | `sha256:357ed1173a7c8f7031ac17429d74653f5d3db9f1f8ab1831185e425e9fae0387` | 100% | `ca-dataforge-backend--mb-93210b8bb7f0` |
| Web | `ca-dataforge-web--opsaug3cd8d44` | `sha256:0672131bdf8ecd322fdeb9826fe3bdbc0233764deaf845e405114d9b238766bc` | 100% | `ca-dataforge-web--mbwebfix93210b8` |

后端切流后，旧 Web 通过稳定入口连续 3 次访问 `/api/health` 成功；随后才切换 Web。未登录访问稳定域名返回 HTTP 401，Easy Auth 边界保持有效。

## 真实候选数据门禁

门禁在部署修订内调用实际 FastAPI、Azure SQL 与 Redis 依赖，不读取或输出密钥、原始身份、正文或 provider response ID。

| 项目 | 验收结果 |
|---|---:|
| 请求 / Token | 198 / 804,706 |
| 估算成本 | USD 2.0694675 |
| 预算 | USD 670；已用 USD 0.5282；预测 USD 18.0156 |
| 成功率 / 错误率 | 95.96% / 4.04% |
| P50 / P95 | 3,032 ms / 9,914 ms |
| 缓存 | 88 次可缓存；13 hit；75 miss；避免 23,563 Token；估算节省 USD 0.11003754 |
| 治理覆盖 | 33.84% |
| 趋势 | 30 个桶；调用、Token、成本均存在不同数值和不同柱高 |
| 部门 / 工作区 / Agent | 5 / 1 / 11 行 |
| 价格 | 6 条官方目录、4 条映射；GPT-5.1 与 GPT-5.6 Terra 均可计价 |
| ROI | 4 个指标、30 个单位经济趋势点、1 个情景、价值桥可用 |
| 风险与优化 | 4 个领域、6 个矩阵点、6 条优先项、6 条优化建议、9 份不同请求证据 |
| 请求详情 | 名称、操作、状态、时延、Token、缓存、成本、业务请求与响应摘要均存在 |

Agent 成本归因中 8 行有数值，3 行为明确“未计价”治理状态；页面提供价格映射入口，不显示为“未接入”，也不将缺失成本伪造为 0。

## 历史聚合修复

旧版小时聚合作业无法读取新版事件结构，历史任务返回 `ValidationError`，导致 ROI 单位经济趋势为空。本次将聚合作业镜像升级到兼容版本，先完成 720 小时回补，再恢复为每次刷新最近 48 小时；恢复后的 48 小时任务已再次执行成功。30 天 ROI 趋势恢复为 30 个有效数据点。

## 自动化回归

| 门禁 | 结果 |
|---|---|
| Python | `1688 passed, 1 skipped, 1 warning`；warning 为既有 MAF ExperimentalWarning |
| Node | `250 passed, 0 failed` |
| Vite | 构建成功，1789 modules；保留既有 chunk size warning |
| Playwright | `46 passed` |
| `git diff --check` | 通过 |

Playwright 包含完整度门禁，要求四个运营页面所有可见 metric、card、chart、table、queue 均有展示内容；同时覆盖桌面、1366px、移动端、键盘焦点、证据抽屉、模型配置冲突、邮件预算页面和价格映射。

## 前端视觉复核

已复核以下本地验收截图，截图不提交到仓库：

- `operations-management-overview-desktop.png`
- `operations-cost-analysis-desktop.png`
- `operations-roi-desktop.png`
- `operations-risk-desktop.png`
- `operations-management-mobile.png`
- `task6-member-budget-page-desktop.png`
- `operations-model-settings-desktop.png`

复核结论：问号紧邻指标标题；AI 为紧凑悬浮入口；证据面板不覆盖应用顶栏；趋势柱、成本条、比例环和风险气泡均按数据比例绘制；无持续装饰点和横向页面溢出。

## 仍需人工观察的边界

- Chrome 登录态页面在生产切流后的自动刷新控制连续超时，因此没有把登录态生产截图标记为完成；服务端数据门禁、同域代理健康、Easy Auth 401、生产日志和本地全量 UI 回归均已通过。
- 成本是官方价目参考下的请求级估算，不是 Azure 实际账单。
- “未计价”是可处理的价格映射状态，不等同于接口未接入；缺失价格不会被伪造为 0。
- 邮件配置入口已启用，端点与发件配置存在；本次没有自动向外部收件人发送测试邮件。
- 生产治理执行继续关闭，建议只能生成草案，不能自动批准或执行。

## 日志门禁

上线后 30 分钟窗口内：

- 新 Backend：36 条日志，精确 `ERROR` / `Exception` / `Traceback` / `CRITICAL` 计数为 0。
- 新 Web：650 条日志，同类严重信号计数为 0。

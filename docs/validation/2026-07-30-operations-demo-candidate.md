# DataForge 运营管理演示候选验收记录

## 结论

本轮代码实现、本地全量回归、本地桌面与移动端浏览器验收以及独立代码复审均已通过。
Azure 已创建 backend/web 两个 **0% 流量**候选 revision，当前生产 revision
继续承载 100% 流量。

Azure SQL additive migration 已完成一次全量执行，最终 backend 候选的托管身份
只读探针确认以下新增结构均已存在：

- `df_finops.budget_subject`
- `df_finops.notification_setting.test_email_succeeded_at`
- `df_finops.demo_seed_event`

演示工作区初始化已连续执行两次并验证幂等，最终 0% backend 候选已开启成员预算
与邮件配置、关闭种子入口；自动邮件、治理动作和外部模型路由继续关闭。当前仍不是
可切流量候选：修复后的 migration 尚缺一次真实重复执行证据，登录态端到端 UI、
预算写入冲突和测试邮件验收也尚未完成。未经完成这些门禁和用户明确批准，不得
切换生产流量。

## 代码与独立复审

- 分支：`codex/operations-demo-readiness`
- 候选代码提交：`302091d7018eedbc62781aafe0661370d17df983`
- 基线：`team/main` 的 `e71a5f0f588703856847427eb1b698b7a6cfd2fe`
- 独立复审最终结论：`No blocking findings`
- `git diff --check`：通过

复审确认：

- 通知设置要求可信 Easy Auth 身份、工作区管理员以及精确的
  `DataForge.FinOpsAdmin` 应用角色。
- 演示初始化器同时校验 tenant/workspace allowlist，并要求已有 HMAC secret。
- 演示请求事实与 seed ownership 在同一 SQL 事务中替换，退役事实会被删除。
- 演示结果保留来源运行并维持 `unverified`，不会伪造人工验证。
- ROI 产物统计扫描完整运行集合，并按产物自己的生成时间落入窗口；第 81 次运行
  和历史运行后补产物均有回归覆盖。
- 同范围刷新失败保留上次数据；workspace、日期或筛选范围变化后不会把旧范围
  数据显示在新范围工具栏下。
- 客户可见运营页面不显示 APIM、Foundry 或 Azure Monitor 产品名称。

预算提醒的去重规则保持为：同一 budget、period、threshold 最多提醒一次；
修改预算或通知配置不会对同一周期同一阈值补发。

## 本地自动化证据

| 门禁 | 结果 |
|---|---|
| `python -m pytest -q` | `1597 passed, 1 skipped` |
| `node --test` | `167 passed` |
| `npm run build` | 成功，1780 modules；仅保留既有大 chunk 警告 |
| `npx playwright test` | `35 passed` |
| 独立复审定向测试 | 通过，无阻断发现 |
| `git diff --check` | clean |

浏览器验收包含：

- 运营总览、成本分析、效能与 ROI、风险与优化四页；
- 真实比例柱状图、自适应纵轴、缓存 miss/hit/bypass 与节省；
- 五分钟刷新、隐藏页暂停、手动刷新；
- 不同风险打开不同请求证据；
- 同范围刷新失败保留数据、范围变化失败清空旧数据；
- 成员预算、邮件配置权限与安全错误状态；
- desktop/mobile 无横向溢出、抽屉和 AI 浮层不遮挡主内容。

本机截图文件名：

- `operations-management-overview-desktop.png`
- `operations-cost-analysis-desktop.png`
- `operations-roi-desktop.png`
- `operations-management-desktop.png`
- `risk-latency-evidence-desktop.png`
- `risk-cache-evidence-desktop.png`
- `operations-management-mobile.png`
- `member-budget-settings-desktop.png`
- `member-budget-settings-mobile.png`

截图属于本地 Playwright 输出，不纳入镜像或 Git 提交。

## 不可变镜像

backend 镜像从候选代码提交 `302091d7018eedbc62781aafe0661370d17df983`
构建；web 代码未变化，继续复用已验证的 `3ffacd3` 不可变镜像：

| 组件 | ACR run | Digest |
|---|---|---|
| backend | `chf7` | `sha256:5e0b9e2536ef0fbd2ed9f59e64701c33bc21f6004772217a1447e6df09035eac` |
| web | `chf5` | `sha256:4d0edb7d162b8a0de3f9e350b5c01808b79a19acaead1ce7910288e7f4699a67` |

Backend 构建使用 `git archive HEAD` 生成只包含已提交文件的临时上下文；本地测试
结果、工作区临时数据、环境文件和密钥均未进入构建上下文。

## Azure 零流量候选

| 组件 | 生产 revision | 候选 revision | 流量 | 状态 |
|---|---|---|---|---|
| backend | `ca-dataforge-backend--mb-93210b8bb7f0` | `ca-dataforge-backend--opsdemo302091dfinal` | `100 / 0` | 两者 Healthy |
| web | `ca-dataforge-web--mbwebfix93210b8` | `ca-dataforge-web--opsdemo302091d` | `100 / 0` | 两者 Healthy |

候选运行验证：

- Backend `/api/health` 连续三次 `200`。
- Web 匿名访问 `401`，Easy Auth 边界保持。
- Web 候选通过 `DF_BACKEND_UPSTREAM` 精确指向最终 0% backend 候选。
- Backend/web 候选日志中未检出 traceback、error、常见 API key、Bearer token
  或 secret 标记。
- 未执行生产流量切换。

Backend 候选安全开关：

```text
DF_FINOPS_READ_ENABLED=1
DF_FINOPS_ACTIONS_ENABLED=0
DF_FINOPS_MEMBER_BUDGETS_ENABLED=1
DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1
DF_FINOPS_EMAIL_ALERTS_ENABLED=0
DF_FINOPS_DEMO_SEED_ENABLED=0
DF_FINOPS_DEMO_WORKSPACE_ID=demo-corpus
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
```

## SQL 与尚未完成的候选门禁

迁移前置依赖（ODBC 18、Azure CLI credential）可用。现有 SQL Entra 管理员与
受控部署身份匹配，本轮没有替换管理员、修改数据库角色或提升运行时托管身份权限。
使用 SQL 返回的实际出口地址创建单 IP 临时规则后，第一次全量 additive migration
成功；所有临时规则均在 `finally` 中删除，最终残留为 0。

原脚本第二次执行暴露 `notification_setting.test_email_succeeded_at` 的静态
`ALTER TABLE` 重复编译问题。提交 `302091d` 已把该升级改为延迟编译，并先通过
失败测试复现，再通过定向测试及 `1597 passed, 1 skipped` 全量 Python 回归。
修复后的真实第二次迁移仍受本机 NAT 出口地址在相邻 SQL 连接间切换阻断；流程
检测到地址变化后停止，没有扩大网段。候选托管身份随后只读确认三个新增结构均存在。

`demo-corpus` 初始化结果：

- 第一次：153 条事件新建，24 条运行证据新建；
- 第二次：0 条事件新增、153 条同批事件更新，24 条运行证据复用；
- 最终候选只读聚合：198 条事件、1 条 cache hit、75 条 cache miss、8 条失败、
  5 个模型、150 条已计价、48 条未计价、1 个 ROI 场景和 2 条成果证据；
- 最终 revision 的 `DF_FINOPS_DEMO_SEED_ENABLED=0`，重复初始化被拒绝。

登录态端到端候选导航已到达 Microsoft 登录页，但候选 revision 的独立回调域
需要用户完成一次账户登录。自动化没有选择账户、提交凭据或绕过验证，因此 UI
实测和测试邮件仍保持未验收。

切流量前仍需：

1. 从稳定管理员网络或 Azure 内部执行环境，对提交 `302091d` 再执行一次
   `backend.finops.migrate`，补齐真实重复执行证据；不得为此开放网段。
2. 用户在候选 revision 完成一次 Microsoft 登录。
3. 使用真实 `DataForge.FinOpsAdmin` 会话完成预算保存、409 revision conflict
   和一封管理员测试邮件。
4. 完成登录态 candidate desktop/mobile 截图和运营总览、成本、ROI、风险、
   设置页 API 200 验收。
5. 重新检查候选日志、健康和 100/0 流量。
6. 获得用户明确的生产流量切换批准。

## 回滚

当前无需回滚，因为候选流量为 0%。如候选出现异常：

1. 保持生产 backend `ca-dataforge-backend--mb-93210b8bb7f0` 为 100%。
2. 保持生产 web `ca-dataforge-web--mbwebfix93210b8` 为 100%。
3. 停用 `opsdemo302091dfinal` backend 与 `opsdemo302091d` web 候选 revision。
4. 保留 additive SQL 结构作为证据，不因应用回滚 DROP 表或删除数据。

# DataForge 运营管理演示候选验收记录

## 结论

本轮代码实现、本地全量回归、桌面与移动端浏览器验收以及独立代码复审均已通过。
Azure 已创建 backend/web 两个 **0% 流量**候选 revision，当前生产 revision
继续承载 100% 流量。

该候选目前是 **schema-gated candidate**，不是可切流量的功能候选。受控部署者
身份无法建立 Azure SQL DDL 会话，且生产 backend 托管身份的只读元数据探针确认
以下新增结构尚不存在：

- `df_finops.budget_subject`
- `df_finops.notification_setting.test_email_succeeded_at`
- `df_finops.demo_seed_event`

因此成员预算、邮件配置、自动邮件和演示工作区种子在候选中继续关闭。未经完成
additive migration、候选功能验收和用户明确批准，不得切换生产流量。

## 代码与独立复审

- 分支：`codex/operations-demo-readiness`
- 候选代码提交：`3ffacd3523370df9b9322af04a515ba8dc538d1a`
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
| `python -m pytest -q` | `1596 passed, 1 skipped` |
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

两个镜像均从候选代码提交 `3ffacd3523370df9b9322af04a515ba8dc538d1a`
构建：

| 组件 | ACR run | Digest |
|---|---|---|
| backend | `chf6` | `sha256:6c78cc08dd5e44a1a09defa1db05c9cfe8c855b8a33325b490c7ed3dc825e3fb` |
| web | `chf5` | `sha256:4d0edb7d162b8a0de3f9e350b5c01808b79a19acaead1ce7910288e7f4699a67` |

Backend 构建使用 `git archive HEAD` 生成只包含已提交文件的临时上下文；本地测试
结果、工作区临时数据、环境文件和密钥均未进入构建上下文。

## Azure 零流量候选

| 组件 | 生产 revision | 候选 revision | 流量 | 状态 |
|---|---|---|---|---|
| backend | `ca-dataforge-backend--mb-93210b8bb7f0` | `ca-dataforge-backend--opsdemo3ffacd3` | `100 / 0` | 两者 Healthy |
| web | `ca-dataforge-web--mbwebfix93210b8` | `ca-dataforge-web--opsdemo3ffacd3` | `100 / 0` | 两者 Healthy |

候选运行验证：

- Backend `/api/health` 连续三次 `200`。
- Web 匿名访问 `401`，Easy Auth 边界保持。
- Backend/web 候选日志中未检出 traceback、error、常见 API key、Bearer token
  或 secret 标记。
- 未执行生产流量切换。

Backend 候选安全开关：

```text
DF_FINOPS_READ_ENABLED=1
DF_FINOPS_ACTIONS_ENABLED=0
DF_FINOPS_MEMBER_BUDGETS_ENABLED=0
DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=0
DF_FINOPS_EMAIL_ALERTS_ENABLED=0
DF_FINOPS_DEMO_SEED_ENABLED=0
DF_FINOPS_DEMO_WORKSPACE_ID=demo-corpus
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
```

## SQL 与尚未完成的候选门禁

迁移前置依赖（ODBC 18、Azure CLI credential）可用。受控部署者身份的连接尝试
在 SQLSTATE `42000` 阶段失败，统一应用错误为 `finops_schema_migration_failed`；
没有得到任何一次 migration 成功证据。没有修改 SQL Entra 管理员、防火墙规则、
数据库角色或运行时托管身份权限。

生产 backend 托管身份仅用于只读元数据探针，确认三项新增结构均不存在。基于该
证据，本轮没有执行演示种子，也没有启用预算或邮件配置。

切流量前仍需：

1. 由现有 SQL Entra 管理员或已授权 DDL 发布身份连续执行两次
   `backend.finops.migrate`。
2. 再次只读确认三个新增结构存在。
3. 复制 backend 候选并只在候选 revision 中启用成员预算、邮件配置和有界演示
   种子；自动邮件与治理动作仍保持关闭。
4. 对 allowlisted `demo-corpus` 执行初始化两次，验证幂等、缓存 miss→hit、
   不同风险证据和 ROI 计算。
5. 使用真实 `DataForge.FinOpsAdmin` 会话完成预算保存、409 revision conflict
   和一封管理员测试邮件。
6. 完成登录态 candidate desktop/mobile 截图和 API 200 验收。
7. 重新检查候选日志、健康和 100/0 流量。
8. 获得用户明确的生产流量切换批准。

## 回滚

当前无需回滚，因为候选流量为 0%。如候选出现异常：

1. 保持生产 backend `ca-dataforge-backend--mb-93210b8bb7f0` 为 100%。
2. 保持生产 web `ca-dataforge-web--mbwebfix93210b8` 为 100%。
3. 停用两个 `opsdemo3ffacd3` 候选 revision。
4. 保留 additive SQL 结构作为证据，不因应用回滚 DROP 表或删除数据。

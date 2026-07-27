# 候选部署与生产推广操作手册

> 本文档仅供人工执行。Cursor / Agent **不得**自行部署 Azure、切换生产流量或合并 `main`。  
> 资源名、命令与顺序均摘自仓库既有脚本与验收文档，未发明新的 Azure 资源。

**代码基线**：推送后的 `cursor/finops-remediation` 最终 HEAD（以 `git rev-parse HEAD` 为准）。  
**目标资源组**：`rg-dataforge-dev`（`eastus2`）。  
**注册表**：`acrdataforgedev`。  
**应用**：`ca-dataforge-backend`、`ca-dataforge-web`。  
**APIM 作业**：`job-dataforge-finops-apim`（仅在配置与托管身份确认不变后更新镜像 digest）。

参考来源：

- `docs/superpowers/plans/2026-07-26-operations-management-evidence-pricing-routing-history.md`（Task 10–11）
- `docs/validation/2026-07-26-operations-management-production.md`
- `docs/monitoring-azure-state.md`
- `infra/lineage-sql.md`
- `backend/sql/finops_schema.sql`

---

## 0. 人工批准门禁（生产切换前必须勾选）

在切换任何生产流量之前，发布负责人需书面确认：

1. 本地全量回归已通过（pytest / node --test / vite build / playwright / `git diff --check`）。
2. 候选镜像 digest 已记录，且由本手册指定的最终 commit 构建。
3. Additive SQL migration 已执行且临时防火墙规则已删除。
4. Backend / Web 零流量 candidate 均为 `Healthy`。
5. 登录态桌面端与移动端验收通过（含运营管理四页与 APIM 覆盖区）。
6. APIM 回填作业报告 **非零** 样本（`apim_observations` 或 `reconciled_events` > 0；全零不得放行）。
7. `DF_FINOPS_READ_ENABLED=1`，**`DF_FINOPS_ACTIONS_ENABLED=0`**（保持不变）。
8. 回滚目标 revision 名称已记录且仍处于 active / 可切流量状态。
9. 本 PR 已完成人工 Code Review，且 **未** 合并前不得切换流量。

未全部勾选 → **停止**，不得执行第 7 节。

---

## 1. 从最终 commit 构建不可变镜像

在干净工作区（不含未跟踪 workspace fixture、`web/test-results/`、`.superpowers/brainstorm/`）执行：

```powershell
$sha = git rev-parse --short HEAD
az acr build --registry acrdataforgedev --image dataforge-backend:finops-remediation-$sha --file backend/Dockerfile .
az acr build --registry acrdataforgedev --image dataforge-web:finops-remediation-$sha --file web/Dockerfile .
```

记录两个镜像的 **digest**（`sha256:...`）。禁止仅使用浮动 tag 作为生产依据。

---

## 2. Additive SQL migration

本轮新增表（幂等 `IF OBJECT_ID ... IS NULL`）：

- `df_finops.gateway_unmatched_rollup`  
  字段：`scope`（仅 `unattributed`）、`bucket_at`、`status_class`（`client_error_4xx` / `server_error_5xx`）、`request_count`、`data_source`、`updated_at`  
  **不** 存 correlation ID、正文、身份、错误正文。

执行方式（与既有 FinOps migration 一致）：

1. 使用受控部署者身份（非应用运行时托管身份；运行时无 DDL）。
2. 按需添加单 IP 临时 SQL 防火墙规则。
3. 对目标库一次性执行 `backend/sql/finops_schema.sql`（整文件幂等）。
4. **立即删除** 临时防火墙规则并确认不存在。
5. 验证表存在：`df_finops.gateway_unmatched_rollup`。

回滚策略（摘自 `infra/lineage-sql.md`）：应用层优先 revision 回滚；数据库默认保留证据，**不** 因回滚应用而 DROP 该表。

---

## 3. Backend / Web 零流量 candidate

1. 用上述 digest 创建 Container Apps revision，**流量权重 0%**。
2. 等待 revision `Healthy`（可参考）：

```powershell
az containerapp revision list --name ca-dataforge-backend --resource-group rg-dataforge-dev --query "[].{name:name,active:properties.active,traffic:properties.trafficWeight}" -o table
az containerapp revision list --name ca-dataforge-web --resource-group rg-dataforge-dev --query "[].{name:name,active:properties.active,traffic:properties.trafficWeight}" -o table
```

3. Web candidate 的上游指向 Backend candidate（或经验证的兼容 Backend），用于登录态验收。
4. 仅在配置与托管身份确认不变后，将 `job-dataforge-finops-apim` 更新为同一 Backend digest；作业须达到 succeeded，且摘要含非零观测。

确认：`DF_FINOPS_ACTIONS_ENABLED=0` 未在候选配置中被改为开启。

---

## 4. Health 检查

对 Backend candidate 直连 / 经 Web 代理：

- `GET /api/health` → HTTP 200，关键依赖探针健康。
- `GET /api/finops/bootstrap`（登录态）→ 200 或预期 403（角色不足时）。
- Owner：`trust.apim.gateway_unmatched.scope == "unattributed"`（有采集数据时）；`unmatched_metric_records` 为聚合计数。
- Member：不得看到 `gateway_unmatched` 块；`unmatched_metric_records` 应为 `null`。

---

## 5. 登录态桌面 / 移动端验收

桌面（约 1440×1000）与移动（390×844）：

1. 首次进入「运营管理」时导航立即可见（不等待 FinOps API）。
2. 四页切换：运营总览 / 成本分析 / 效能与 ROI / 风险与优化。
3. Token / 成本 / 调用 / P95 切换后柱高、单位、tooltip 同步。
4. APIM 覆盖区展示：已关联请求、未关联网关错误、数据更新时间、`scope=unattributed/system`。
5. 价格关联 / 解除关联后未计价恢复；模型路由 409 后重载最新 revision。
6. 页面与 console **无** `Failed to fetch`、raw `request_ref` / provider ID、未捕获异常。
7. 移动端无横向溢出，关键按钮可点。

截图建议路径（与既有 Playwright 产物一致）：

- `output/playwright/operations-management-overview-desktop.png`
- `output/playwright/operations-management-mobile.png`
- `output/playwright/operations-gateway-evidence-desktop.png`

---

## 6. APIM 非零样本验证

触发或等待 `job-dataforge-finops-apim` 后检查作业 JSON 摘要：

- `apim_observations` 或 `reconciled_events` ≥ 1（窗口内有真实流量时）；
- `gateway_only_errors.total` 可为 0（无网关-only 错误时正常）；
- 若作业成功但观测与对账均为 0 → **验收失败**，不得推广。

Portal Owner 视图中「未归属网关证据」仅在有持久化聚合时显示计数；不得将全局数字展示为某一租户账本。

---

## 7. 流量切换步骤（人工批准后）

顺序（与既有生产记录一致）：

1. **Backend 先切**：将候选 Backend revision 流量调至 100%，确认 health 与 FinOps 读接口兼容。
2. **再切 Web**：将候选 Web revision 流量调至 100%。
3. 不启用治理执行（`DF_FINOPS_ACTIONS_ENABLED` 保持 `0`）。
4. 生产冒烟：桌面/移动强制刷新、运营管理四页、价格映射读路径、路由读路径、AI 历史、请求下钻。

列出 revision 示例命令（权重以当时候选名为准）：

```powershell
az containerapp revision list --name ca-dataforge-backend --resource-group rg-dataforge-dev -o table
az containerapp revision list --name ca-dataforge-web --resource-group rg-dataforge-dev -o table
```

具体 `ingress traffic` 权重调整沿用团队既有 Container Apps 流量切换流程；切换后立即复核 `GET /api/health`。

---

## 8. 回滚目标

切换前记录当前生产 revision 为回滚点（示例命名模式来自既有文档）：

| 角色 | 示例命名模式 |
|------|----------------|
| Backend production（切换前） | `ca-dataforge-backend--<current>` |
| Web production（切换前） | `ca-dataforge-web--<current>` |

回滚：将流量切回上述 revision（保留候选 revision，不要删除）。  
触发回滚条件：health 失败、鉴权失败、证据完整性破坏、前端验收失败、或 APIM 作业异常。

数据库：不 DROP `gateway_unmatched_rollup`；应用回滚后该表可保留为空或历史聚合。

---

## 9. 本轮环境变量（相对既有基线）

| 变量 | 要求 |
|------|------|
| `DF_FINOPS_READ_ENABLED` | `1` |
| `DF_FINOPS_ACTIONS_ENABLED` | **`0`（禁止改为 1）** |
| `DF_FINOPS_SQL_ENABLED` | 生产既有值保持；gateway rollup 仅在 SQL 启用时写入/读取 |
| `DF_FINOPS_HMAC_SECRET` | 既有，不轮换于本手册 |
| 新增环境变量 | **无** |

---

## 10. 执行后记录

在 `docs/validation/` 新建当日 candidate / production 证据文件，并更新 `docs/monitoring-azure-state.md`，至少包含：

- 最终 HEAD / short SHA  
- 镜像 digest  
- candidate / production revision 名与流量  
- SQL migration 结果  
- 测试 totals  
- 桌面/移动截图路径  
- APIM 非零样本计数  
- 回滚 revision  
- 人工批准人与时间  

---

## 11. External provider schema gate（2026-07-27 补充）

启用模型提供商或 Entra 组映射前，必须使用受控部署身份连续执行两次
additive FinOps migration，并确认以下表存在：

- `df_finops.model_provider`
- `df_finops.model_provider_model`
- `df_finops.provider_route_revision`
- `df_finops.entra_group_mapping`

执行入口：

```powershell
python -m backend.finops.migrate
```

两次执行均须成功。`finops_schema_migration_failed`、SQL 权限错误或任一
表缺失都阻断候选部署。不得为 Container Apps 运行时身份授予宽泛 DDL
权限；继续使用受控部署身份，并在验证后立即删除临时 SQL 防火墙规则。

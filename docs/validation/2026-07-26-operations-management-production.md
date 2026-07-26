# 运营管理正式发布验收记录

验收日期：2026-07-26  
代码基线：`03602ad252fc2648ccecbb1f4d19aad100bba3e7`

## 构建产物

- Backend：`dataforge-backend:opsmgmt-03602ad`
  - digest：`sha256:6d35e56989e8200e590fcffe838b43004979046b35ff5a44161390858ad0c823`
- Web：`dataforge-web:opsmgmt-03602ad`
  - digest：`sha256:8d61d290dd06a66d3d43a923f2cfd6d570e2c85d831f80b159aac6a5e75ad13f`
- 两个镜像均由代码基线的干净归档构建，不包含本地未跟踪 workspace fixture。

## 自动化回归

- Python：`1181 passed, 1 skipped`
- Node：`125 passed, 0 failed`
- Vite：production build 通过，共转换 1772 个模块
- Playwright：完整套件 `5 passed`
- 修正 AI 小窗遮挡后的运营管理专项回归：`2 passed`

## 候选发布与数据迁移

- Backend candidate `ca-dataforge-backend--opsmgmt03602adv2` 在零生产流量下达到 `Healthy`。
- Web candidate `ca-dataforge-web--opsmgmt03602adp` 在零生产流量下达到 `Healthy`，并验证其正式上游为 Backend 主入口。
- 新增 `df_finops.budget` 与 `df_finops.saved_view` 表通过一次性部署者权限执行 additive migration。
- 应用运行时托管身份未获得 DDL 权限。
- 迁移使用的单 IP 临时 SQL 防火墙规则已删除并确认不存在。
- `DF_FINOPS_READ_ENABLED=1`；`DF_FINOPS_ACTIONS_ENABLED=0`。

## 候选接口验收

以下请求均通过 Web Easy Auth 和 Backend 代理完成登录态验证：

- `GET /api/health`：200
- `GET /api/finops/bootstrap`：200
- `GET /api/finops/overview`：200
- `GET /api/finops/budgets`：200
- `GET /api/finops/views`：200
- `GET /api/finops/roi/economics?workspace_id=<authorized>`：200
- `GET /api/finops/opportunities`：200
- `GET /api/finops/export.csv?group_by=workspace`：200

ROI 接口验证了响应 workspace 与授权 workspace 一致；响应与页面均未出现 `Failed to fetch`。

## 正式流量与回滚点

- Backend production：`ca-dataforge-backend--opsmgmt03602adv2`，`100%`，`Healthy`
- Backend rollback：`ca-dataforge-backend--finopsmi15d03c9`，保留
- Web production：`ca-dataforge-web--opsmgmt03602adp`，`100%`，`Healthy`
- Web rollback：`ca-dataforge-web--finopsprod8ed23f4`，保留

发布顺序为 Backend 先切换并验证兼容接口，再切换 Web。治理执行未启用。

## 真实生产页面验收

桌面端（1440×1000）：

- 左侧“运营管理”和“运行记录”均可见。
- 首次加载导航可见约 3.6 秒；强制刷新后约 0.1 秒。
- 运营管理 8 个指标全部加载。
- 右上角显示“刚刚更新”，按钮与文本对齐正常。
- AI 为 387×283 的局部小窗，不是整页抽屉。
- 首次加载和强制刷新均无 `Failed to fetch`、错误态、控制台错误或失败请求。

移动端（390×844）：

- 页面无横向溢出，`scrollWidth=390`。
- 运营管理 8 个指标全部加载。
- AI 小窗为 358×282。
- 无错误态、控制台错误或失败请求。

截图：

- `output/playwright/production-operations-management.png`
- `output/playwright/production-operations-management-mobile.png`

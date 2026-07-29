# DataForge FinOps 生产发布记录

## 发布结论

- GitHub PR [#16](https://github.com/beifu12/dataforge-agent/pull/16) 已合并到 `main`。
- Backend `ca-dataforge-backend--mb-93210b8bb7f0` 已承载 100% 生产流量，状态为 `Healthy`。
- Web `ca-dataforge-web--mbwebfix93210b8` 已承载 100% 生产流量，状态为 `Healthy`。
- 旧 Backend `ca-dataforge-backend--deepseekkv29ed8a5` 与旧 Web
  `ca-dataforge-web--extbc697c0` 保留为显式回滚点，当前不承载正流量。
- 生产 Web 原有 `DF_BACKEND_UPSTREAM` 指向旧地址。发布过程中基于同一不可变
  Web 镜像创建了修正配置的 revision，并在零流量状态验证代理健康后才切换流量。

## 自动化与运行时证据

- Python：`1577 passed, 1 skipped`。
- Node：`161 passed`。
- Vite：构建成功，共转换 1780 个模块；保留既有大 chunk 警告。
- Playwright：`30 passed`。
- 数据库迁移专项：`31 passed`。
- `git diff --check origin/main...HEAD`：clean。
- Azure SQL additive migration 连续执行两次成功；应用运行时托管身份未获得 DDL
  权限。
- Backend 切换后连续 5 次公开健康检查均为 `200`，Web 切换后连续 5 次未登录
  入口均为 Easy Auth `401`，同时 Backend 连续 3 次健康检查均为 `200`。
- 登录态生产首页刷新后，业务工作台、运营管理和设置导航一次性可见；页面未再
  显示 `Failed to fetch`、`更新失败` 或“暂时无法连接服务”。

## 安全边界

- `DF_FINOPS_MEMBER_BUDGETS_ENABLED=0`
- `DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=0`
- `DF_FINOPS_EMAIL_ALERTS_ENABLED=0`
- `DF_FINOPS_ACTIONS_ENABLED=0`
- 未发送测试邮件，未创建自动提醒任务，未启用自动治理执行。
- DeepSeek、Bedrock、模型路由和 APIM 的生产执行开关未因本次发布扩大。
- Provider 密钥继续使用写入后不可回显的 Key Vault 持久化边界。

## ACS Email 准备状态

- Azure Communication Services、Email Communication Service 与
  Azure Managed Domain 已创建并完成域关联。
- Backend 系统托管身份仅在目标 Communication Service 范围获得自定义发送
  角色；未创建或读取连接字符串、服务密钥。
- 自动邮件仍关闭。只有 Entra 应用角色、管理员收件人、历史关联和候选邮件验证
  全部通过后，才可单独申请开启。

## 尚未开放的成员预算与邮件提醒

成员预算和邮件配置没有随基础版本上线，原因如下：

1. 当前 Azure 操作身份没有读取或修改应用注册角色的 Microsoft Graph 权限，
   因而无法验证或创建精确应用角色 `DataForge.FinOpsAdmin`，也无法完成管理员
   角色分配验收。
2. 历史身份关联窗口扫描到 50 条模型事件。29 条已与规范 actor reference 一致；
   其余 21 条来自 5 个旧运行，现有账本与保留运行之间出现
   `event_identity_conflict`。发布过程按设计拒绝覆盖，没有用邮箱或原始 ID
   猜测身份，也没有删除冲突证据。

在上述两项解决前，预算、邮件配置、自动邮件和治理动作必须继续保持关闭。

## 回滚

如出现健康、授权范围、数据状态或 UI 回归：

1. 保持全部邮件与治理开关为 `0`。
2. Web 流量回切 `ca-dataforge-web--extbc697c0`。
3. Backend 流量回切 `ca-dataforge-backend--deepseekkv29ed8a5`。
4. 重新检查健康、Easy Auth 与关键错误计数。
5. 保留 additive SQL 表和候选 revision，不删除审计证据。

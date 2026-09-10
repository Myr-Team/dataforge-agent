# DataForge Azure 恢复 Agent 交接说明

## 任务目标

根据仓库中的脱敏清单恢复或重新启动 DataForge，保留认证、租户隔离、审计、FinOps、Trace 和数据完整性。不要因为演示环境而绕过 Easy Auth、Workspace RBAC、FinOps 管理员角色或 Secret 管理。

## 必须先读取

1. `docs/operations/azure-recovery/README.md`
2. `docs/operations/azure-recovery/current-manifest.json`
3. `docs/superpowers/specs/2026-08-07-dataforge-cross-subscription-migration-design.md`
4. `docs/superpowers/plans/2026-08-07-dataforge-cross-subscription-migration.md`
5. `docs/validation/2026-08-11-operations-governance-production.md`
6. `infra/envs/dev` 与 `infra/modules`

历史设计中的资源名称可能已经过期。先执行只读导出并与当前清单比较，不要根据名称相似度删除资源。

## 两条恢复路径

### 资源仍存在，只是停止或缩容

- 先运行 `scripts/azure/resume_dataforge_runtime.ps1` 查看 dry-run。
- 检查 Azure 订阅显示名称和 4 个应用名称。
- 使用 `-Execute` 按 Redis → MCP → Backend → Web 顺序启动。
- 完成登录、依赖和数据检查后，才使用 `-IncludeScheduledJobs` 恢复任务。

### 资源已经删除

- 不要运行 resume 脚本尝试“自动补建”；它会 fail closed。
- 使用现有 Terraform 作为基线，但不要假设它覆盖全部当前资源。
- 用 `current-manifest.json` 补齐 Container Apps、Jobs、SQL、APIM、Search、Foundry、Monitor、Storage、Key Vault、Speech、Content Safety 和 Email。
- 先创建资源和新托管身份，再授予最小 RBAC，最后绑定 Secret 引用。
- SQL/Blob/Search 必须从独立备份恢复；Redis 不迁移旧缓存。
- Easy Auth 回调必须使用新 Web FQDN：`https://<web-fqdn>/.auth/login/aad/callback`。

## 禁止事项

- 不把 Key、Token、连接串、证书、PAT、Cookie 或生产响应写入日志、文件、PR 或 Issue。
- 不把订阅、租户、对象、应用和 principal GUID 写入仓库。
- 不从客户端输入信任 tenant、workspace、actor 或 model identity。
- 不删除带 legal hold 或 immutability 的审计容器。
- 不在未验证 SQL/Blob/Search 数据前删除源数据服务。
- 不在 Web/Backend 验收前启动 FinOps 定时任务。
- 不把 provider accepted 的邮件状态描述成已送达。
- 不自动打开 `DF_FINOPS_ACTIONS_ENABLED` 或外部 Provider 自动生产路由。

## 已知边界

- GitHub 恢复包没有保存任何 Secret 实值或业务数据。
- APIM Policy 可能包含敏感值，不能直接未经审查提交到仓库；需要保存在受控归档并以 Named Value/Key Vault 重新绑定。
- 当前镜像引用可以帮助验证运行当时部署内容，但 ACR 被删除后必须有另一份 Registry 或 OCI archive。
- 当前清单不保存 Azure 资源 GUID，因此重建后应生成新的资源 ID，并重新配置 RBAC/身份关联。
- Foundry deployment 名称已记录，但区域 quota、模型可用性和版本仍需在恢复当天重新检查。

## 交付证据

恢复 Agent 应输出：

- 实际使用的 Git commit。
- Azure 资源数量差异，不包含 GUID。
- Container Apps revision 健康和流量状态。
- 登录态桌面/移动端验收。
- SQL/Blob/Search 恢复校验摘要。
- Foundry、主网关、Redis miss→hit、Trace 和 FinOps 真实样本。
- 定时任务恢复时间与首次成功执行。
- 未恢复项、风险和回滚方式。
- 密钥扫描结果。

未经用户明确批准，不删除源数据资源，不切换域名流量，不启用生产治理执行。

# DataForge Azure 休眠与恢复包

更新日期：2026-09-10

本目录是其他工程 Agent 接手 DataForge Azure 环境时的唯一恢复入口。目标是让项目可以安全休眠，并在保留数据与密钥边界的前提下重新拉起。

## 先读结论

- GitHub 可以保存代码、资源拓扑、镜像引用、缩放参数、任务计划、配置项名称、Secret 引用名和验收步骤。
- GitHub **不能代替** SQL、Blob、Search、Key Vault、APIM Policy 和容器镜像的数据备份。
- 当前清单可用于恢复“仍然存在但已停止”的环境。
- 删除主 SQL、Storage、Search、ACR、Key Vault 或主 APIM 前，必须先完成本文的离线备份门禁。
- 仓库中不保存订阅 ID、租户 ID、对象 ID、连接串、Key、Token、证书、原始用户身份、Prompt、Response 或生产数据。

## 当前环境快照

机器可读快照见 [`current-manifest.json`](current-manifest.json)。快照只使用订阅显示名称，不包含 Azure GUID 标识符。

| 类别 | 当前数量 | 说明 |
|---|---:|---|
| Resource Group | 2 | `rg-dataforge-dev`、`Agent-Demo-Fuzh` |
| Container Apps | 4 | Web、Backend、MCP、Redis |
| Container Apps Jobs | 4 | 3 个定时任务、1 个历史迁移任务 |
| SQL Database | 3 | 1 个主库、1 个 Connector Demo、1 个历史回滚库 |
| API Management | 2 | 1 个生产网关、1 个无 API 的旧网关 |
| Azure AI Search | 1 | Standard，1 replica / 1 partition |
| Azure AI Foundry | 1 | 7 个模型或 Embedding/Image deployment |
| 辅助资源 | 35 | ACR、Storage、Key Vault、Monitor、Speech、Content Safety、Email、Identity 等 |

Container Apps 的恢复顺序固定为：

1. `ca-dataforge-redis`
2. `ca-dataforge-mcp`
3. `ca-dataforge-backend`
4. `ca-dataforge-web`
5. 完成健康检查后再恢复 3 个 FinOps 定时任务

## 其他 Agent 的最短恢复流程

### 1. 获取仓库并阅读状态

```powershell
git clone https://github.com/Myr-Team/dataforge-agent.git
Set-Location dataforge-agent
Get-Content docs/operations/azure-recovery/README.md
Get-Content docs/operations/azure-recovery/current-manifest.json
```

不要从历史文档中的旧资源名称推断当前环境；以 `current-manifest.json` 和新的实时导出为准。

### 2. 登录 Azure 并重新导出一份只读快照

```powershell
az login
& ./scripts/azure/export_dataforge_recovery.ps1 `
  -SubscriptionName 'Microsoft Azure ai1-1' `
  -OutputPath 'output/recovery/azure-live-check.json'
```

该工具只读取经过字段投影的配置，不读取 Secret 实值。若检测到 GUID 或敏感字段，工具会失败而不是生成不完整或不安全的文件。

### 3. 环境仍存在时先预览启动计划

```powershell
& ./scripts/azure/resume_dataforge_runtime.ps1
```

默认是 dry-run，不会修改 Azure。确认计划和资源名称无误后才运行：

```powershell
& ./scripts/azure/resume_dataforge_runtime.ps1 -Execute
```

这一步只启动 4 个 Container Apps 并恢复快照中的副本范围，不自动开启定时任务。

### 4. 验收通过后恢复定时任务

```powershell
& ./scripts/azure/resume_dataforge_runtime.ps1 `
  -Execute `
  -IncludeScheduledJobs
```

不要在 Backend、SQL、APIM、Search 和身份验证尚未通过时启动对账、聚合和保留任务。

## 启动后验收门禁

至少完成以下检查：

- 4 个 Container Apps 均为 Running，最新 revision 健康。
- Web 未登录访问仍执行 Easy Auth，不出现匿名越权。
- 登录后工作区、数据、会话、运行记录、产物、成本管理和风险优化可读取。
- Backend `/api/health` 不因 Foundry、Search、MCP、Speech、Blob、SQL 或 Content Safety 卡死。
- Foundry Agent 和至少一个文本模型完成真实调用，并产生 Trace。
- 主 APIM 完成一次受治理调用，能关联 DataForge request reference。
- SQL 主库可读取，workspace、run、FinOps ledger 和配置 revision 存在。
- Redis 完成一次真实 miss 到 hit，Trace 中 cache evidence 不为空。
- FinOps 对账、rollup 和 retention 各完成一次成功执行。
- Easy Auth 登录、回调 URI、Workspace RBAC、FinOps 管理员权限正常。
- Email 仅在配置已验证时执行测试发送；provider accepted 不等于最终送达。

## GitHub 不包含的内容

以下资产必须保存在 Azure 或另一个受控备份位置：

| 资产 | 删除前必须完成 |
|---|---|
| `df_lineage` | 导出 BACPAC；记录校验和、表数量、行数摘要和恢复演练结果 |
| `df_connector_demo` | 导出 BACPAC，或书面确认可以丢弃 |
| `df_lineage_premerge_20260808` | 导出 BACPAC，或书面确认不再需要回滚副本 |
| Blob Storage | 复制到独立存储；核对 container、blob count、bytes 和抽样 hash |
| 审计容器 | 保留 legal hold/immutability；不得为省钱移除保护策略 |
| AI Search | 保存 index schema、skillset/indexer 配置并验证可以从 Blob/SQL 重建 |
| ACR | 保留当前镜像 digest，或复制到另一个 Registry/离线 OCI archive |
| 主 APIM | 保存 OpenAPI、Policy、Backend、Named Value 名称、JWT/身份结构和自定义域配置；Secret 实值不得进入 Git |
| Key Vault/Container App secrets | 保留 Vault 或写入另一个受控秘密存储；Git 只记录引用名 |
| Easy Auth/Entra | 记录应用显示名、角色、回调 URI 和授权范围；不要记录 tenant/object/client GUID |

## 完整删除后的重建路径

如果资源已经被删除，`resume_dataforge_runtime.ps1` 会在任何写入前因资源缺失而停止。重建 Agent 应执行：

1. 阅读 [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md)。
2. 以 `infra/envs/dev` 和 `infra/modules` 重建仓库已覆盖的资源。
3. 对 Terraform 尚未覆盖的 APIM、SQL、Jobs、Email、Foundry 和 Easy Auth 使用 typed Azure CLI/ARM 操作。
4. 从受控备份恢复 SQL 和 Blob，再重建 Search；Redis 以空缓存启动。
5. 从 Key Vault 或受控秘密存储重新绑定 Secret，不从 Git 查找 Secret。
6. 按上述验收门禁完成真实调用后，再启动定时任务。

`current-manifest.json` 是恢复输入和差异证据，不是可以盲目部署的 ARM Template。

## 更新快照

任何部署、缩放、模型调整或任务计划变更后运行：

```powershell
& ./scripts/azure/export_dataforge_recovery.ps1 `
  -SubscriptionName 'Microsoft Azure ai1-1' `
  -OutputPath 'docs/operations/azure-recovery/current-manifest.json'

python -m pytest tests/test_dataforge_recovery_manifest.py -q
git diff --check
```

提交前必须再次执行仓库密钥扫描。若快照与 Azure 不一致，以 Azure 实时读取为准并更新快照，不要手工猜测。

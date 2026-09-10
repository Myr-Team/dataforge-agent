# DataForge 删除前准备状态

检查日期：2026-09-10

当前状态：**尚不适合删除主数据与主网关资源。** GitHub 脱敏恢复清单已经生成，但数据备份和完整重建演练尚未在本次工作中完成。

## 已完成

- [x] 从当前 Azure 订阅按显示名称读取资源。
- [x] 生成无订阅 ID、租户 ID、对象 ID和 Secret 实值的资源清单。
- [x] 记录 Container Apps 镜像、缩放范围、Ingress、配置项名称和 Secret 引用名。
- [x] 记录 FinOps Jobs 类型、Cron、镜像和配置引用。
- [x] 记录 SQL 数据库名称与 SKU。
- [x] 记录 APIM SKU、容量和 API 名称/路径。
- [x] 记录 Search 容量和 Foundry deployment 名称。
- [x] 提供默认 dry-run 的运行时恢复脚本。

## 删除前仍需完成

- [ ] `df_lineage` BACPAC 已存入独立受控位置，并完成一次恢复演练。
- [ ] `df_connector_demo` 已备份，或确认可以丢弃。
- [ ] `df_lineage_premerge_20260808` 已备份，或确认可以丢弃。
- [ ] Blob 数据完成异地复制和 count/bytes/hash 验证。
- [ ] 审计容器 legal hold/immutability 保持不变并记录剩余保留周期。
- [ ] Search index schema/indexer/skillset 已导出，并完成从权威数据源重建测试。
- [ ] 当前 ACR 镜像已复制到另一个 Registry 或导出 OCI archive。
- [ ] 主 APIM OpenAPI、Policy、Backend、Named Value 名称、JWT/身份结构已保存到受控恢复包。
- [ ] Key Vault/Container App Secret 已有第二受控秘密存储，且恢复过程不打印值。
- [ ] Easy Auth/Entra 回调、角色和授权范围已完成无 GUID 的恢复说明，并验证新环境登录。
- [ ] 完整重建演练通过，并记录回滚时间。

## 可以单独评估的冗余项

以下项目与“完整删除环境”分开处理，仍需在删除当日重新查询依赖：

- 无 API 的 `dataforge-ai-gateway-myr0807`。
- 历史迁移任务 `job-dataforge-finops-migrate`。
- 迁移专用身份 `id-dataforge-sql-migration`。
- 历史回滚数据库 `df_lineage_premerge_20260808`。
- 未被当前应用引用的旧 Application Insights（需先确认不再需要历史日志）。

任何删除操作都应使用当天重新生成的精确清单，逐项执行，不能直接删除整个资源组。

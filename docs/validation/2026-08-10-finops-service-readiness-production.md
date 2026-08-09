# DataForge FinOps 生产就绪记录（2026-08-10）

## 结论

DataForge FinOps 当前版本已部署到新环境并切换 100% 生产流量。成本管理、效能与 ROI、风险与优化、请求证据和运营 AI 均通过接口及登录态浏览器验收。治理执行开关保持关闭，生产页面只提供分析、建议、确认与抑制能力，不自动修改生产配置。

## 生产版本与回滚点

- Backend revision：`ca-dataforge-backend--cacheec2`，Healthy / Running / 100%。
- Web revision：`ca-dataforge-web--finec2`，Healthy / Running / 100%。
- Backend rollback revision：`ca-dataforge-backend--dse9135641`，保留且流量为 0%。
- Web rollback revision：`ca-dataforge-web--dse9135641`，保留且流量为 0%。
- Web build marker：`finops-20260810`；source SHA marker：`ec2fb65`。
- `DF_FINOPS_ACTIONS_ENABLED=0`，未启用治理执行。

生产入口：<https://ca-dataforge-web.grayground-b382bfb9.eastus2.azurecontainerapps.io/>

## 本轮修复

- Redis 使用 Container Apps 同环境服务发现地址，继续从既有安全配置读取认证信息；不在仓库或应用设置中复制密钥。
- FinOps 默认查询窗口按 5 分钟稳定分桶，避免每次刷新因秒级窗口变化造成缓存失效。
- Dashboard 增加短 TTL 与锁防击穿；首屏和运营页面增加骨架及并行预加载，降低空白等待感。
- 风险证据按具体 finding 绑定，避免不同风险项显示相同证据；“问 AI”携带当前指标、风险项和证据上下文。
- 运营 AI 输出使用结论、依据、影响、建议、判断边界的结构化呈现，并修正数据状态契约。
- DeepSeek / 外部模型、模型路由、模型价格映射、未计价恢复、风险扫描和缓存判定均纳入验收链路。

## 真实接口验收

最终候选环境使用登录态授权范围执行完整验收：

- 2,457 次请求，165,816,830 Token，估算成本 `$494.09777289`。
- 预算 `$670`，已用 `$106.1230761`，预算使用率 15.8393%，预测 `$376.3216`。
- P50 2,136 ms，P95 3,510 ms，错误率 3.46%，成功率 96.54%。
- 缓存 eligible 1,929 次，hit 358 次，miss 1,571 次，命中率 18.56%。
- 缓存避免 13,785,603 Token，估算节省 `$68.47102251`。
- 统一入口治理覆盖率 90.11%。
- 趋势包含 30 个桶，成本与 Token 数值具有差异，不使用等高占位柱。
- 部门 5 个、Agent 成本行 11 个；其中 8 个已计价、3 个诚实显示未计价。
- ROI 含 4 项指标、4 个证据阶段、22 个请求证据、4 个趋势点和 1 个情景测算。
- 风险含 4 个领域、6 个矩阵项、6 个优先项、6 个优化项；证据集合彼此区分。
- 价目目录与模型映射各 6 项；Terra 路由、请求详情及运营 AI 证据引用验证通过。

## 性能验收

固定 30 天窗口的后端实测：

| 资源 | 冷请求 | 热缓存 |
| --- | ---: | ---: |
| Bootstrap | 5,373 ms | 718 ms |
| ROI | 21,697 ms | 27 ms |
| Risk | 2,835 ms | 25 ms |

查询缓存 fresh TTL 为 300 秒，stale TTL 为 1,800 秒。生产登录态浏览器中，从成本页切换到已预热的风险页并显示“风险矩阵”耗时 364 ms；页面无 `Failed to fetch`，浏览器错误日志为 0。

首次冷计算仍可能需要数秒到约 22 秒，页面以骨架、缓存结果和后台刷新承接，避免阻塞导航与权限菜单。

## 作业验收

以下作业均已更新到最终 Backend 镜像并手动执行成功：

- `job-dataforge-finops-apim`（每 5 分钟）。
- `job-dataforge-finops-rollup`（每 15 分钟）。
- `job-dataforge-finops-retention`（每日 02:00）。

门户不直接向业务用户展示底层网关产品名称，界面统一使用“统一入口”“治理覆盖”等业务表达。

## 回归结果

- Python：`1841 passed, 1 skipped`。
- Node：`307 passed`。
- Vite：构建成功（1,795 modules；仅保留既有大 chunk 警告）。
- Playwright：`63 passed`，使用独立端口运行，未复用陈旧 preview。
- `git diff --check`：通过。

## 已知边界

- ROI 由 DataForge 自有估算与业务证据计算，不依赖 Azure 实际账单；未验证的业务结果继续标注为情景测算或待验证。
- 可选 Foundry ROI 探针未配置，不影响当前自计算 ROI 页面。
- Key Vault 健康状态为 configured-unverified；应用运行与既有密钥引用正常，但当前操作者未扩大目录或密钥读取权限。
- 首次冷查询可能较慢；300 秒内刷新和跨页面切换优先复用缓存。
- 旧生产修订版继续保留为明确回滚点，在稳定观察期结束前不删除。

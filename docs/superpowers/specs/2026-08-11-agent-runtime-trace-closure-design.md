# DataForge Agent 运行时与 Trace 闭环设计

日期：2026-08-11

状态：已确认设计，待实施计划

范围：Agent 运行时可信性、Foundry External Agent 注册、请求级 Trace 检查器、既有审查缺陷修复

## 1. 目标

本次整改把 DataForge 已存在但未闭环的 Agent、OpenTelemetry、运行记录和证据能力连接起来：

1. 将运行在 Azure Container Apps 的 DataForge 注册为 Microsoft Foundry External Agent。
2. 保证 External Agent 的 `otel_agent_id` 与所有 DataForge 根 Span 的 `gen_ai.agent.id` 一致。
3. 在现有“运行记录”页面内提供同页面 Trace 详情态，不增加一级导航，也不使用遮挡内容的弹窗。
4. 让风险证据和成本异常可以直达对应运行及 Span，并可返回原页面、原筛选和原滚动位置。
5. 修复当前 Agent 工具授权、审计故障语义、Agent 注册清单、MCP 默认地址、诊断和 smoke test 的已确认缺陷。
6. 通过真实 Agent 调用、真实云端 Trace 和浏览器验收后再部署生产流量。

## 2. 非目标

- 不把 DataForge 运行时迁移到 Foundry Hosted Agent。
- 不让 Foundry 代理或托管 DataForge 的业务请求。
- 不新增独立“Trace 中心”一级页面。
- 不采集或返回原始 Prompt、Completion、会话正文、邮箱、原始 Entra 身份、密钥、Provider Response ID 或内部错误正文。
- 不查询或展示 `AppGenAIContent` 中的敏感内容。
- 不把 Foundry External Agent 的预览能力描述为生产 SLA。
- 不因为 Trace 未及时进入 Application Insights 而阻塞本地运行记录显示。

## 3. 已确认的现状与根因

### 3.1 External Agent 缺失

当前代码会在根 Span 和 MAF 参与者 Span 上写入 `gen_ai.agent.id`，但没有创建、验证或维护 Foundry External Agent 版本。`DF_FOUNDRY_AGENT_REGISTERED` 只是手工布尔标记，不能证明 Foundry 中存在匹配注册。

### 3.2 Trace 数据被拆成两套能力

- `run_store.steps` 已能立即生成本地运行时间线。
- Azure Monitor 查询目前只证明远端送达并返回聚合数量，不返回请求级 Span 列表。
- 前端“运行记录”已经有 Agent 分组、完整日志和远端送达状态，但没有统一的 Span 详情与脱敏 JSON 视图。

### 3.3 Agent 运行时缺陷

- Foundry Agent Service 的 `search_pack_context` 信任模型提供的 `workspace_id`，还能回退到 `demo-corpus`，存在越权读取风险。
- Legacy Audit 调用失败时返回 `pass`，属于故障放行。
- 注册脚本只创建六个 Prompt Agent，但运行时还调用 FinOps 和 ROI Agent。
- 多处代码仍包含已失效的旧 MCP Container Apps 域名。
- FinOps/ROI 分析 Agent 吞掉异常，没有安全的错误分类与诊断记录。
- 当前 Agent smoke 实际调用基础 Responses API，没有验证注册后的 Agent 和工具闭环。

## 4. 总体架构

```text
DataForge request
  -> server-authorized workspace context
  -> Agent / MAF / tool execution
  -> run_store safe steps (immediate)
  -> OpenTelemetry spans (async export)
  -> Application Insights connected to Foundry
  -> Foundry External Agent attribution by gen_ai.agent.id

Runs page
  -> local trace immediately
  -> remote span enrichment only after “查看 Trace”
  -> server-side scope binding + redaction
  -> same-page Trace detail view
```

本地运行步骤是即时、可用的产品记录；Application Insights Span 是延迟到达的云端观测证据。两者通过服务端持有的 `workspace_id + run_id + trace_id` 关联，绝不由浏览器提交任意工作区或 Trace ID 发起无边界查询。

## 5. External Agent 生命周期

### 5.1 注册模型

新增独立的 `foundry_external_agent` 模块和受信任 CLI：

- `register`：创建新的 External Agent version。
- `verify`：读取注册，校验 kind、名称和 `otel_agent_id`。
- `status`：输出脱敏健康状态，不输出 Token、连接字符串或资源密钥。
- `delete`：不纳入自动部署；只有显式运维命令和单独确认才能执行。

配置：

- `DF_FOUNDRY_EXTERNAL_AGENT_NAME`，默认 `dataforge-runtime`。
- `FOUNDRY_AGENT_ID`，默认使用安全稳定值 `dataforge-runtime-v1`。
- `FOUNDRY_PROJECT_ENDPOINT`。
- `APPLICATIONINSIGHTS_CONNECTION_STRING`。

注册使用 `AIProjectClient(..., allow_preview=True)` 和 `ExternalAgentDefinition(otel_agent_id=...)`。`azure-ai-projects` 升级到官方 External Agent 接口所需版本。注册是部署步骤，不放在 Web 或 Backend 启动路径中，避免控制面暂时不可用导致应用启动失败。

### 5.2 状态语义

系统状态不再相信 `DF_FOUNDRY_AGENT_REGISTERED`：

- `registered`：Foundry 返回匹配 kind 和 `otel_agent_id`。
- `mismatch`：存在同名但 kind 或 ID 不一致。
- `not_registered`：Foundry 中不存在。
- `unavailable`：身份、网络或控制面暂时不可用。
- `not_configured`：缺少必要配置。

状态查询使用短 TTL 缓存，避免每次打开页面都访问 Foundry。

## 6. 统一 Trace 查询服务

### 6.1 公共接口

新增：

`GET /api/runs/{run_id}/trace-view?include_remote=1`

响应包含：

- `run`：客户可读名称、workspace、操作类型、时间和状态。
- `summary`：总耗时、Span 数、Token、估算成本、缓存和错误数。
- `source_state`：`local_available`、`remote_confirming`、`remote_confirmed`、`remote_unavailable`。
- `external_agent`：名称、匹配状态和安全深链。
- `spans`：有序 Span 列表，每项包含安全摘要和 `technical_json`。
- `return_context`：前端内部路由使用，不接受任意外部 URL。

现有 `/api/runs/{run_id}/trace` 保持兼容，继续返回本地步骤投影。现有远端送达和聚合指标接口保持兼容，但由统一 Trace 服务复用。

### 6.2 云端查询

查询仅针对已通过当前用户 `run.read` 校验且属于当前 workspace 的 Run。服务端从 Run 读取受信任 Trace ID，并使用 workspace/run/correlation 哈希再次限定 KQL。

查询表限定为：

- `AppRequests`
- `AppDependencies`
- `AppTraces`
- 必要时 `AppEvents`

不查询 `AppGenAIContent`。KQL 只 project 白名单列和白名单 Properties，按 `OperationId`、`ParentId`、时间和 Span ID 排序。单个 Run 最多返回 200 个 Span，单个技术 JSON 限定大小，查询时间窗由 Run 的开始和完成时间计算并限制在保留期内，不使用无限窗口。

### 6.3 本地与远端合并

新运行会在安全的 `run_store.steps` 记录中持久化：

- `trace_id`
- `span_id`
- `parent_span_id`
- Agent ID
- 事件类型
- 证据引用

这些字段都是不透明技术引用，不包含业务正文。远端 Span 到达前，页面用本地步骤立即渲染；到达后按 Span ID 精确合并。旧记录没有 Span ID 时，只展示本地时间线和根 Trace JSON，不做不可靠的时间猜测关联。

## 7. 脱敏与授权

### 7.1 字段白名单

可展示字段：

- Trace/Span/Parent Span ID
- Span name、状态、时间、耗时
- External Agent ID、MAF participant Agent ID
- 模型 deployment、Token 分类、估算成本 revision
- 工具名称、工具状态、命中数量
- Cache state
- Evidence refs
- 规范化错误类别和 HTTP 状态类

明确禁止：

- Prompt、Completion、会话正文
- 工具原始参数和结果正文
- 原始邮箱、Entra object ID、tenant claim
- Key、Token、SAS、连接字符串
- Provider 原始 Response ID
- 内部异常正文和 traceback

禁止字段即使出现在 telemetry Properties 中也必须在服务端投影时丢弃；不是在浏览器端隐藏。

### 7.2 权限

- 具备 `run.read` 的 workspace 成员可查看客户可读时间线。
- 具备 `finops.trace.read` 的 owner/admin 可查看脱敏 `technical_json`、复制 JSON 和打开 Foundry 深链。
- 任意跨 workspace Run 返回 404，避免泄漏资源存在性。
- 浏览器传入的 correlation/trace/workspace 标识不作为查询授权依据。

## 8. 前端交互

### 8.1 入口

不新增一级导航。现有“运行记录”保持主入口：

- 每条运行显示轻量“查看 Trace”动作。
- 点击后在同一个 `RunsCenter` 主内容区切换为 Trace 详情态。
- 左上角“返回运行记录”恢复列表、筛选、分页和滚动位置。
- 风险证据使用“追踪本次调用”，成本异常使用“查看调用”，进入同一详情态并保留来源返回上下文。

### 8.2 详情态

- 顶部：客户可读运行名称、时间、External Agent、远端确认状态。
- 摘要：总耗时、Span 数、Token、估算成本和结果。
- 左侧：可滚动 Span 时间线。
- 右侧：所选 Span 的脱敏 JSON。
- JSON 固定可视高度、保持原始缩进、禁止自动换行、支持上下和左右滚动。
- 所有白色背景文字使用明确深色；状态使用可读颜色和文字双编码。
- 支持复制脱敏 JSON和跳转 Foundry；不提供原始 JSON 下载。

### 8.3 加载与错误

- 首屏只加载本地 Trace，不等待 Azure Monitor。
- 进入详情态后才请求远端 Span；同一 Trace 使用 30–60 秒查询缓存和锁防击穿。
- 云端尚未摄取时显示“云端确认中”，本地时间线仍正常使用。
- 权限不足显示无技术详情权限，不回退到不安全查询。
- 云端限流或失败显示安全错误类别和手动重试，不展示 SDK 错误正文。
- 页面不进行常驻高频刷新。

## 9. Agent 运行时整改

### 9.1 Workspace 工具边界

`run_agent` 和 Foundry tool loop 必须接收服务端授权后的 workspace context。模型工具参数中的 `workspace_id` 被忽略或与受信任值严格比对；不再回退到 `demo-corpus`。

### 9.2 Audit 故障关闭

Audit Provider/Agent 不可用时抛出类型化 `AuditUnavailableError`，Run 进入明确错误或待复核状态，不能产生 `pass`。没有可审计证据的预检查语义另行保留，但必须标记为 `insufficient_data`，不能伪装成模型审计成功。

### 9.3 Agent 注册单一事实源

统一 Prompt Agent 清单，包含 Coordinator、Corpus、Feasibility、Market、Producer、Auditor、FinOps、ROI。构建、验证、运行时 allowlist 和 smoke 都读取同一清单，防止再次漂移。

### 9.4 MCP 配置

移除失效旧域名默认值。Market MCP 只有显式配置 `MCP_MARKET_URL` 时才启用；缺失时返回可识别的 `not_configured`，并按既有边界降级到 workspace evidence 或已配置的 Foundry web search。

### 9.5 诊断与 Smoke

- FinOps/ROI Agent 异常记录安全的 `agent_id`、错误类别、状态码和 latency，不记录输入输出正文。
- `verify` 读取每个已注册 Agent 并校验定义。
- `smoke` 通过 `agent_reference` 调用实际注册 Agent；带工具 Agent 使用受信任 workspace fixture 验证工具闭环。
- External Agent 另做 registration verify 和真实 Trace 验证，不能用基础 Responses 成功代替。

## 10. 测试与验收

### 10.1 后端测试

- 模型提供其他 workspace 或省略 workspace 时不能跨租户搜索。
- Audit 故障不能返回 pass。
- 八个 Prompt Agent 构建、验证、运行时清单一致。
- 未配置 MCP 时不访问旧域名。
- External Agent 注册、重复注册、ID mismatch、无权限和不可用状态。
- KQL 同时绑定 workspace/run/trace，限制表、时间窗、行数和字段。
- 敏感字段在任意嵌套深度都不出现在 Trace API。
- 旧 Run、远端延迟、远端限流、部分结果和 90 天外记录正确降级。

### 10.2 前端测试

- 运行列表点击后同页面进入详情并可返回原位置。
- 风险和成本入口定位正确 Run/Span，并返回原页面。
- 本地 Trace 先显示，远端请求不阻塞页面。
- JSON 保持缩进、不换行、固定区域上下/左右滚动。
- 390px 移动端无横向页面溢出，JSON 只在自身容器滚动。
- 普通成员看不到技术 JSON和 Foundry 深链。
- 深色文字、状态、键盘操作和焦点可见性验收。

### 10.3 集成验收

1. 运行真实多 Agent 分析，确认 Run、Trace 和 Span 引用落库。
2. 在 DataForge 中立即打开本地 Trace。
3. 等待云端摄取后确认远端状态和 Span JSON补充成功。
4. 在 Foundry External Agent 的 Traces 中找到同一 Trace ID。
5. 从一条风险证据进入并定位到产生该证据的 Span。
6. 从一条成本异常进入并看到一致的模型、Token、缓存和估算成本。
7. 用普通成员和管理员分别验证权限差异。
8. 运行全量 Python、Node、Vite 和 Playwright 门禁。

## 11. 部署顺序

1. 在隔离分支完成 TDD 实现和全量回归。
2. 构建不可变 Backend/Web 镜像并部署零流量 candidate。
3. 运行 External Agent `register` 和 `verify`。
4. 对 candidate 发起真实多 Agent 调用，完成本地与远端 Trace 验收。
5. 使用登录态桌面和移动端完成 Trace 详情、返回上下文、风险和成本直达验收。
6. 确认生产健康、回滚目标和无密钥提交后切换流量。
7. 切流后再次执行真实 Trace 和关键页面 smoke；异常则立即回滚到上一健康 revision。

## 12. 成功标准

- Foundry 中存在真实、匹配的 DataForge External Agent，而不是手工布尔标记。
- DataForge 页面能按一条运行逐 Span 查看脱敏 JSON，并与 Foundry Trace ID 对应。
- Trace 功能不拖慢运行记录首屏。
- 证据和成本项能定位到自身对应 Run/Span，不再复用通用内容。
- 跨 workspace 工具调用被阻断，Audit 故障不放行。
- Agent 清单、注册、运行和 smoke 一致。
- 所有自动化测试与候选/生产验收通过，并保留可复现实证。

## 13. 参考

- Microsoft Foundry：[Register external agents for observability and evaluation](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/register-external-agent)
- Microsoft Foundry：[Set up tracing for AI agents](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup)
- Microsoft Foundry：[Tracing and data handling](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-data)

# DataForge AI FinOps Portal

FinOps Portal 是 DataForge 面向 IT 与财务人员的运营视图。它不替代工作区、
数据资产、会话与产物，也不把运行记录重新复制成一套审计产品。

Portal 只回答四类问题：

1. **运营总览**：当前调用、Token、估算成本、成功率、延迟和网关覆盖是否正常。
2. **成本与预算**：成本由哪些部门、工作区、Agent 和模型产生，预算使用到哪里。
3. **效能与 ROI**：已验证结果带来了什么价值；证据不足时明确显示“证据不足”。
4. **风险与优化**：哪些异常需要关注，以及可创建哪些待审批治理草案。

页面进入前会预加载一个有响应预算的 bootstrap 请求；切换到 Portal 时复用缓存结果。
Portal 每 60 秒只刷新已保存的聚合数据与分析结论，不会因此触发模型调用。

## 证据体验与隐私边界

- 运营指标可按需打开请求证据抽屉，但不提供独立“请求追踪”或“审计溯源”页面。
- 客户默认看到稳定的业务名称，例如
  `Commerce · 分析运行 · 7月24日 10:42`；技术 ID 只在折叠的“技术信息”中显示。
- 友好名称由 tenant、workspace、操作类型与发生时间生成并持久化，同一证据不会因刷新改名。
- Owner 或管理员可查看 DataForge 应用层已记录的用户请求与最终可见回答。
- 不采集或展示 system prompt、provider 原始响应、密钥、原始身份和内部错误正文。
- Foundry Trace 与 Azure Monitor 链接由服务端模板和域名白名单生成，客户端不能提交任意 URL。
- 应用事件和 APIM 记录通过 correlation 对账，绝不把同一次调用的 Token 或成本相加两次。

## FinOps Agent 与 ROI Agent

两个 Agent 嵌入相应业务页面，不提供独立聊天或 Agent 中心：

- **FinOps Agent**解释成本变化、预算压力、缓存和模型使用情况。
- **ROI Agent**只使用已验证 outcome 事件；证据不足时不调用模型、不生成 ROI 结论。
- 输入只包含当前 tenant、授权 workspace、所选窗口的聚合指标和证据引用。
- 输出采用结构化契约，引用的证据必须来自本次输入白名单。
- Agent 可建议并创建 typed action 草案，但不能提交、批准或执行。
- 定时异常/结果事件可触发后台分析；60 秒页面刷新不会触发 Agent。

## 安全上线顺序

1. 以零生产流量部署 backend candidate，并保持
   `DF_FINOPS_READ_ENABLED=0`、`DF_FINOPS_ACTIONS_ENABLED=0`、
   `DF_FINOPS_SQL_ENABLED=0`。
2. 配置 Azure SQL 托管身份访问，执行
   `python -m backend.finops.migrate`。
3. 设置 `DF_FINOPS_SQL_ENABLED=1`，用真实调用验证应用事件即时入账。
4. 每 5 分钟运行 `python -m backend.finops.apim_backfill`，每小时运行
   `python -m backend.finops.rollup_refresh`，每日运行
   `python -m backend.finops.retention`；任务窗口可重叠且必须保持幂等。
5. 完成权限、价目表、correlation、Portal 和证据抽屉验收后，才可设置
   `DF_FINOPS_READ_ENABLED=1`。
6. 在 APIM candidate 与一条 DataForge 模型/缓存动作完成异人审批、验证和回滚前，
   始终保持 `DF_FINOPS_ACTIONS_ENABLED=0`。

这些步骤不授权切换生产流量。

## 数据与执行边界

- Azure SQL 保存请求事实、聚合、映射、价目表、证据别名、异常、分析结论、
  策略、审批和动作状态。
- Redis 只缓存有边界的查询结果，最长
  `DF_FINOPS_QUERY_CACHE_TTL_SECONDS`。
- 请求事实默认保留 90 天。
- APIM 目标只由 `DF_FINOPS_APIM_TARGETS_JSON` 提供；公共管理接口不接受
  Azure 资源坐标、任意 XML、脚本或资源 ID。
- APIM executor 生成自己的 typed policy，在候选 revision 上验证托管身份 200、
  匿名 401 及策略 hash 后才允许激活。
- 模型路由与缓存动作执行前校验 `base_version`；配置漂移返回 409 并重新审批。
- 当前成本是 DataForge 价目表估算，不代表 Azure 实际账单或对账结果。

## 实测门禁

在候选环境保留以下证据后，才能申请生产启用：

- 多 Agent、多模型成功、4xx、5xx 和慢请求样本；
- APIM correlation 与 DataForge 请求证据联查；
- 一笔按价目表 revision 手算的成本与 Portal 一致；
- 同 workspace 同分析的真实 Redis miss → hit；
- member 无法读取未授权 workspace 或组织级人员明细；
- 一条异常完成触发、确认与解决；
- 一条生产动作完成异人审批、候选执行、验证与回滚；
- Python、Node、Vite、桌面与移动端浏览器回归全部通过；
- backend/web candidate 均 Healthy 且保持零生产流量。

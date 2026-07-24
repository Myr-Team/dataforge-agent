# DataForge FinOps 运营驾驶舱重构设计

日期：2026-07-24
状态：已通过对话与视觉原型确认

## 1. 背景与问题

DataForge 已通过工作区、数据资产、会话与运行、分析产物承载业务操作与结果。现有治理和 FinOps 页面又把运行、Agent、模型、趋势、审计、请求等信息拆成多个入口，造成以下问题：

- Portal 与工作区已有功能重复。
- 技术记录主导导航，IT 与财务用户难以快速判断页面用途。
- 审计与溯源只有扁平事件列表，缺少业务请求、响应结果和处理链路。
- 请求、run、APIM correlation、价目表 revision 等技术编号直接面向客户，理解成本高。
- Agent 能力如果继续按独立页面堆叠，会再次增加导航与学习成本。

重构后的 Portal 定位为 IT 与财务共同使用的运营驾驶舱，而不是第二套工作区或日志浏览器。

## 2. 目标与非目标

### 2.1 目标

- 用一套共同指标回答成本、预算、运行健康、业务价值和风险问题。
- Portal 内部只保留四个职责清晰的页面。
- 请求、Agent、模型和审计信息作为指标下钻证据，不再单独占据一级页面。
- 引入 FinOps Agent 与 ROI Agent，提供有证据的解释、归因和建议。
- 用客户友好名称展示请求和关联证据，技术 ID 收进详情。
- 保持当前 MAF、工作区、会话、数据和产物内核不变。

### 2.2 非目标

- 不接入 Azure 实际账单或 Cost Management 对账。
- 不让 Agent 自动批准或执行生产变更。
- 不新增独立 Agent 中心或全局聊天助手。
- 不采集或展示 system prompt、Provider 原始请求/响应、密钥、原始身份和内部错误正文。
- 不用示例值补齐缺失的 Token、成本、价值或 ROI。
- 不删除底层不可变审计能力；只取消其默认客户导航入口。

## 3. 产品信息架构

DataForge 主导航收敛为：

- 业务工作台
  - 工作区
  - 数据资产
  - 会话与运行
  - 分析产物
- 运营治理
  - 运营驾驶舱
- 系统
  - 设置

“审计与溯源”“请求追踪”“Agent 与模型”“使用趋势”不再作为 Portal 一级入口。原有后端审计、run trace 和请求账本继续保留，用于权限校验、证据下钻、合规和排障。

运营驾驶舱内部只有四个页面：

1. 运营总览
2. 成本与预算
3. 效能与 ROI
4. 风险与优化

统一下钻路径为：

`指标或异常 → 部门与专案 → Agent 与模型 → 请求证据抽屉`

## 4. 页面设计

### 4.1 运营总览

首屏只保留六个核心指标：

- 估算成本
- 预算使用
- 调用次数
- 成功率
- P95 延迟
- APIM 覆盖率

主体区域包含：

- 成本与调用趋势。
- 需要关注的预算、延迟和 APIM 覆盖问题。
- 部门成本与运行质量。
- 价值证据和优化机会摘要。

全局筛选包含时间、部门、专案、Agent 和模型。右上角使用自然语言“1 分钟前更新”，不使用“数据新鲜度”等后台术语。

### 4.2 成本与预算

该页面只回答“钱花在哪里，是否会超预算”：

- 估算成本、预算使用、月末预测、未计价比例。
- 成本与预算阈值趋势。
- 部门、专案、Agent 和模型成本归因。
- 价目表版本和计价覆盖缺口。

请求明细不直接平铺；点击成本、未计价比例或归因项后进入下钻。

### 4.3 效能与 ROI

该页面只回答“这些消耗是否产生可证明的价值”：

- 已记录业务价值。
- 可复核 ROI。
- 每次成功成本。
- 已确认节省。
- 成本与价值趋势。
- 结果事件覆盖率和证据缺口。

ROI 只使用已验证结果事件。没有结果证据时显示“未记录”或“证据不足”，不把估算值伪装成实测值。

### 4.4 风险与优化

该页面只回答“现在什么问题需要人采取行动”：

- 开放异常、严重异常、影响成本、治理覆盖率。
- 预算、错误率、延迟、计价和 APIM 覆盖异常。
- FinOps Agent 和 ROI Agent 分析卡。
- 优化建议及“创建治理草案”入口。
- 已创建动作的审批、验证和回滚状态。

## 5. FinOps Agent 与 ROI Agent

### 5.1 职责

FinOps Agent：

- 解释成本变化。
- 定位成本和异常驱动因素。
- 发现缓存、模型路由和预算优化机会。
- 生成类型化治理草案。

ROI Agent：

- 关联成本与已验证结果事件。
- 计算可复核 ROI 和每次成功成本。
- 解释价值变化。
- 列出结果证据缺口。

### 5.2 入口

两个 Agent 都以内嵌分析卡出现：

- FinOps Agent 位于成本与预算、风险与优化页面。
- ROI Agent 位于效能与 ROI、风险与优化页面。
- 不新增 Agent 中心。
- 不新增全局 Copilot 聊天框。

### 5.3 触发方式

- 新异常创建或状态发生重要变化时触发 FinOps Agent。
- 新的已验证结果事件出现时触发 ROI Agent。
- 用户可手动点击“重新分析”。
- Portal 每 60 秒刷新数据，但不会每 60 秒调用 Agent。

### 5.4 结构化输出

Agent 输出保存为 `FinOpsInsight`，至少包含：

- `insight_id`
- `agent_kind`：`finops` 或 `roi`
- `tenant_ref`、授权 workspace 范围、时间窗口
- `trigger_type` 和触发对象
- `title`、`summary`、结构化 findings
- `evidence_refs`
- `evidence_state`、confidence
- `source_revisions`
- `generated_at`、`expires_at`
- `status`：`ready`、`insufficient_data`、`failed`、`stale`

Agent 只能通过受控只读查询服务获取账本、异常、价目表和结果事件。它不能读取其他租户或未授权 workspace，也不能执行任意 SQL、脚本、APIM XML 或资源操作。

Agent 可以创建类型化 `draft` 治理动作，但不能 submit、approve、execute 或 verify。

## 6. 请求证据抽屉

请求证据抽屉从指标、异常或 Agent 结论进入，关闭后回到原运营上下文。抽屉展示：

- 进入原因，例如“来自 Commerce 成本异常”。
- 客户友好请求名称和响应状态。
- 延迟、Token、估算成本、缓存状态。
- DataForge 已保存的应用层业务请求。
- 最终用户可见业务回答，可按需展开。
- APIM、MAF、Agent、模型和返回阶段组成的处理链路。
- 模型、路由、价目表和证据状态。
- 受权限保护的技术 ID。
- 服务端验证过的 Foundry Trace 深链。

应用层业务请求与最终业务回答不同于 Provider 原始 prompt/completion。后者仍不采集、不展示。

## 7. 客户友好命名与技术关联

### 7.1 显示规则

后台按受控规则生成显示名：

`工作区名称 · 操作类型 · 日期时间`

例如：

`Commerce · 分析运行 · 7月24日 10:42`

操作类型来自服务端 allowlist，例如：

- 分析运行
- 会话跟进
- 模型调用
- 工具调用
- 缓存评估
- 结果验证
- 治理动作

模型不能自由生成技术对象名称。没有可用工作区名称时使用安全的“工作区”回退值；没有已知操作类型时使用“操作记录”。

### 7.2 关联模型

新增稳定的 `FinOpsEvidenceAlias` 关联记录：

- `tenant_ref`
- `workspace_id`
- `object_kind`
- 内部 `object_ref`
- `operation_code`
- `workspace_name_snapshot`
- `display_name`
- `occurred_at`
- `created_at`

唯一键为 `tenant_ref + workspace_id + object_kind + object_ref`。显示名重复时由日期时间区分。显示名变化不会改变 request_ref、run_id、trace_id 或 APIM correlation。

### 7.3 客户展示

默认展示：

- `Commerce · 分析运行 · 7月24日 10:42`
- `主分析流程`
- `统一 API 网关`
- `GPT-5.6 主路由`
- `2026 年 7 月标准价目表`

技术信息折叠区才展示：

- request_ref
- run_id
- trace_id
- APIM correlation
- price_card_revision

列表接口不返回不必要的技术 ID。详情接口在服务端完成权限判断后才返回可复制 ID 和 Trace 链接。

## 8. 数据流与服务边界

数据流保持：

`应用事件 + APIM 事件 → 对账与规范化 → FinOps SQL 账本 → 聚合与查询 → Portal`

扩展后的读取路径为：

1. 应用事件完成后立即写入请求事实。
2. APIM 事件按 correlation 补充治理和用量证据，绝不与应用数据重复相加。
3. 写入请求事实时创建或获取稳定 `FinOpsEvidenceAlias`。
4. 查询服务返回四个页面所需聚合，不在客户端重算成本或 ROI。
5. 异常或已验证结果事件触发 Agent 分析。
6. Agent 仅保存结构化 insight 和 evidence_refs。
7. 点击指标或 insight 时，详情服务按权限解析业务请求、响应、链路、友好名称和技术关联。
8. Foundry Trace 链接由服务端根据受信配置和 trace reference 构造或读取；客户端不能提交任意 URL。

建议新增接口：

- `GET /api/finops/insights`
- `POST /api/finops/insights/analyze`
- `GET /api/finops/requests/{request_ref}` 增加 display、business_request、business_response、timeline、technical_refs 和 links

已有 overview、breakdowns、trends、anomalies、recommendations 和 actions 接口继续作为底层能力，Portal 只重组其呈现方式。

## 9. 权限与隐私

所有接口继续从受信 Easy Auth 声明推导 tenant，并在服务端收窄 workspace 范围。

权限使用能力键表达，不新增硬编码的“财务”身份类型：

- `finops.summary.read`
- `finops.cost.read`
- `finops.roi.read`
- `finops.request_detail.read`
- `finops.trace.read`
- `finops.action.draft`

这些能力可由现有工作区角色或受信 Entra 组映射。客户端只消费服务端能力投影，不能自行推断权限。

财务能力集默认允许：

- 读取授权范围内的成本、预算、ROI 和聚合趋势。
- 查看证据状态、覆盖率和 Agent 汇总结论。

财务能力集默认不允许：

- 查看业务请求正文或最终业务回答。
- 查看 request_ref、run_id、trace_id、APIM correlation。
- 打开 Foundry Trace。

工作区 Owner 或 IT 管理员在拥有相应 workspace 权限时可以：

- 展开业务请求和最终业务回答。
- 查看技术 ID。
- 打开 Foundry Trace。
- 创建治理草案。

生产动作继续要求异人审批；紧急回滚规则保持不变。Agent 永远不能成为批准人或执行人。

## 10. 缺失数据与失败处理

- 证据样本不足时，Agent 返回 `insufficient_data`，列出缺口，不生成推测建议。
- Agent 调用失败不影响 Portal 数据读取；界面保留上次成功 insight，并标记“分析结果已过期”。
- 数据更新时间与 Agent 分析时间分别展示。
- 没有成本、Token、业务价值或 Trace 时显示“未记录”“部分记录”或“未配置”。
- 友好名称生成失败时回退为“工作区 · 操作类型 · 时间”。
- Foundry Trace 未配置或未经验证时不返回链接。
- 技术 ID 关联失败时不猜测，不把其他请求的 ID 拼接进详情。
- 治理草案创建失败只影响该动作，不影响异常和 insight 阅读。

## 11. 测试与验收

### 11.1 后端

- 名称按 workspace、操作类型和时间稳定生成。
- 重复名称不会造成错误关联。
- workspace 改名不改变底层技术关联。
- 跨租户和未授权 workspace 访问被拒绝。
- 财务能力集无法读取正文、技术 ID 或 Trace 链接。
- Owner/IT 管理员只能读取已授权 workspace 的请求详情。
- Foundry Trace 链接只能来自服务端受信配置。
- Agent insight 的每个结论都能关联 evidence_refs。
- 证据不足、Agent 失败、过期 insight 和缺失价目表均返回明确状态。
- Portal 刷新不会隐式触发 Agent。
- Agent 不能 approve 或 execute 动作。

### 11.2 前端

- 主导航只保留一个“运营驾驶舱”入口。
- Portal 内只显示四个页面。
- 首屏六个指标、筛选和“1 分钟前更新”在 desktop/mobile 正常呈现。
- 请求、Agent、模型和审计信息只通过下钻打开。
- 客户友好名称优先于技术 ID。
- 技术 ID 默认折叠。
- 财务角色不渲染正文、ID 或 Trace 入口。
- 缺失值和过期 insight 不显示伪造数字。
- 请求证据抽屉支持键盘关闭、焦点约束和移动端布局。

### 11.3 真实门禁

- 用真实多 Agent、多模型调用验证四页聚合。
- 从成本或异常下钻到正确业务请求和响应。
- 友好名称能解析到正确 request_ref、run_id 和 APIM correlation。
- Foundry Trace 深链打开对应 trace。
- FinOps Agent 的成本归因可由请求账本复核。
- ROI Agent 的结果只包含已验证结果事件。
- 财务用户无法读取正文；IT 管理员可以读取授权 workspace 的详情。
- 一条建议只能创建 draft，不能绕过审批直接执行。
- Python、Node、Vite 和 Playwright 回归通过。
- 未经用户明确批准，不切换生产流量，不启用生产治理执行。

## 12. 迁移原则

- 保留已有 FinOps 请求账本、查询、异常、建议和动作能力。
- 移除或隐藏重复的 Portal 页面，不删除其底层查询接口。
- 先完成四页信息架构和角色可见性，再接入友好命名和证据抽屉，最后启用两个 Agent。
- 已有请求没有 alias 时按确定性回退规则惰性补齐。
- `DF_FINOPS_ACTIONS_ENABLED` 继续默认关闭。
- 候选环境验收完成前保持零生产流量。

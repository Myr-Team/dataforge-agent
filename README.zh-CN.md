# DataForge —— 把沉睡的数据变成产品机会

[English](./README.md)

**DataForge 是一个把企业「现有数据」商机化的多 Agent 系统** —— 并进一步用证据量化每个机会到底可不可行。把业务数据上传进来，多个专家 Agent 协作，发现非显而易见、有高价值的产品方向，按五维标准给可行性打分，并一键产出交付物（PDF 提案、概念图、语音摘要）。

> 为微软 GCR *AI Agent Frontier 黑客松* —— **赛道 B（Pro Code）** 而建。编排逻辑、可行性引擎、护栏全部用代码写成，没有任何低代码拖拽自动化。

---

## 痛点

企业手里堆着海量数据 —— 实地信号、交易流水、设备日志、周边环境…… 但除了本职业务，**没人说得清这些数据还能做成什么。** 养一支数据科学团队去探索又贵又慢，于是多数数据就这么沉睡。

催生这个项目的，是一个朴素的念头：

> *「我有这么多数据，却发现不了别的商机 —— 那就把它全丢进来试试，**量化**一下我的数据到底值多少。」*

DataForge 把这件事自动化了。

**启发它的真实案例：** 一家做楼层级防丢失硬件的公司，手里全是实地信号数据，本来只当定位用 —— 直到被点醒：这些信号其实是「位置 / 人流情报」，于是基于它做了个开店**选址 App**，结果爆火。DataForge 就是要从你已经拥有的数据里，挖出这种意料之外、却又站得住脚的「第二曲线」。

---

## Agent 执行链路

```
上传数据 → 自动画像 + 建索引
        → 多 Agent 分析（检索 → 分析 → 市场）
        → 审计 ⇄ 复修 回流（Microsoft Agent Framework）
        → 有据可查的可行性结论（含引用）
        → 一键产出交付物（PDF / 概念图 / 语音）
        → 回填真实试点指标 → 迭代逼近公司重点方案
```

整个推理过程通过 SSE 实时流式呈现在界面上 —— 你看到的是「推理在发生」，而不只是最终答案。

## PPT / Agent 简报

其他 Agent 如果要理解这个应用、产出介绍 PPT，优先看这一段。

**一句话定位：** DataForge 是一个面向企业数据商机化的 Pro-Code 多 Agent 产品工作台：把已有文件、表格和外部数据连接转成有证据支撑的商业机会，再完成审计、讨论、产物生成和版本迭代。

**核心演示故事：** 楼层级防丢硬件 / IoT 信号数据，原本只用于定位；DataForge 将它重新识别为位置与人流情报，判断是否能产品化为「快闪店 / 小店选址建议」服务，给出证据、风险、低成本试点，生成项目 PDF 与概念图，并在回填试点指标后从 v1 迭代到 v2。

**5 分钟视频建议路线：**

1. 打开默认工作区，介绍左侧导航：工作区、数据、运行记录、会话、产物、设置。
2. 上传或选择开店选址数据，展示数据工作台里的文件库、字段质量、表格编辑与 Markdown 补充。
3. 点击自动分析，展示多 Agent 执行链路与审计员如何复修 / 降档。
4. 在会话中追问方案，Agent 应给出可校准的暂定建议、证据缺口和低成本试点，而不是因缺预算直接拒答。
5. 生成产物，展示 PDF、概念图、语音摘要会同步进入产物页，并记录为新的方案版本。
6. 回到工作区，展示方案迭代、v1/v2 对比、指标回填和收敛图。
7. 可选：用连接字符串接入 SQL Database / Blob Storage，预览外部数据，导入文件库并发送到分析。

**PPT 叙事重点：**

- 「不是聊天机器人」：它包含数据接入、检索、评分、审计、产物生成、版本迭代。
- 「不自欺」：证据强度控制结论档位；不够支撑的内容会变成缺口或验证任务。
- 「企业可扩展」：本地上传、SQL / Blob 连接、Azure AI Search、Foundry 联网搜索、Blob 持久化、Container Apps 部署。
- 「可持续使用」：团队能持续导入试点数据、对比版本，逐步收敛到可决策的重点方案。

## 多 Agent 设计

六个专家由一个调度器协同，而不是一条写死的流水线：

| Agent | 职责 |
|---|---|
| **Coordinator 协调** | 判断意图、选哪些 Agent 上场、决定输出形态（对话 / 报告 / 完整方案）。 |
| **检索** | 通过 Azure AI Search（语义向量 + 关键词混合）从工作区捞出相关证据。 |
| **分析** | 按五维标准给可行性打分；每个分数都必须挂证据。 |
| **市场** | 通过 MCP 与 Foundry 原生联网搜索调研真实竞品；严格标注为「市场推断」。 |
| **审计** | 审核分析结果；发现实质质量缺口就把工作**回流**复修。 |
| **产物** | 生成 PDF 提案、概念图、语音摘要。 |

### 审计 ⇄ 复修 回流（Microsoft Agent Framework）

这是「真会判断、而非一条直线」的核心。审计员的结论驱动一张用 [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)（`agent-framework-core`）搭的**条件 Agent 执行图**：

- `verdict = 需复修` → 携审计反馈**回流**给分析 Agent，重新分析。
- `verdict = 通过` → 收敛、定稿。
- 复修轮数**有上限**（可配置），所以循环一定终止。

路由由审计员在运行时的结论经条件边决定 —— 是**代码控制的判断**，不是写死的 `if`。我们只借用 MAF 做编排拓扑，分析/审计引擎仍是自研的，因此零行为漂移。

### 当前实现边界

- 一等 MAF 团队运行时支持四种有界协作模式：`direct`、`concurrent_research`、`specialist_handoff` 和 `bounded_review`。每次运行会记录所选 Agent 数量上限、复修上限、墙钟耗时、参与者工作量和提供商实际返回的 token；拿不到的用量保持 `null`。原有审计/复修图仍可通过 `audit` 模式使用。
- 治理页面把三类事实分开：实际模型用量/成本、基于时间价值假设的估算，以及带来源的真实业务结果。结果只有经过单独的复核动作才能从 `measured` 进入 `verified`。Application Insights / OpenTelemetry 连接状态与 Foundry 原生 ROI 分开显示；后者当前仍未接入。
- 分析运行会形成规范的实验账本。方案草稿和产物快照附着到源分析，不冒充新实验；每一版均包含证据、指标来源、证据变化和决策变化。合成、仅市场推断或不可追溯的输入不能抬高有效结论档位。
- 产物生成使用可持久化的后台任务，支持逐项成功、刷新恢复、幂等请求和原子任务领取。PDF 文件名携带源方案版本（`V1`、`V2` 等）。
- 全局任务中心读取分析、产物、导入和连接器流程的服务端持久化任务记录，提供真实的排队、运行和终态，支持刷新后恢复、取消，以及仅在服务端存在调度器时提供重试。
- 市场相关性是严格的来源证据门禁：直接且与当前机会相关的来源才会被接受；无关、相邻、仅面向消费者或没有来源证据的说法会被拒绝，或降级为明确的不可用合同。
- MAF 对证据包以及 Agent、复修和可选市场调用使用有界预算。确定性评估仍是 fixture 证据，不代表生产质量或 ROI。
- 连接器记录只持久化脱敏元数据和不透明密钥引用。配置 `DF_KEY_VAULT_URL` 时，部署身份必须具有 Key Vault 密钥 `get`、`set`、`delete` 权限；失败不会回退到会话存储。未配置时，加密的进程内密钥为 `session_only`，会在 TTL 到期或进程重启后失效，必须使用新凭据重新连接。
- 设置 `DF_WORKSPACE_RBAC_ENFORCED=1` 后会执行 `owner`、`admin`、`editor`、`viewer` 工作区权限。浏览器通过受 Easy Auth 保护的 Web 同源代理请求 API；后端只接受由 `DF_WEB_PROXY_SECRET` 签名转发的登录身份。待接受邀请仅在匹配的 Easy Auth 对象 ID 与租户登录后激活。
- 当前没有启用开放式 Magentic 编排，也没有启用 Foundry Hosted Agents。

### MAF 运行模式、灰度与评估

- `DF_MAF_RUNTIME=off` 使用旧版编排器；`audit` 仅启用现有的有界审计/复修图；`full` 允许请求进入一等 MAF 团队运行时。
- 未设置 `DF_MAF_RUNTIME` 时，`DF_USE_MAF=1` 为兼容旧配置映射到 `audit`。
- `DF_MAF_AUTH_MODE=auto` 会在 `AZURE_OPENAI_API_KEY` 与 `OPENAI_ENDPOINT` 可用时使用 Azure OpenAI API Key，否则使用 Foundry 项目的托管身份。可设置为 `api_key` 或 `managed_identity` 强制指定认证模式。Azure Responses 的 Key 路由默认使用 `preview` API 版本，也可通过 `AZURE_OPENAI_API_VERSION` 覆盖。
- `DF_MAF_TRAFFIC_PERCENT` 取值为 `0..100`。灰度选择基于工作区 ID 与会话 ID 的稳定哈希，不使用业务名、数据集名、行业名或演示名称做路由触发器。在完整测试、确定性评估、后端镜像导入/启动 smoke、广泛评审以及单独批准的生产 canary 通过前，保持为 `0`。
- MAF 在首个实时事件发出前构造或运行失败时，会记录回退证据并执行旧版路径 exactly once（恰好一次）；一旦 MAF 输出开始，终态由 MAF 持有，不再启动旧版执行。可选市场分支失败只降级为工作区证据，不会触发整条链路重放。
- FULL 模式中，凡是需要工作区数据的请求都先走后端权威检索，并使用 typed corpus/evidence/rubric contract，证据必须有效、非空且能回溯到真实检索命中。可行性和审计输出使用 Pydantic 校验，合同纠正最多重试一次；现有 rubric、证据核验以及经过 typed contract 复核的审计前后 guardrail 继续作为权威边界。`full_package` 仍会运行 producer，保留 PDF、图片、计划和音频交付。
- MAF 事件和参与者 span 按真实执行实时发出。Token 只读取运行时/提供商响应字段，未知用量保持 `null`，工作流墙钟时长与参与者工作量总和分开记录，模型输出中的遥测字典不被采信。
- 稳定依赖版本固定为 `agent-framework-core==1.11.0`、`agent-framework-foundry==1.10.1` 和 `agent-framework-orchestrations==1.0.0`。
- 无连接器评估命令为 `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`。报告明确声明 `measurement_scope='deterministic_harness'` 和 `production_quality_claim=false`。groundedness 与 unsupported-claim rate 只是 fixture/reference-propagation contract checks，not production answer quality（不是生产答案质量评价）。其他指标仅在确定性夹具观测存在时给值；没有用量遥测的 tokens 等缺失值保持 `null`/`unknown`。
- 集成 P2-A 门禁命令为 `python eval/run_p2_a_acceptance.py --output generated-outputs/p2-a-acceptance.json`。机器可读报告包含 baseline、市场相关性、MAF、任务和连接器门禁的证据类别、样本数、是否允许生产声明、失败原因和输入谱系；fixture 检查中的延迟和 token 均保持 `unmeasured`。
- P2-B Azure 治理门禁命令为 `python eval/run_p2_b_acceptance.py --output generated-outputs/p2-b-acceptance.json`。它覆盖追踪配置、追踪投递、本地 ROI 状态、Foundry ROI 状态、分摊谱系、邀请声明匹配、审计脱敏和授权边界。默认离线报告只验证本地契约：Azure Monitor 投递保持 `unmeasured`，Foundry 原生 ROI 保持 `not_configured`，`production_claim_allowed` 始终为 `false`。调用方提供的来源名、时间、ID、结果摘要或自称证明都不可信，不能抬升任何门禁。生产声明必须由独立的可信提供方验证器给出不可变绑定，覆盖预期的工作区、运行、关联 ID、构建、修订版本和观测时间；该确定性命令不会伪造或授予这类声明。

完整发布与演进设计见 [`docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md`](docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md)。

## 核心能力

- **发现非显而易见的机会** —— 没人告诉它「去做选址」，它是从你数据里的证据自己推断出机会的。
- **自我审计** —— 分析结果在给你看之前先经审核与复修；整个判断过程在界面上逐步可见。
- **有据可查、不自欺** —— 每个结论都挂着可点开的引用；市场推断与工作区事实严格分离；缺证据就如实降级，绝不伪造「可行」。
- **方案迭代 → 重点方案** —— 把真实试点指标（转化率、客单价、价格）作为「实测」值回填，生成下一版，并对比 v1 / v2，逐版逼近一个公司重点方案。它是企业能持续用的工具，不是一次性 demo。
- **可恢复的产物生成** —— PDF 提案、产品概念图和口播版执行摘要通过后台任务生成；某一类失败时，已经成功的文件仍会保留。
- **受治理的多人协作** —— Entra 用户会在审计和用量视图中归因；工作区角色可执行读、写和管理边界；业务结果保留来源与复核链路。
- **全程可追溯** —— 每次运行都带审计/复修标签落盘；任意历史运行都能恢复回放，引用悬停依然可查。

## Azure 集成度（Pro Code）

全链路 11 项 Azure 原生服务 —— 全部代码可控、可观测、可扩展：

| 层 | 服务 | 用途 |
|---|---|---|
| 智能 | Azure OpenAI (gpt-5.1) | 多 Agent 推理、结构化输出 |
| 智能 | Azure AI Foundry | Agent Service 底座 + 原生联网搜索 |
| 智能 | Azure AI Search | 混合检索（RAG）、证据挂钩 |
| 数据 | Azure Blob Storage | 产物 / 运行 / 会话持久化 |
| 数据 | Azure Cache for Redis | 可行性结果缓存 |
| 安全 | Azure AI Content Safety | Prompt Shield（防注入） |
| 安全 | Microsoft Entra ID | 登录鉴权（Easy Auth） |
| 体验 | Azure AI Speech | 语音摘要生成 |
| 运维 | Azure Container Apps | 容器化滚动部署 |
| 运维 | Azure Container Registry | 镜像构建与托管 |
| 运维 | Application Insights | OpenTelemetry 分布式追踪 |

协议 / 框架：**Microsoft Agent Framework · MCP · A2A（路线图）**。

## 技术栈

- **后端：** Python · FastAPI · SSE 流式 · Microsoft Agent Framework（`agent-framework-core==1.11.0`、`agent-framework-foundry==1.10.1`、`agent-framework-orchestrations==1.0.0`）· Azure SDK
- **前端：** React · Vite · 实时流式交互
- **基础设施：** Terraform（模块化）· Azure Container Apps · ACR
- **可观测：** OpenTelemetry → Application Insights

## 仓库结构

```
backend/      FastAPI 应用、编排器、MAF 审计回流、Azure 客户端、可行性引擎
web/          React + Vite 前端（流式 UI、Agent 执行视图、方案迭代）
agents/       Agent 提示词（分析、审计……）
mcp-market/   MCP 市场查询工具
ingest/       文档摄取 / 索引
infra/        Terraform 模块与 dev 环境
eval/         路由 / 质量评估脚本
docs/         设计决策与评估证据
workspaces/   合成演示语料（虚构公司，可安全公开）
```

## 运行

### 后端（本地冒烟）

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -r mcp-market/requirements.txt
cp backend/.env.example backend/.env   # 填入你自己的值
.venv/bin/python -m pytest
```

未配置 Azure 资源时后端有 mock 安全回退，可部分离线运行。

### 前端

```bash
cd web
npm install
npm run dev
```

### 基础设施

```bash
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # 填入你自己的值
terraform init && terraform apply
```

## 配置

- `backend/.env.example` —— 后端全部环境变量（Azure 端点、密钥、特性开关），复制为 `.env`。
- `infra/envs/dev/terraform.tfvars.example` —— 部署标识，复制为 `terraform.tfvars`。
- `.env`、`*.tfvars`、`*.tfstate*` 已被 git 忽略。**绝不要提交真实密钥或订阅 ID。**

关键特性开关：`DF_MAF_RUNTIME`（`off`、`audit` 或 `full`）、`DF_MAF_AUTH_MODE`（`auto`、`api_key` 或 `managed_identity`）、`DF_MAF_TRAFFIC_PERCENT`（稳定灰度百分比）、`DF_USE_MAF`（兼容旧配置并映射到 `audit`）、`DF_MAF_MAX_REVISIONS`（复修上限）、`DF_AUDIT_STRICT_GATE`（旧版保守审计门）、`DF_WEB_MARKET`（Foundry 联网搜索）、`DF_WORKSPACE_RBAC_ENFORCED`（工作区角色强制执行）、`DF_WEB_PROXY_SECRET`（Web 到后端身份代理的共享密钥）和 `DF_ARTIFACT_JOB_STALE_SECONDS`（中断任务恢复窗口）。

## 负责任 AI

- 输入经 Azure AI Content Safety 做 **Prompt Shield 防注入**。
- **来源分级红线：** 外部市场信息标注为「市场推断」，绝不升级为「工作区已证实事实」。
- **不自欺：** 证据不足时如实降级结论；审计员只提证据撑得起的缺口，绝不编造。
- 演示语料均为**合成数据**（虚构公司），并明确标注。

---

*DataForge 不是一个会聊天的 demo，而是一套用 Pro Code 搭的、会发现机会、会自我审计、能持续沉淀企业价值的多 Agent 系统。*

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

## 核心能力

- **发现非显而易见的机会** —— 没人告诉它「去做选址」，它是从你数据里的证据自己推断出机会的。
- **自我审计** —— 分析结果在给你看之前先经审核与复修；整个判断过程在界面上逐步可见。
- **有据可查、不自欺** —— 每个结论都挂着可点开的引用；市场推断与工作区事实严格分离；缺证据就如实降级，绝不伪造「可行」。
- **方案迭代 → 重点方案** —— 把真实试点指标（转化率、客单价、价格）作为「实测」值回填，生成下一版，并对比 v1 / v2，逐版逼近一个公司重点方案。它是企业能持续用的工具，不是一次性 demo。
- **一键产出交付物** —— 可下载的 PDF 提案、产品概念图、口播版执行摘要。
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

- **后端：** Python · FastAPI · SSE 流式 · Microsoft Agent Framework（`agent-framework-core`）· Azure SDK
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

关键特性开关：`DF_USE_MAF`（启用审计⇄复修 Agent 回流）、`DF_MAF_MAX_REVISIONS`（复修上限）、`DF_AUDIT_STRICT_GATE`（旧版保守审计门）、`DF_WEB_MARKET`（Foundry 联网搜索）。

## 负责任 AI

- 输入经 Azure AI Content Safety 做 **Prompt Shield 防注入**。
- **来源分级红线：** 外部市场信息标注为「市场推断」，绝不升级为「工作区已证实事实」。
- **不自欺：** 证据不足时如实降级结论；审计员只提证据撑得起的缺口，绝不编造。
- 演示语料均为**合成数据**（虚构公司），并明确标注。

---

*DataForge 不是一个会聊天的 demo，而是一套用 Pro Code 搭的、会发现机会、会自我审计、能持续沉淀企业价值的多 Agent 系统。*

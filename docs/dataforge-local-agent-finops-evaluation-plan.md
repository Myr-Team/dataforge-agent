# DataForge 本地 Agent FinOps、ROI 回归与检索计划

状态：仅限本地实现与验证
计划基线：`5634cf085e96893f4912f8f14033b2c8621321a6`
适用范围：DataForge 本地运行、离线评估、本地检索和本地 observation 合同。
不适用范围：APIM、Azure 部署、Terraform、SQL migration、生产流量、Docker、Web、远程 Graph 服务和新的连接器密钥。

## 1. 目标

本计划先建立可验证、可回归、可移植的本地 Agent 运营能力，并解决三类问题。

1. 将 provider、route、token、reasoning、provider cache、成本和执行状态投影为可信的本地运行事实。
2. 在模型、Prompt、上下文、路由或检索策略变更后，使用固定数据集量化质量、成本和性能变化。
3. 在不改变既有 `rag.search()` 默认合同的前提下，为本地 keyword、graph 和 hybrid retrieval 建立可插拔接口。

本阶段不是云部署工作，也不建立通用 IDE Agent Gateway。后续部署工作只能在审查本计划、代码 diff 与验证报告后，将稳定字段映射到 APIM、SQL 或其他生产系统。

## 2. 核心原则

### 2.1 三类事实必须分离

| 层次 | 负责内容 | 禁止内容 |
| --- | --- | --- |
| `LocalModelObservation` | provider、route、usage、cache、latency、status、cost evidence | 推测业务收益，或把未知值填成零 |
| `EvaluationReport` | 离线回归、检索排序、groundedness、单位经济性比较 | 写入 verified outcome，或声称生产质量 |
| Outcome 和 ROI ledger | observed outcome、独立验证和 verified ROI | 接受 fixture、demo seed 或预测结果作为 verified ROI |

### 2.2 APIM 不是本地实现依赖

本地能力从 provider response、run event 和本地 corpus 读取事实。APIM、Azure Monitor、SQL 与 Redis 不能成为第一阶段运行前提。

### 2.3 回归损失不等于 ROI

MAE、MSE、RMSE、Huber、BCE、Brier、Recall、MRR 和 nDCG 描述模型或检索质量，不会自动转化为财务收益。只有既有 outcome ledger 中具备独立验证、完整成本和一致币种的业务结果才能显示 verified ROI。

### 2.4 默认行为必须兼容

- 默认检索继续使用既有 `rag.search()` 行为。
- 默认不启动本地评估 API，也不访问网络。
- 新字段未知时使用 `null`、`unavailable` 或 `not_applicable`，不得回填为零或成功。
- 本地实验结果固定 `production_quality_claim=false`。

## 3. 本地数据合同

### 3.1 LocalModelObservation

```json
{
  "schema_version": "dataforge.local-model-observation.v1",
  "run_ref": "opaque-reference",
  "request_ref": "opaque-reference",
  "workspace_ref": "opaque-reference",
  "agent": "df-feasibility-analyst",
  "capability": "feasibility_analysis",
  "provider_type": "azure_foundry",
  "provider_id": "configured-provider-reference",
  "model_id": "configured-model-reference",
  "route": "primary",
  "route_evidence": "observed",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 20,
    "reasoning_tokens": 7,
    "cached_input_tokens": 40,
    "total_tokens": 120
  },
  "provider_cache": {
    "state": "partial_hit",
    "hit_tokens": 40,
    "miss_tokens": 60
  },
  "latency_ms": 850,
  "status": "completed",
  "cost": {
    "amount": 0.0123,
    "currency": "USD",
    "status": "estimated",
    "price_card_revision": "revision-reference"
  }
}
```

约束如下。

- 不保存 prompt、response、provider 原始 body、密钥、邮箱、原始 tenant 或 actor 标识。
- `route_evidence` 只能是 `observed`、`selected`、`inferred` 或 `unavailable`。
- reasoning 是 output detail，不能再次加入 total。
- provider cache 与 DataForge result cache 必须分开记录。
- Foundry 只报告 `cached_tokens` 时，仅记录已知 hit token 和 `partial` evidence，不推断 miss token 或 hit rate。
- token 仅接受有限、非负、整型数值。小数、NaN、Infinity、布尔值、负数和字符串均为 unavailable。
- 真实 run 使用安全 opaque reference 或受限 configured-provider reference。fixture 可使用可读但脱敏的 reference。

### 3.2 EvaluationCase 与 EvaluationReport

`EvaluationCase` 使用显式类型，包括 `continuous_regression`、`binary_probability`、`retrieval_ranking`、`grounded_generation` 与 `unit_economics`。

`EvaluationReport` 必须包含 dataset digest、baseline、candidate、metrics、gates、sample count、invalid count 与 not-applicable count。baseline 与 candidate 必须使用同一 dataset digest。空样本不能通过，单位、币种、时间窗口和 evidence status 不可比较时必须 fail closed。

所有 `measurement_scope=sanitized_fixture` 的数据集都禁止包含 verified outcome evidence 或 verified benefit evidence。即使外部 cases 文件刷新 digest，也不得在 runner 中生成 `net_verified_value`、`verified_roi` 或 `verified_status=verified`。verified ROI 数学仅保留在独立、非 runner 的纯函数合同测试中。

### 3.3 RetrievalRequest 与 RetrievalHit

```json
{
  "workspace_id": "workspace-reference",
  "query": "sanitized query",
  "top_k": 5,
  "allowed_corpus_refs": ["corpus-a"],
  "mode": "local_keyword"
}
```

本地 adapter 必须在 lexical、graph expansion、fusion 之前应用 corpus scope。`permission_filtered=true` 仅表示实际应用过授权决定。若本地 backend 无法从可信 workspace metadata 获得 `authorized_corpus_refs`，必须 fail closed，不得把空 scope 解释为允许所有文档。默认 legacy 路径保持既有行为。

## 4. 指标与证据规则

### 4.1 连续值回归

第一版使用 MAE、MSE、RMSE 与 Huber。拒绝 NaN、Infinity 与非数值输入，并报告有效、缺失和非法样本数。非法样本不能被静默丢弃后仍返回 pass。

### 4.2 二分类概率

第一版使用 Binary Cross-Entropy 和 Brier score。概率必须位于零到一之间，label 只能是零或一。不得把文本 verdict 或 status 隐式转换为概率。

### 4.3 检索排序

第一版使用 Recall at K、MRR 与 nDCG at K。qrels 使用稳定 corpus 或 chunk reference。relevant set 为空时标记 `not_applicable`。重复 hit 先去重再计分，越权 hit 是硬失败，不参与均值稀释。

### 4.4 Groundedness

第一版只做确定性 reference contract：claim 是否带 evidence reference，reference 是否在允许证据内，以及 unsupported claim rate。该指标不等同于人工或 LLM judge 的语义正确性。

### 4.5 ROI 与单位经济性

允许的本地比较包括：

```text
Cost per Successful Request = observed comparable cost / successful requests
Cost per Verified Outcome = complete comparable cost / verified outcomes
Net Verified Value = verified monetized benefit - complete cost
Verified ROI = Net Verified Value / complete cost
```

成本不完整、币种不一致或时间窗口不一致时不得计算 verified ROI。estimated 或 scenario outcome 永远不得在 evaluation runner 中升级为 verified。模型质量改善不能直接货币化。

## 5. 实施批次

### L0：本地 observation 事实闭环

目标是确保回归输入可信。

- `backend/foundry_client.py` 提取 input、output、total、reasoning detail 与 provider cache token。
- `backend/run_store.py` 将安全的 provider、model、route、route evidence、usage、cache 与成本投影到 `run.models[]`。
- `backend/local_agent_observation.py` 从持久化 model record 构建纯函数 observation。
- `backend/finops/normalization.py` 与 `backend/provider_usage.py` 使用相同的 token 完整性规则，兼容旧 run 中缺失字段。
- 测试覆盖 Foundry、DeepSeek、reasoning、partial cache、route evidence、provider reference、未知 usage 与敏感字段清洗。

### L1：本地回归与 ROI 证据比较

目标是提供无需网络、可重复的 baseline 与 candidate gate。

- `backend/evaluation_metrics.py` 提供纯指标函数。
- `eval/run_agent_finops_roi_regression.py` 只处理 sanitized fixture，并验证 schema、digest、比较边界与证据状态。
- `eval/agent_finops_roi_cases.json` 只含脱敏或合成 case，不含 verified ROI evidence。
- runner 不调用 `record_outcome_event()` 或 `verify_outcome_event()`。

### L2：可插拔本地检索

目标是在默认行为不变的前提下，提供 graph-assisted local retrieval。

- `LegacyRetrievalAdapter` 包装既有检索。
- `LocalKeywordAdapter` 使用本地文档。
- `LocalGraphAdapter` 只读取 workspace 内版本化 JSON 或 JSONL graph。
- `HybridRetrievalAdapter` 对已授权 lexical 与 graph candidates 进行 RRF、去重和稳定排序。
- local adapter 不访问网络，graph 路径必须位于 workspace root 内。

### L3：部署交接，不属于本地实现

仅在 L0 至 L2 验收通过后，部署工作才可决定 APIM 映射、SQL schema、Azure Monitor、远程 Graph 服务、service identity、Key Vault、canary、回填和回滚。L3 不能反向成为 L0 至 L2 的前置条件。

## 6. 验收与验证

L0 验收：provider fixture 到 run model 再到 local observation 不丢失安全的 provider、reasoning、cache 与 route evidence。未知 usage 保持 unavailable。provider cache 与 result cache 分离。敏感字段不被投影。

L1 验收：固定输入重复执行得到相同 metrics、gates 与 digest。dataset digest 不一致、非法样本、不可比较单位或币种均 fail closed。sanitized fixture 永远不显示 verified ROI。

L2 验收：默认 legacy 检索合同不变。本地 keyword、graph、hybrid 与 fallback 都在 corpus 授权范围内执行。RRF 去重、稳定排序和 trace 可复现。越权 hit 使 gate 失败。

建议验证命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q -p no:cacheprovider tests/test_finops_normalization.py tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_evaluation_metrics.py tests/test_agent_finops_roi_regression.py tests/test_local_agent_observation.py tests/test_retrieval_adapters.py
python eval/run_agent_finops_roi_regression.py --cases eval/agent_finops_roi_cases.json
git diff --check
```

完成后还应运行全量 Python 与既有 Node 测试，以确认默认行为未回归。本阶段不修改 UI，因此不以 Playwright 作为 L0 至 L2 的最低验收前提。

## 7. 安全、隐私与禁止范围

- 不记录 secret、credential、Authorization header、system prompt、完整用户 prompt、完整 model response、原始 tenant、actor、email 或 provider 原始错误正文。
- provider error 仅使用 allowlisted category。
- graph node 与 edge 只引用当前 workspace 的已授权 evidence。
- evaluation report 只保留安全 reference 和聚合 metric。
- 本地 eval 不修改 outcome verification ledger。
- 不修改 `infra/apim`、`infra/envs`、`infra/modules`、FinOps SQL DDL 或 migration、`web`、Dockerfile、nginx、部署脚本、生产 feature flag、流量或云资源。

## 8. 部署交接清单

后续部署审查需明确：

- 哪些 LocalModelObservation 字段可进入生产事实表，以及 provider 和 model 标识是否需要 HMAC 或映射表。
- provider usage 与 APIM metric 的字段权威来源、重复请求、retry 与 streaming interruption 的幂等键。
- 高基数明细的日志、事实表、retention、partition、回填与删除策略。
- 远程检索的 tenant 和 workspace ACL 执行位置，以及 Graph 或 Search credential 的 Key Vault 管理。
- canary 期间质量、成本、P95 与错误率的比较方法，以及关闭 remote adapter 时回退 legacy 的条件。

# FinOps Agent 与 ROI Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在成本、ROI 和风险页面嵌入两个受控分析 Agent，用结构化且可复核的 evidence refs 解释成本变化、异常驱动、可验证 ROI 和证据缺口，并只允许创建类型化 draft 治理动作。

**Architecture:** 新增 `FinOpsInsight` 领域模型和 SQL 仓库；Agent 输入由受控查询服务生成，永远不获得任意 SQL、脚本、APIM XML、资源 ID 或跨 workspace 数据。触发器使用去重 fingerprint，把“异常新建/重要变化”和“结果验证”变成可重放的分析任务；Portal 60 秒刷新只读取 insight，不触发模型。Agent 输出通过 Pydantic schema 验证后保存，失败时保留上次成功 insight 并标记 stale。

**Tech Stack:** FastAPI、Pydantic v2、Azure SQL additive schema、现有 Foundry `run_agent` 封装、pytest、React、Node test runner、Playwright。

## Global Constraints

- Agent 不能 submit、approve、execute、verify 或 rollback。
- Agent 只能创建现有 allowlist 支持的类型化 `draft` 动作。
- ROI 只能使用 `verified` outcome events。
- 每个 finding 必须至少有一个属于当前 tenant/workspace/window 的 `evidence_ref`。
- 证据不足返回 `insufficient_data`，不能生成推测数字或建议。
- Agent 失败不能阻塞 Portal 聚合读取。
- 60 秒 Portal 刷新不得隐式调用 Agent。
- `DF_FINOPS_ACTIONS_ENABLED` 保持默认关闭。

---

## Task 1: 建立 FinOpsInsight 模型与持久化

**Files:**

- Create: `backend/finops/insights.py`
- Create: `backend/finops/insight_repository.py`
- Modify: `backend/sql/finops_schema.sql`
- Create: `tests/test_finops_insights.py`
- Modify: `tests/test_finops_sql.py`

- [ ] **Step 1: 写模型和仓库失败测试**

最小合法 insight：

```python
insight = FinOpsInsight.model_validate({
    "insight_id": "ins_aaaaaaaaaaaa",
    "agent_kind": "finops",
    "tenant_ref": "tenant-a",
    "workspace_ids": ["ws-a"],
    "window": {"from": "2026-07-23T00:00:00Z", "to": "2026-07-24T00:00:00Z"},
    "trigger_type": "anomaly_changed",
    "trigger_ref": "anom-budget-1",
    "title": "预算使用上升",
    "summary": "成本上升主要来自主分析流程。",
    "findings": [{
        "kind": "cost_driver",
        "statement": "主分析流程贡献本时段 62% 估算成本。",
        "evidence_refs": ["req_aaaaaaaaaaaa"],
    }],
    "evidence_refs": ["req_aaaaaaaaaaaa"],
    "evidence_state": "estimated",
    "confidence": 0.82,
    "source_revisions": {"price_card": "price-2026-07"},
    "generated_at": "2026-07-24T02:00:00Z",
    "expires_at": "2026-07-24T08:00:00Z",
    "status": "ready",
})
```

拒绝 finding 无 evidence refs、空 workspace scope、跨 tenant update 和非法 status。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_insights.py tests/test_finops_sql.py -k insight -q
```

Expected: FAIL，模型和仓库不存在。

- [ ] **Step 3: 实现领域模型**

状态只允许：

```python
InsightStatus = Literal["ready", "insufficient_data", "failed", "stale"]
AgentKind = Literal["finops", "roi"]
```

`FinOpsInsight` 必须校验：

- `ready` 至少一个 finding 和一个 evidence ref。
- `insufficient_data` 必须有 `evidence_gaps`。
- evidence ref 长度和数量有上限。
- confidence 在 0 到 1。

- [ ] **Step 4: 实现内存和 SQL 仓库**

SQL 表至少包含：

```sql
IF OBJECT_ID(N'finops.insight', N'U') IS NULL
BEGIN
    CREATE TABLE finops.insight (
        insight_id nvarchar(64) NOT NULL PRIMARY KEY,
        tenant_ref nvarchar(128) NOT NULL,
        agent_kind nvarchar(16) NOT NULL,
        workspace_scope_hash char(64) NOT NULL,
        trigger_type nvarchar(64) NOT NULL,
        trigger_ref nvarchar(160) NULL,
        trigger_fingerprint char(64) NOT NULL,
        status nvarchar(32) NOT NULL,
        generated_at datetime2(3) NOT NULL,
        expires_at datetime2(3) NOT NULL,
        insight_payload nvarchar(max) NOT NULL,
        CONSTRAINT UQ_finops_insight_trigger
            UNIQUE (tenant_ref, agent_kind, trigger_fingerprint)
    );
END;
```

列表方法必须接收 tenant_ref 和 authorized workspace IDs，并使用游标分页。

- [ ] **Step 5: 运行模型和 SQL 测试**

Run:

```powershell
python -m pytest tests/test_finops_insights.py tests/test_finops_sql.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/finops/insights.py backend/finops/insight_repository.py backend/sql/finops_schema.sql tests/test_finops_insights.py tests/test_finops_sql.py
git commit -m "feat(finops): persist scoped analysis insights"
```

## Task 2: 构建受控 FinOps 与 ROI 分析输入

**Files:**

- Create: `backend/finops/agent_inputs.py`
- Create: `tests/test_finops_agent_inputs.py`
- Modify: `backend/roi_service.py`

- [ ] **Step 1: 写输入边界失败测试**

FinOps 输入只包含：

- 当前 scope/window 的 overview、trends、breakdowns。
- 当前异常摘要。
- 价目表 revision。
- 有界 evidence refs。

ROI 输入只包含：

- 当前 scope/window 的估算成本。
- verified outcome events。
- 由 `build_roi_snapshot()` 产生的可复核 ROI。
- evidence gaps。

测试未验证 outcome 不进入输入，prompt/answer/raw identity/internal error 不进入 JSON。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_agent_inputs.py -q
```

Expected: FAIL，输入构建器不存在。

- [ ] **Step 3: 实现输入构建器**

定义两个显式入口：

- `build_finops_agent_input(query, query_service, anomalies, price_card_revision)` 返回当前 scope 的有界成本分析 JSON。
- `build_roi_agent_input(workspace_id, window, roi_snapshot, verified_outcomes)` 返回只包含已验证结果的有界 ROI 分析 JSON。

每个列表设置明确上限；没有足够样本时返回 `{"status": "insufficient_data", "evidence_gaps": ["已验证结果事件不足"]}`，不调用模型。

- [ ] **Step 4: 运行输入测试**

Run:

```powershell
python -m pytest tests/test_finops_agent_inputs.py tests/test_roi_service.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/finops/agent_inputs.py backend/roi_service.py tests/test_finops_agent_inputs.py
git commit -m "feat(finops): build bounded agent analysis inputs"
```

## Task 3: 实现结构化 Agent runner 与输出校验

**Files:**

- Create: `backend/finops/analysis_agents.py`
- Create: `tests/test_finops_analysis_agents.py`
- Modify: `backend/foundry_client.py`

- [ ] **Step 1: 写 runner 失败测试**

覆盖：

- FinOps Agent 输出成本驱动和 typed draft 建议。
- ROI Agent 输出只引用 verified outcome evidence。
- 未知 evidence ref 导致输出拒绝并保存 failed/stale。
- 模型返回非结构化文本不会直接显示给客户。
- insufficient input 不调用 `run_agent`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_analysis_agents.py -q
```

Expected: FAIL，runner 不存在。

- [ ] **Step 3: 定义结构化输出 schema**

```python
class AgentFinding(BaseModel):
    kind: Literal["cost_driver", "risk", "optimization", "roi", "evidence_gap"]
    statement: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

class AgentDraftSuggestion(BaseModel):
    action_type: Literal["apim_token_limit", "model_route", "cache_policy", "price_card_activation"]
    reason: str = Field(min_length=1, max_length=600)
    payload: dict[str, Any]
```

payload 再交给现有 typed action model 验证，拒绝额外字段、XML、脚本和资源 ID。

- [ ] **Step 4: 封装现有 `run_agent`**

`analysis_agents.py` 注入 callable，测试不调用外部服务。生产实现使用现有 `foundry_client.run_agent`，系统指令明确：

- 只根据给定 JSON 证据分析。
- 不补全缺失数字。
- 每个 finding 必须引用 evidence refs。
- 只返回目标 JSON schema。

解析后再次校验所有 evidence refs 都属于输入 allowlist。

- [ ] **Step 5: 运行 runner 测试**

Run:

```powershell
python -m pytest tests/test_finops_analysis_agents.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/finops/analysis_agents.py backend/foundry_client.py tests/test_finops_analysis_agents.py
git commit -m "feat(finops): add evidence-bound FinOps and ROI agents"
```

## Task 4: 实现去重触发、读取和手动分析 API

**Files:**

- Create: `backend/finops/insight_service.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/outcome_store.py`
- Modify: `backend/finops/anomaly_store.py`
- Create: `tests/test_finops_insight_triggers.py`
- Modify: `tests/test_finops_api.py`

- [ ] **Step 1: 写触发和权限失败测试**

覆盖：

- 新异常触发一次 FinOps 分析。
- 相同 anomaly revision 的重复 reconcile 不重复调用。
- severity/status 重要变化产生新 fingerprint。
- outcome 只有 verify 成功后触发 ROI 分析。
- Portal GET/60 秒刷新不调用 Agent。
- `POST /insights/analyze` 需要对应读权限。
- Agent 创建动作时状态只能是 `draft`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_insight_triggers.py tests/test_finops_api.py -k insight -q
```

Expected: FAIL，服务和路由不存在。

- [ ] **Step 3: 实现触发 fingerprint 和保留旧 insight**

fingerprint 使用：

```python
sha256(
    canonical_json({
        "tenant_ref": tenant_ref,
        "workspace_ids": sorted(workspace_ids),
        "agent_kind": agent_kind,
        "trigger_type": trigger_type,
        "trigger_ref": trigger_ref,
        "source_revision": source_revision,
    }).encode("utf-8")
).hexdigest()
```

失败时：

- 保存本次 failed insight。
- 上次 ready insight 标记为 stale 但继续可读。
- 不阻塞 anomaly/outcome 原操作。

- [ ] **Step 4: 接入事件触发**

异常服务只在新建或重要状态变化后调用 trigger callback。`verify_outcome_event()` 成功持久化后调用 ROI trigger callback。callback 接口可在测试注入，无配置时安全 no-op。

- [ ] **Step 5: 增加 API**

```text
GET /api/finops/insights?agent_kind=finops|roi
POST /api/finops/insights/analyze
```

POST body 只接受：

```json
{
  "agent_kind": "finops",
  "workspace_id": "ws-a",
  "from": "2026-07-23T00:00:00Z",
  "to": "2026-07-24T00:00:00Z"
}
```

workspace 和 tenant 仍从服务端权限上下文收窄。响应不等待不受限模型调用；返回受控任务状态或已存在的 fingerprint 结果。

- [ ] **Step 6: 运行触发和 API 测试**

Run:

```powershell
python -m pytest tests/test_finops_insight_triggers.py tests/test_finops_api.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add backend/finops/insight_service.py backend/finops/router.py backend/outcome_store.py backend/finops/anomaly_store.py tests/test_finops_insight_triggers.py tests/test_finops_api.py
git commit -m "feat(finops): trigger scoped insight analysis"
```

## Task 5: 把 Agent 卡嵌入四页 Portal

**Files:**

- Modify: `web/src/api.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsViewModel.test.mjs`
- Modify: `web/src/styles.css`
- Create: `web/src/finopsInsightCards.test.mjs`

- [ ] **Step 1: 写卡片状态失败测试**

覆盖：

- ready：显示标题、摘要、证据状态、分析时间和 findings。
- insufficient_data：只显示证据缺口。
- stale：保留上次内容并标记“分析结果已过期”。
- failed 且无旧结果：显示“分析暂不可用”，不影响页面其他数据。
- 数据更新时间和 Agent 分析时间分开。
- 点击“重新分析”只调用 POST，不直接创建或执行生产动作。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
node --test web/src/finopsInsightCards.test.mjs web/src/finopsViewModel.test.mjs
```

Expected: FAIL，Insight API 和卡片不存在。

- [ ] **Step 3: 增加 API loader**

```javascript
export function loadFinOpsInsights(filters = {}, options = {}) {
  const query = buildFinOpsQuery(filters);
  return request(`/api/finops/insights${query ? `?${query}` : ""}`, options);
}

export function analyzeFinOpsInsight(payload) {
  return request("/api/finops/insights/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 4: 实现两个内嵌卡**

- 成本与预算：FinOps Agent。
- 效能与 ROI：ROI Agent。
- 风险与优化：两个 Agent 的紧凑状态卡。
- 不新增 Agent 中心或全局聊天。
- finding 点击进入证据抽屉。
- draft suggestion 只能显示“创建治理草案”，调用现有 create action API 后状态必须为 draft。

- [ ] **Step 5: 运行前端测试与构建**

Run:

```powershell
node --test web/src/*.test.mjs
Push-Location web
npm run build
Pop-Location
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add web/src/api.js web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/finopsViewModel.test.mjs web/src/styles.css web/src/finopsInsightCards.test.mjs
git commit -m "feat(finops): embed FinOps and ROI insight cards"
```

## Task 6: 完成 Agent 安全与真实验收

**Files:**

- Modify: `docs/finops-portal.md`
- Create: `web/tests/finops-insight-agents.spec.mjs`

- [ ] **Step 1: 写端到端场景**

验证：

- 一条真实异常触发 FinOps insight。
- 一个 verified outcome 触发 ROI insight。
- 相同 trigger revision 不重复分析。
- 每个 finding 可下钻到同 scope evidence。
- insufficient data 不出现推测数字。
- Portal 60 秒刷新不增加 Agent 调用次数。
- Agent 建议创建的动作状态为 draft。
- Agent 无法 approve/execute。

- [ ] **Step 2: 更新运行与故障说明**

记录：

- 两个 Agent 的只读输入边界。
- trigger fingerprint 去重语义。
- stale/failed/insufficient_data 的 UI 含义。
- Agent 与数据更新时间分离。
- 生产动作仍需异人审批且默认关闭。

- [ ] **Step 3: 运行阶段回归**

Run:

```powershell
python -m pytest tests/test_finops_insights.py tests/test_finops_agent_inputs.py tests/test_finops_analysis_agents.py tests/test_finops_insight_triggers.py tests/test_finops_api.py tests/test_roi_service.py -q
node --test web/src/*.test.mjs
Push-Location web
npx playwright test tests/finops-insight-agents.spec.mjs
npm run build
Pop-Location
```

Expected: 全部 PASS。

- [ ] **Step 4: 提交**

```powershell
git add docs/finops-portal.md web/tests/finops-insight-agents.spec.mjs
git commit -m "test(finops): verify agent insight safety loop"
```

## Completion Evidence

- FinOpsInsight 模型、SQL 幂等和 scope 收窄通过。
- ROI 输入只包含 verified outcome。
- 所有 finding 都有关联 evidence refs。
- 事件触发去重，60 秒刷新不调用 Agent。
- Agent 失败保留旧 insight，不阻塞 Portal。
- Agent 只能创建 draft，无法审批或执行。
- Node、Vite、pytest、Playwright 都有当前分支实测输出。

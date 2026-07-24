# FinOps 友好证据命名与请求详情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让成本、异常和 Agent 结论能够下钻到客户可理解的请求证据，同时把业务请求/最终回答、技术 ID 和 Foundry Trace 严格限制在授权详情接口。

**Architecture:** 新增稳定的 `FinOpsEvidenceAlias`，以 tenant、workspace、object kind、object ref 为唯一关联键，显示名由 workspace 快照、受控操作类型和发生时间确定生成。请求详情服务先做 tenant/workspace/capability 校验，再从 run store 读取 DataForge 应用层业务请求和最终用户可见回答；技术 ID 与 Trace 只在单条详情中按权限返回。请求列表和 bootstrap 不返回正文或技术引用。

**Tech Stack:** FastAPI、Pydantic v2、Azure SQL additive schema、pytest、React、Node test runner、Playwright。

## Global Constraints

- 不采集或展示 system prompt、Provider 原始请求/响应、密钥、原始身份和内部错误正文。
- 不把 `correlation_ref` 或内部 HMAC 关联键返回给客户端。
- Foundry/Azure Monitor 链接只由服务端受信配置生成，客户端不能提交任意 URL。
- Finance 聚合能力不能读取业务正文、技术 ID 或 Trace。
- 已有请求没有 alias 时允许确定性惰性补齐，但不得猜测错误关联。
- SQL 迁移必须 additive、可重复执行，不删除现有列或表。

---

## Task 1: 建立稳定的证据别名模型

**Files:**

- Create: `backend/finops/evidence.py`
- Create: `backend/finops/evidence_repository.py`
- Modify: `backend/sql/finops_schema.sql`
- Create: `tests/test_finops_evidence.py`
- Modify: `tests/test_finops_sql.py`

- [ ] **Step 1: 写命名和唯一性失败测试**

覆盖：

```python
alias = build_evidence_alias(
    tenant_ref="tenant-a",
    workspace_id="ws-a",
    workspace_name="Commerce",
    object_kind="request",
    object_ref="req_aaaaaaaaaaaa",
    operation_code="analysis_run",
    occurred_at=datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
)
assert alias.display_name == "Commerce · 分析运行 · 7月24日 10:42"
```

另测：

- 未知 workspace 回退“工作区”。
- 未知 operation 回退“操作记录”。
- 同 object ref 重复 upsert 返回同一 alias。
- workspace 后续改名不改变已保存的 `workspace_name_snapshot` 和底层 object ref。
- 不同 tenant 的相同 object ref 互不覆盖。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_evidence.py tests/test_finops_sql.py -k evidence_alias -q
```

Expected: FAIL，模型、仓库和表不存在。

- [ ] **Step 3: 实现受控命名**

`backend/finops/evidence.py` 定义：

```python
OPERATION_LABELS = {
    "analysis_run": "分析运行",
    "conversation_followup": "会话跟进",
    "model_call": "模型调用",
    "tool_call": "工具调用",
    "cache_evaluation": "缓存评估",
    "outcome_verification": "结果验证",
    "governance_action": "治理动作",
}

class FinOpsEvidenceAlias(BaseModel):
    tenant_ref: str
    workspace_id: str
    object_kind: Literal["request", "run", "trace", "apim", "price_revision"]
    object_ref: str
    operation_code: str
    workspace_name_snapshot: str
    display_name: str
    occurred_at: datetime
    created_at: datetime
```

显示时间统一转换到 `Asia/Shanghai`，名称只使用服务端 allowlist，不调用模型自由命名。

- [ ] **Step 4: 实现内存和 SQL 仓库**

接口要求如下：

- `get_or_create(value)` 按唯一键幂等返回 `FinOpsEvidenceAlias`。
- `get(tenant_ref, workspace_id, object_kind, object_ref)` 只在完整 scope 匹配时返回记录。

SQL 表：

```sql
IF OBJECT_ID(N'finops.evidence_alias', N'U') IS NULL
BEGIN
    CREATE TABLE finops.evidence_alias (
        tenant_ref nvarchar(128) NOT NULL,
        workspace_id nvarchar(160) NOT NULL,
        object_kind nvarchar(32) NOT NULL,
        object_ref nvarchar(256) NOT NULL,
        operation_code nvarchar(64) NOT NULL,
        workspace_name_snapshot nvarchar(200) NOT NULL,
        display_name nvarchar(320) NOT NULL,
        occurred_at datetime2(3) NOT NULL,
        created_at datetime2(3) NOT NULL,
        CONSTRAINT PK_finops_evidence_alias PRIMARY KEY
            (tenant_ref, workspace_id, object_kind, object_ref)
    );
END;
```

- [ ] **Step 5: 运行后端测试**

Run:

```powershell
python -m pytest tests/test_finops_evidence.py tests/test_finops_sql.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/finops/evidence.py backend/finops/evidence_repository.py backend/sql/finops_schema.sql tests/test_finops_evidence.py tests/test_finops_sql.py
git commit -m "feat(finops): add stable customer evidence aliases"
```

## Task 2: 在入账和惰性读取时建立 alias

**Files:**

- Modify: `backend/finops/ingestion.py`
- Modify: `backend/finops/reconciliation.py`
- Modify: `backend/finops/repository.py`
- Modify: `tests/test_finops_ingestion.py`
- Modify: `tests/test_finops_domain.py`
- Modify: `tests/test_finops_apim_collector.py`

- [ ] **Step 1: 写入账关联失败测试**

断言应用事件入账后：

- request alias 的 object ref 等于 `request_ref`。
- run alias 的 object ref 等于 `run_id`。
- APIM 迟到对账不会创建第二个 request alias。
- 重放相同事件保持幂等。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_ingestion.py tests/test_finops_domain.py tests/test_finops_apim_collector.py -k alias -q
```

Expected: FAIL，入账流程未写 alias。

- [ ] **Step 3: 注入 alias repository**

应用事件归一化完成后调用：

```python
alias_repository.get_or_create(
    build_evidence_alias(
        tenant_ref=event.tenant_ref,
        workspace_id=event.workspace_id,
        workspace_name=workspace_name_resolver(event.workspace_id),
        object_kind="request",
        object_ref=event.request_ref,
        operation_code=operation_code_for(event),
        occurred_at=event.occurred_at,
    )
)
```

APIM 对账只补充原事件，不另建 request alias。RunStore 兼容读取在 alias 缺失时使用同一函数惰性补齐。

- [ ] **Step 4: 运行入账与对账测试**

Run:

```powershell
python -m pytest tests/test_finops_ingestion.py tests/test_finops_domain.py tests/test_finops_apim_collector.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/finops/ingestion.py backend/finops/reconciliation.py backend/finops/repository.py tests/test_finops_ingestion.py tests/test_finops_domain.py tests/test_finops_apim_collector.py
git commit -m "feat(finops): associate ledger events with friendly evidence names"
```

## Task 3: 实现权限受控的请求详情投影

**Files:**

- Create: `backend/finops/request_detail.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_api.py`
- Create: `tests/test_finops_request_detail.py`

- [ ] **Step 1: 写详情权限与隐私失败测试**

Owner/Admin + `finops.request_detail.read` 应看到：

```python
assert detail["display"]["name"] == "Commerce · 分析运行 · 7月24日 10:42"
assert detail["business_request"]["text"] == "分析本月销售异常"
assert detail["business_response"]["text"] == "已定位主要变化来自华东区域。"
assert detail["technical_refs"]["request_ref"] == "req_aaaaaaaaaaaa"
```

不具有 request detail 权限的能力集必须返回 403，并且响应不包含正文、ID 或 Trace。列表接口继续不返回 business_request/business_response。

敏感字段断言：

```python
for forbidden in ("system_prompt", "provider_request", "provider_response", "internal_error", "join-secret"):
    assert forbidden not in response.text
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_request_detail.py tests/test_finops_api.py -k "request_detail or privacy" -q
```

Expected: FAIL，详情仍只有请求事实。

- [ ] **Step 3: 实现详情服务**

`FinOpsRequestDetailService.build()` 接收：

- 已经 tenant/workspace 收窄的 `FinOpsQuery`。
- `request_ref`。
- 当前能力键集合。
- query service、alias repository、run loader。

返回：

```python
{
    "display": {"name": alias.display_name, "operation": "分析运行"},
    "status": event.status,
    "metrics": {
        "latency_ms": event.latency_ms,
        "tokens": event.tokens.model_dump(),
        "estimated_cost": event.estimated_cost.model_dump(),
        "cache": event.cache.model_dump(),
    },
    "business_request": safe_business_request(run),
    "business_response": safe_business_response(run),
    "timeline": safe_timeline(run, event),
    "technical_refs": technical_refs if can_trace else None,
    "links": links if can_trace else {},
}
```

`safe_business_request()` 只读取 DataForge 已保存的用户消息；`safe_business_response()` 只读取最终用户可见 answer/artifact 文本。使用长度上限并丢弃非字符串、错误正文和 provider payload。

- [ ] **Step 4: 在路由中强制 capability**

`GET /api/finops/requests/{request_ref}` 必须先验证 workspace role 对应的服务端能力，再调用详情服务。返回 404 时不泄露该 request 是否存在于其他 workspace。

- [ ] **Step 5: 运行详情测试**

Run:

```powershell
python -m pytest tests/test_finops_request_detail.py tests/test_finops_api.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/finops/request_detail.py backend/finops/router.py tests/test_finops_request_detail.py tests/test_finops_api.py
git commit -m "feat(finops): project authorized business request evidence"
```

## Task 4: 增加服务端验证的 Foundry Trace 深链

**Files:**

- Modify: `backend/finops/request_detail.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/.env.example`
- Modify: `tests/test_finops_request_detail.py`
- Modify: `tests/test_finops_api.py`

- [ ] **Step 1: 写安全链接失败测试**

接受：

```text
https://ai.azure.com/
https://portal.azure.com/
https://portal.azure.cn/
```

拒绝：

- 非 HTTPS。
- 非 allowlist host。
- 用户输入 URL。
- 含 `/`、`?`、`#` 的 trace reference。
- 没有 `finops.trace.read`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_request_detail.py tests/test_finops_api.py -k trace -q
```

Expected: FAIL，尚无 Foundry Trace builder。

- [ ] **Step 3: 实现 server-owned link builder**

新增环境变量：

```text
DF_FINOPS_FOUNDRY_TRACE_LINK_TEMPLATE=
```

只允许模板包含 `{trace_id}`，格式化前验证 trace ID 为 `^[A-Za-z0-9._:-]{1,160}$`，格式化后用 `urlparse` 校验 scheme 和 hostname。无受信配置时不返回链接。

- [ ] **Step 4: 运行安全链接测试**

Run:

```powershell
python -m pytest tests/test_finops_request_detail.py tests/test_finops_api.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/finops/request_detail.py backend/finops/router.py backend/.env.example tests/test_finops_request_detail.py tests/test_finops_api.py
git commit -m "feat(finops): add validated Foundry trace links"
```

## Task 5: 重构前端请求证据抽屉

**Files:**

- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsViewModel.test.mjs`
- Modify: `web/src/styles.css`
- Create: `web/src/evidenceDrawer.test.mjs`

- [ ] **Step 1: 写友好名称和权限投影测试**

断言 `finopsRequestViewModel()`：

- 首要标题来自 `display.name`。
- 默认不展开 `technical_refs`。
- `business_request`、`business_response` 缺失时显示“未记录”。
- 无 `links.foundry_trace` 时不渲染假按钮。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
node --test web/src/finopsViewModel.test.mjs web/src/evidenceDrawer.test.mjs
```

Expected: FAIL，旧模型优先显示技术编号。

- [ ] **Step 3: 实现抽屉结构**

抽屉顺序：

1. 进入原因。
2. 客户友好名称、状态、发生时间。
3. 延迟、Token、估算成本、缓存。
4. 应用层业务请求。
5. 最终用户可见回答。
6. APIM → MAF → Agent/模型 → 返回的处理时间线。
7. 默认折叠的技术信息。
8. 有权限且链接存在时显示“在 Foundry Trace 中查看”。

不再把 `request_ref` 当作抽屉标题。

- [ ] **Step 4: 完成可访问性与移动端样式**

- `role="dialog"`、`aria-modal="true"`、可读标题。
- 打开时聚焦关闭按钮，关闭时还原触发元素焦点。
- Escape 关闭。
- Tab 焦点限制在抽屉。
- mobile 使用全屏底部层。

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
git add web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/finopsViewModel.test.mjs web/src/styles.css web/src/evidenceDrawer.test.mjs
git commit -m "feat(finops): show friendly evidence request drawer"
```

## Task 6: 完成证据下钻验收

**Files:**

- Modify: `docs/finops-portal.md`
- Create: `web/tests/finops-evidence-drawer.spec.mjs`

- [ ] **Step 1: 写 Playwright 权限场景**

验证：

- 从成本或异常卡进入正确请求证据。
- Owner 能看到业务请求、最终回答和折叠技术信息。
- 聚合财务能力不出现正文、ID 或 Trace。
- Friendly name 与后端 request/run/APIM 关联一致。
- Foundry Trace 只在受信链接存在时出现。
- 抽屉键盘关闭和 mobile 全屏行为正常。

- [ ] **Step 2: 更新隐私说明**

明确：

- 展示的是应用层用户请求和最终用户可见回答。
- 不采集 Provider 原始请求/响应。
- 技术 ID 和 Trace 的能力门禁。
- alias 的稳定性与 workspace 名称快照语义。

- [ ] **Step 3: 运行阶段回归**

Run:

```powershell
python -m pytest tests/test_finops_evidence.py tests/test_finops_request_detail.py tests/test_finops_api.py tests/test_finops_ingestion.py tests/test_finops_domain.py tests/test_finops_apim_collector.py tests/test_finops_sql.py -q
node --test web/src/*.test.mjs
Push-Location web
npx playwright test tests/finops-evidence-drawer.spec.mjs
npm run build
Pop-Location
```

Expected: 全部 PASS。

- [ ] **Step 4: 提交**

```powershell
git add docs/finops-portal.md web/tests/finops-evidence-drawer.spec.mjs
git commit -m "test(finops): verify authorized evidence drilldown"
```

## Completion Evidence

- Stable alias 和 SQL 唯一约束通过。
- 请求详情能展示正确的应用层请求和最终回答。
- 财务能力无法读取正文、技术 ID 或 Trace。
- Trace 链接只来自受信服务端配置。
- 列表和 bootstrap 不泄露技术 ID 或正文。
- Node、Vite、pytest、Playwright 都有当前分支实测输出。

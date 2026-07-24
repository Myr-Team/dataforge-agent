# FinOps Bootstrap 与运营驾驶舱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有六页 FinOps Portal 收敛为四页运营驾驶舱，并通过权限受控的 bootstrap 和浏览器会话内预加载消除首次进入时的整页等待。

**Architecture:** 后端 `FinOpsQueryService` 继续作为唯一指标口径，新增一个只组合有界摘要的 `bootstrap()`。前端用独立的内存预加载模块管理缓存、in-flight 去重、过期与取消；`App` 在工作区和能力投影解析后安排 idle 预取，导航入口在 hover、focus、touch 时复用同一预取。Portal 进入时先消费缓存，再静默校验，不预加载请求正文、请求列表、技术 ID 或 Trace。

**Tech Stack:** FastAPI、Pydantic v2、pytest、React、Vite、Node test runner、Playwright。

## Global Constraints

- 保持 MAF、工作区、数据、会话、运行和产物内核不变。
- `DF_FINOPS_READ_ENABLED` 继续作为总开关；`DF_FINOPS_ACTIONS_ENABLED` 保持默认关闭。
- 不接 Azure 实际账单，不生成示例值，不修改 Easy Auth。
- 所有聚合从服务端返回，客户端不重算成本、预算或 ROI。
- bootstrap 响应不得包含 `request_ref`、`run_id`、`trace_id`、correlation、业务请求、业务响应或 Trace URL。
- 未经明确批准，不部署、不切换生产流量。

---

## Task 1: 固化 bootstrap 查询契约

**Files:**

- Modify: `backend/finops/query.py`
- Modify: `backend/finops/query_cache.py`
- Modify: `tests/test_finops_query.py`
- Modify: `tests/test_finops_query_cache.py`

- [ ] **Step 1: 写失败的查询服务测试**

在 `tests/test_finops_query.py` 增加覆盖以下契约的测试：

```python
def test_bootstrap_reuses_query_metrics_and_bounds_summary(repository):
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        from_value="2026-06-24T00:00:00Z",
        to_value="2026-07-24T00:00:00Z",
    )
    payload = FinOpsQueryService(repository).bootstrap(query)

    assert payload["overview"]["metrics"]["requests"] == 1
    assert payload["trend"]["bucket"] == "day"
    assert len(payload["departments"]["items"]) <= 5
    assert "request_ref" not in json.dumps(payload)
    assert "run_id" not in json.dumps(payload)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_finops_query.py -k bootstrap -q
```

Expected: FAIL，提示 `FinOpsQueryService` 没有 `bootstrap`。

- [ ] **Step 3: 实现有界组合**

在 `FinOpsQueryService` 增加：

```python
def bootstrap(self, query: FinOpsQuery) -> dict[str, Any]:
    rows = self._rows(query)
    overview = self._overview_from_rows(query, rows)
    trend = self._trends_from_rows(query, rows, "day")
    departments = self._breakdowns_from_rows(query, rows, "department")
    departments["items"] = departments["items"][:5]
    departments["count"] = len(departments["items"])
    return {
        **self._envelope(query, rows),
        "overview": overview,
        "trend": trend,
        "departments": departments,
        "filters": self._filters_from_rows(query, rows),
    }
```

把 `overview()`、`trends()`、`breakdowns()`、`filters()` 的计算体提取为接收同一组 `rows` 的私有函数，保证 bootstrap 只读取仓库一次且不产生第二套口径。

- [ ] **Step 4: 给 Redis 查询缓存增加 bootstrap**

在 `CachedFinOpsQueryService` 增加：

```python
def bootstrap(self, query: FinOpsQuery) -> dict[str, Any]:
    return self._cached("bootstrap", query, lambda: self._delegate.bootstrap(query))
```

在 `tests/test_finops_query_cache.py` 的 `_Delegate` 增加 `bootstrap()`，测试同 tenant/scope 的第二次调用命中缓存，不同 tenant 不共享缓存。

- [ ] **Step 5: 运行查询层测试**

Run:

```powershell
python -m pytest tests/test_finops_query.py tests/test_finops_query_cache.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/finops/query.py backend/finops/query_cache.py tests/test_finops_query.py tests/test_finops_query_cache.py
git commit -m "feat(finops): add bounded bootstrap query"
```

## Task 2: 增加能力键与受保护的 bootstrap API

**Files:**

- Modify: `backend/control_plane.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_actor_audit_usage.py`
- Modify: `tests/test_finops_api.py`

- [ ] **Step 1: 写能力投影和 bootstrap 失败测试**

增加断言：

```python
assert payload["sections"]["finops"]["permissions"] == {
    "finops.summary.read": True,
    "finops.cost.read": True,
    "finops.roi.read": True,
    "finops.request_detail.read": True,
    "finops.trace.read": True,
    "finops.action.draft": True,
}
```

Owner/Admin 首版拥有上述能力；非管理员保持 `finops.visible == False`。同时在 `tests/test_finops_api.py` 增加：

```python
response = client.get(
    "/api/finops/bootstrap?workspace_id=ws-a&from=2026-06-24T00:00:00Z&to=2026-07-24T00:00:00Z",
    headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
)
assert response.status_code == 200
serialized = response.text
for forbidden in ("request_ref", "run_id", "trace_id", "correlation", "business_request", "business_response"):
    assert forbidden not in serialized
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest tests/test_actor_audit_usage.py tests/test_finops_api.py -k "capabilit or bootstrap" -q
```

Expected: FAIL，缺少 permissions 和 `/api/finops/bootstrap`。

- [ ] **Step 3: 服务端生成能力，不让客户端推断**

把 `backend/control_plane.py::_governance_sections()` 的 FinOps 投影改为：

```python
finops_visible = is_admin and _env_enabled("DF_FINOPS_READ_ENABLED")
finops_permissions = {
    "finops.summary.read": finops_visible,
    "finops.cost.read": finops_visible,
    "finops.roi.read": finops_visible,
    "finops.request_detail.read": finops_visible,
    "finops.trace.read": finops_visible,
    "finops.action.draft": finops_visible,
}
```

响应结构为：

```python
"finops": {
    "visible": finops_visible,
    "permissions": finops_permissions,
}
```

- [ ] **Step 4: 实现 `/api/finops/bootstrap`**

在 `backend/finops/router.py` 增加只读路由：

```python
@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    workspace_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(
        request, from_value, to_value, None, workspace_id, None, None, None
    )
    if not all(role in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="workspace access denied for finops.summary.read")
    payload = service.bootstrap(query)
    anomalies = _bounded_open_anomaly_summaries(query, limit=3)
    payload["anomalies"] = anomalies
    payload["insights"] = {"finops": None, "roi": None}
    return payload
```

`_bounded_open_anomaly_summaries()` 只返回 `anomaly_id` 的客户友好替代字段、type、severity、title、status、observed_at 和 evidence_state；在证据命名计划完成前不要返回请求技术 ID。Insight 先返回明确的 `None`，不生成示例文本。

- [ ] **Step 5: 运行 API 测试**

Run:

```powershell
python -m pytest tests/test_actor_audit_usage.py tests/test_finops_api.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/control_plane.py backend/finops/router.py tests/test_actor_audit_usage.py tests/test_finops_api.py
git commit -m "feat(finops): expose protected bootstrap summary"
```

## Task 3: 实现会话内预加载与 in-flight 去重

**Files:**

- Modify: `web/src/api.js`
- Create: `web/src/finopsPreload.js`
- Create: `web/src/finopsPreload.test.mjs`

- [ ] **Step 1: 写预加载状态机测试**

测试以下行为：

```javascript
test("same scope shares one in-flight bootstrap request", async () => {
  let calls = 0;
  const loader = async () => {
    calls += 1;
    return { freshness: { generated_at: "2026-07-24T02:00:00Z" } };
  };
  const scope = finopsScopeKey({
    tenantKey: "tenant-session",
    workspaceId: "ws-a",
    identityKey: "owner-a",
    permissions: ["finops.summary.read"],
    filters: { window: "30d" },
  });
  await Promise.all([
    prefetchFinOpsBootstrap(scope, loader),
    prefetchFinOpsBootstrap(scope, loader),
  ]);
  assert.equal(calls, 1);
});
```

另测：

- 60 秒内为 `fresh`。
- 60 秒到 5 分钟为 `stale` 且可展示。
- 超过 5 分钟为 `expired`。
- 清理 scope 会 abort 对应请求。
- 不使用 `localStorage`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
node --test web/src/finopsPreload.test.mjs
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 增加 bootstrap API loader**

在 `web/src/api.js` 增加：

```javascript
export function loadFinOpsBootstrap(filters = {}, options = {}) {
  const query = buildFinOpsQuery(filters);
  return request(`/api/finops/bootstrap${query ? `?${query}` : ""}`, options);
}
```

- [ ] **Step 4: 实现纯内存预加载模块**

`web/src/finopsPreload.js` 导出：

```javascript
const entries = new Map();
const FRESH_MS = 60_000;
const USABLE_STALE_MS = 300_000;

export function finopsScopeKey(scope) {
  return JSON.stringify({
    tenantKey: scope.tenantKey,
    workspaceId: scope.workspaceId,
    identityKey: scope.identityKey,
    permissions: [...scope.permissions].sort(),
    filters: scope.filters,
  });
}

export function readFinOpsBootstrap(key, now = Date.now()) {
  const entry = entries.get(key);
  if (!entry?.value) return { status: "missing", value: null };
  const ageMs = now - entry.storedAt;
  if (ageMs <= FRESH_MS) return { status: "fresh", value: entry.value };
  if (ageMs <= USABLE_STALE_MS) return { status: "stale", value: entry.value };
  return { status: "expired", value: null };
}
```

`prefetchFinOpsBootstrap()` 必须复用已有 Promise，保存 `AbortController`，成功后替换 value，失败时保留旧 value；`clearFinOpsBootstrap()` 按 key 或全部清理并 abort。

- [ ] **Step 5: 运行 Node 测试**

Run:

```powershell
node --test web/src/finopsPreload.test.mjs
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add web/src/api.js web/src/finopsPreload.js web/src/finopsPreload.test.mjs
git commit -m "feat(finops): add session bootstrap preloading"
```

## Task 4: 把预加载接入工作区与导航意图

**Files:**

- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Create: `web/src/finopsNavigation.test.mjs`

- [ ] **Step 1: 写导航意图测试**

把事件绑定提取为可测试的 `finopsIntentHandlers(item, onIntent)`，断言只有 `item.id === "finops"` 时 hover、focus、touch 才调用预取。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
node --test web/src/finopsNavigation.test.mjs
```

Expected: FAIL，缺少意图处理器。

- [ ] **Step 3: 在 App 中创建稳定 scope**

从服务端投影读取：

```javascript
const finopsPermissions = governanceCapabilities?.sections?.finops?.permissions || {};
const canPreloadFinOps = finopsPermissions["finops.summary.read"] === true;
```

scope key 包含：

- `authState` 作为 tenant/session 边界。
- 当前 `workspaceId`。
- `user.email` 的会话内身份键。
- 所有为 true 的 FinOps 权限键。
- 默认 30 天窗口和空筛选。

能力未解析或 `canPreloadFinOps` 为 false 时不得发请求。

- [ ] **Step 4: 实现 idle 与导航意图预取**

`useEffect` 在 workspace、身份、auth 或能力变化时先清理旧 scope，再用 `requestIdleCallback` 调用同一个 `preloadFinOps`；没有该 API 时使用不超过 250ms 的 `setTimeout`。把 `onFinOpsIntent={preloadFinOps}` 传给 `ShellNav` 和 `MobileNav`。

导航按钮增加：

```jsx
onMouseEnter={() => item.id === "finops" && onFinOpsIntent()}
onFocus={() => item.id === "finops" && onFinOpsIntent()}
onTouchStart={() => item.id === "finops" && onFinOpsIntent()}
```

- [ ] **Step 5: 运行 Node 测试**

Run:

```powershell
node --test web/src/finopsNavigation.test.mjs web/src/navigationContract.test.mjs
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add web/src/App.jsx web/src/components.jsx web/src/finopsNavigation.test.mjs
git commit -m "feat(finops): preload dashboard on workspace and navigation intent"
```

## Task 5: 将 Portal 收敛为四页并消费 bootstrap

**Files:**

- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsViewModel.test.mjs`
- Modify: `web/src/constants.js`
- Modify: `web/src/navigationContract.test.mjs`
- Modify: `web/src/styles.css`
- Modify: `web/src/components.jsx`
- Modify: `web/src/App.jsx`

- [ ] **Step 1: 写四页和六指标契约测试**

`navigationContract.test.mjs` 断言主导航只保留一个“运营驾驶舱”入口，不再默认展示 lineage/monitor/model-routing。`finopsViewModel.test.mjs` 断言六张卡为：

```javascript
assert.deepEqual(cards.map((card) => card.id), [
  "cost",
  "budget",
  "requests",
  "success",
  "p95",
  "coverage",
]);
```

预算缺失时显示“未配置”，成功率无样本时显示“未记录”。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
node --test web/src/finopsViewModel.test.mjs web/src/navigationContract.test.mjs
```

Expected: FAIL，仍是旧八指标和旧导航。

- [ ] **Step 3: 重构信息架构**

Portal 页签改为：

```javascript
const TABS = [
  { id: "overview", label: "运营总览", icon: Gauge },
  { id: "cost", label: "成本与预算", icon: WalletCards },
  { id: "roi", label: "效能与 ROI", icon: TrendingUp },
  { id: "risk", label: "风险与优化", icon: AlertTriangle },
];
```

主导航结构改为：

- 业务工作台：工作区、数据资产、会话与运行、分析产物。
- 运营治理：运营驾驶舱。
- 系统：设置。

原 `lineage`、`monitor`、`model-routing` 路由保留兼容，不再作为默认客户导航项。

- [ ] **Step 4: Portal 首屏使用 bootstrap**

`FinOpsPortal` 接收 `preloadScopeKey` 和 `permissions`。初次渲染调用 `readFinOpsBootstrap()`：

- `fresh`：立即显示并静默 revalidate。
- `stale`：显示旧数据和“数据更新中”。
- `missing/expired`：显示局部 skeleton。
- revalidate 失败：保留已知数据并显示非阻塞“更新失败”。

页面仍每 60 秒刷新聚合，但不得调用 Agent。

- [ ] **Step 5: 完成四页内容布局**

- 运营总览：六 KPI、成本与调用趋势、Top 5 部门、前三项开放异常、价值与优化摘要。
- 成本与预算：成本/预算/未计价、成本趋势、部门/专案/Agent/模型归因。
- 效能与 ROI：仅展示服务端 ROI 数据；证据不足时使用明确状态。
- 风险与优化：异常、建议、治理动作和两个 Agent 卡的插槽。

右上角更新时间用 `formatRelativeUpdateTime()` 输出“刚刚更新”“1 分钟前更新”“N 分钟前更新”；不出现“新鲜度”。

- [ ] **Step 6: 样式和响应式**

在 `web/src/styles.css` 中实现六卡 desktop 网格、tablet 三列、mobile 单列；四页页签支持横向滚动；局部 skeleton 保持页面壳和筛选可操作。

- [ ] **Step 7: 运行前端测试与构建**

Run:

```powershell
node --test web/src/*.test.mjs
Push-Location web
npm run build
Pop-Location
```

Expected: 所有 Node tests PASS，Vite build 成功。

- [ ] **Step 8: 提交**

```powershell
git add web/src/App.jsx web/src/components.jsx web/src/constants.js web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/finopsViewModel.test.mjs web/src/navigationContract.test.mjs web/src/styles.css
git commit -m "feat(finops): reshape portal as operations dashboard"
```

## Task 6: 完成阶段回归与浏览器验收

**Files:**

- Modify: `docs/finops-portal.md`
- Create: `web/tests/finops-operations-dashboard.spec.mjs`

- [ ] **Step 1: 写 Playwright 验收**

覆盖 desktop 与 mobile：

- 权限解析前没有 bootstrap 请求。
- idle、hover、focus、touch 对同 scope 只产生一次并发请求。
- 首次进入能复用 bootstrap。
- 四页和六指标可见。
- 请求列表、正文和技术 ID 未出现在 bootstrap 响应或首屏。
- workspace 切换后旧缓存不再显示。

- [ ] **Step 2: 更新运行说明**

在 `docs/finops-portal.md` 记录：

- `GET /api/finops/bootstrap` 契约。
- 60 秒/5 分钟缓存语义。
- `finops.summary.read` 门禁。
- 明确“不预加载请求详情或 Trace”。

- [ ] **Step 3: 运行阶段回归**

Run:

```powershell
python -m pytest tests/test_finops_query.py tests/test_finops_query_cache.py tests/test_finops_api.py tests/test_actor_audit_usage.py -q
node --test web/src/*.test.mjs
Push-Location web
npx playwright test tests/finops-operations-dashboard.spec.mjs
npm run build
Pop-Location
```

Expected: 全部 PASS。若 Playwright 依赖本地服务，测试输出必须显示实际启动 URL 和 desktop/mobile 两种 viewport。

- [ ] **Step 4: 提交**

```powershell
git add docs/finops-portal.md web/tests/finops-operations-dashboard.spec.mjs
git commit -m "test(finops): verify bootstrap dashboard experience"
```

## Completion Evidence

- bootstrap 一次仓库读取，响应有界且无正文/技术 ID。
- 同 scope 预加载 in-flight 去重通过。
- 权限、workspace、身份变化会取消并清理旧请求。
- Portal 仅四页，首屏仅六个核心 KPI。
- Node、Vite、pytest、Playwright 都有当前分支实测输出。
- 未部署，生产流量和治理执行状态未改变。

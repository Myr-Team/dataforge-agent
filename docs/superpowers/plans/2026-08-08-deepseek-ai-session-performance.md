# DeepSeek 接入与运营 AI 双速优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 DeepSeek 安全接入，显著降低运营 AI 历史与回答等待时间，并让运营页面在用户当前显示器上采用统一、稳定的 FinOps 比例体系。

**Architecture:** Provider 侧以 Key Vault 为凭据事实源，使用分阶段安全探测生成可操作状态；运营 AI 使用单次 bootstrap、浏览器内存与 Redis 两级缓存、复用 FinOps 查询快照，并通过现有 `direct_reply`/`full_analysis` 模型路由实现快速与深度模式。前端只调整运营模块的比例令牌和信息编排，不改变工作区、会话、产物或 Easy Auth 边界。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、Azure Key Vault、Azure SQL、Redis、React、Vite、Node Test、Playwright、Azure Container Apps。

## Global Constraints

- Provider Key 只写、不回显；日志、数据库业务表、API、Git、测试产物均不得出现明文 Key。
- Easy Auth 声明继续作为 tenant/actor 事实源，所有服务端查询必须收窄到授权 workspace。
- `DF_FINOPS_ACTIONS_ENABLED=0` 保持不变。
- `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0` 在候选验收完成前保持不变。
- 连接成功不得自动启用生产外部路由；DeepSeek 只先进入零流量候选。
- Redis 只作加速层，缓存故障必须回退 SQL，失败结果不得缓存。
- 主视觉以用户 `3056×1656` 物理像素显示环境为截图验收基准，同时回归 `1440×900`、`1024×768`、`390×844` CSS viewport。
- 运营主内容宽度控制在 `1440–1500px`，卡片主圆角约 `10px`、间距 `14px`、内边距 `18px`；不得通过浏览器缩放掩盖布局问题。
- 不生成虚假进度，不把估算成本描述为 Azure 实际账单，不把情景 ROI 描述为已实现回报。

## File Structure

- `backend/model_provider_secrets.py`：Key Vault Secret 存在性与安全错误分类。
- `backend/model_providers.py`：Provider 公共状态契约。
- `backend/provider_connection_probe.py`：DeepSeek 分阶段连接探测，不持久化敏感正文。
- `backend/model_provider_service.py`：Provider 状态编排、模型目录与探测结果持久化。
- `backend/model_provider_router.py`：可信身份、审计、公共错误映射和候选 API。
- `backend/finops/assistant_store.py`、`backend/finops/sql_assistant.py`：单次会话 bootstrap。
- `backend/finops/assistant_bootstrap.py`：Redis bootstrap 快照与精确失效。
- `backend/finops/agent_inputs.py`：面向问答的紧凑运营快照。
- `backend/finops/assistant.py`：quick/deep 公共回答契约与输出上限。
- `backend/finops/router.py`：bootstrap、非流式兼容接口、NDJSON 流式接口和路由作用域。
- `web/src/api.js`、`web/src/finopsAssistantHistory.js`：单次历史加载与 NDJSON 消费。
- `web/src/FinOpsAssistant.jsx`：即时历史、双速模式、真实阶段与重试体验。
- `web/src/ProviderConnectionsPage.jsx`、`web/src/providerConnectionsViewModel.js`：DeepSeek 凭据/连接/阶段状态。
- `web/src/finops/DecisionCharts.jsx`、`web/src/finops/RoiDecisionPage.jsx`、`web/src/styles.css`：统一比例与 ROI 价值表达。

---

### Task 1: Key Vault 凭据事实状态

**Files:**
- Modify: `backend/model_provider_secrets.py`
- Modify: `backend/model_providers.py`
- Modify: `backend/model_provider_service.py`
- Test: `tests/test_model_provider_secrets.py`
- Test: `tests/test_model_providers.py`
- Test: `tests/test_model_provider_api.py`

**Interfaces:**
- Produces: `SecretStatus = Literal["stored", "missing", "unavailable"]`
- Produces: `ModelProviderSecretStore.status(tenant_ref, provider_id, secret_ref) -> SecretStatus`
- Produces: `ModelProviderRecord.public_payload(*, secret_status: SecretStatus) -> dict[str, Any]`

- [ ] **Step 1: 写入失败测试，证明数据库记录不能再默认宣称 Secret 已保存**

```python
def test_public_payload_does_not_claim_secret_is_stored_without_vault_evidence(record):
    assert record.public_payload(secret_status="missing")["secret_status"] == "missing"

def test_key_vault_missing_secret_has_typed_status(fake_client):
    fake_client.get_secret.side_effect = ResourceNotFoundError("missing")
    store = KeyVaultModelProviderSecretStore(client=fake_client)
    assert store.status("tenant", "provider", "kv:" + provider_secret_name("tenant", "provider")) == "missing"
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run: `python -m pytest tests/test_model_provider_secrets.py tests/test_model_providers.py tests/test_model_provider_api.py -q`

Expected: FAIL，原因是 `status()` 不存在且 `public_payload()` 仍固定返回 `stored`。

- [ ] **Step 3: 实现 Secret 存在性契约和缺失分类**

```python
SecretStatus = Literal["stored", "missing", "unavailable"]

class ModelProviderSecretStore(Protocol):
    def status(self, tenant_ref: str, provider_id: str, secret_ref: str) -> SecretStatus:
        raise NotImplementedError

def status(self, tenant_ref: str, provider_id: str, secret_ref: str) -> SecretStatus:
    name = self._reference_name(tenant_ref, provider_id, secret_ref)
    try:
        value = str(self._client.get_secret(name).value or "").strip()
    except ResourceNotFoundError:
        return "missing"
    except Exception:
        return "unavailable"
    return "stored" if value else "missing"
```

`get()` 对 `ResourceNotFoundError` 抛出 `ModelProviderSecretError("provider_secret_missing")`，其他异常仍返回 `provider_secret_get_failed`。`ModelProviderService.list()` 为每条记录调用 `status()`，但任何 Vault 故障只产生 `unavailable`，不得让整个列表失败。

`ModelProviderRecord.public_payload()` 的 `secret_status` 默认值为 `unavailable`，所有生产列表和测试路径都应显式传入事实状态。`ModelProviderService.test()` 在 `secret_read` 失败时将记录更新为 `connection_state="invalid"`、`connection_stage="secret_read"` 和对应安全错误，不得保留旧的 connected 外观。

- [ ] **Step 4: 运行 Provider 单元与 API 测试**

Run: `python -m pytest tests/test_model_provider_secrets.py tests/test_model_providers.py tests/test_model_provider_api.py -q`

Expected: PASS，公共响应不包含 `secret_ref` 或 Secret 值。

- [ ] **Step 5: 提交**

```powershell
git add backend/model_provider_secrets.py backend/model_providers.py backend/model_provider_service.py tests/test_model_provider_secrets.py tests/test_model_providers.py tests/test_model_provider_api.py
git commit -m "fix(providers): report key vault credential truth"
```

### Task 2: DeepSeek 分阶段安全探测

**Files:**
- Create: `backend/provider_connection_probe.py`
- Modify: `backend/provider_client.py`
- Modify: `backend/deepseek_provider.py`
- Modify: `backend/model_provider_service.py`
- Modify: `backend/model_providers.py`
- Test: `tests/test_provider_connection_probe.py`
- Test: `tests/test_provider_http_transport.py`
- Test: `tests/test_model_provider_api.py`

**Interfaces:**
- Produces: `ConnectionProbeResult(connection_stage, stage_durations_ms, safe_error_category, models)`
- Produces: `DeepSeekConnectionProbe.run(api_key, base_url, secret_read_ms) -> ConnectionProbeResult`
- Consumes: endpoint allowlist from `validate_provider_endpoint()`、DeepSeek 官方 `/models` 目录和本地已计价模型允许列表。

- [ ] **Step 1: 写入阶段顺序、超时和安全错误测试**

```python
def test_probe_returns_only_safe_stage_metadata(probe):
    result = probe.run(api_key="secret-value", base_url="https://api.deepseek.com", secret_read_ms=2)
    assert list(result.stage_durations_ms) == [
        "secret_read", "endpoint_resolution", "tls_connect", "provider_auth",
        "minimal_inference", "model_discovery",
    ]
    assert "secret-value" not in result.model_dump_json()

@pytest.mark.parametrize("status,category", [(401, "authentication_failed"), (402, "insufficient_balance"), (429, "rate_limited")])
def test_probe_maps_provider_status_without_body(status, category, probe_factory):
    probe = probe_factory(status=status, body={"message": "sensitive"})
    result = probe.run(api_key="secret-value", base_url="https://api.deepseek.com", secret_read_ms=2)
    assert result.safe_error_category == category
    assert "sensitive" not in result.model_dump_json()
```

- [ ] **Step 2: 运行测试并确认新模块缺失**

Run: `python -m pytest tests/test_provider_connection_probe.py tests/test_provider_http_transport.py -q`

Expected: FAIL with `ModuleNotFoundError: backend.provider_connection_probe`。

- [ ] **Step 3: 实现有界探测**

```python
class ConnectionProbeResult(BaseModel):
    connection_stage: str
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    safe_error_category: str | None = None
    models: list[ProviderModel] = Field(default_factory=list)

class DeepSeekConnectionProbe:
    def run(self, *, api_key: str, base_url: str, secret_read_ms: int) -> ConnectionProbeResult:
        self._durations["secret_read"] = max(0, secret_read_ms)
        origin = validate_provider_endpoint("deepseek", base_url)
        self._stage("endpoint_resolution", lambda: self._resolve(origin))
        self._stage("tls_connect", lambda: self._tls(origin, timeout_seconds=3.0))
        self._stage("provider_auth", lambda: self._transport.get_json(
            provider_type="deepseek", base_url=origin, path="/user/balance",
            api_key=api_key, timeout_seconds=5.0,
        ))
        self._stage("minimal_inference", lambda: self._provider.invoke(
            _connection_test_invocation(), api_key=api_key, base_url=origin,
        ))
        models = self._stage("model_discovery", lambda: self._discover_models(
            self._transport.get_json(
                provider_type="deepseek", base_url=origin, path="/models",
                api_key=api_key, timeout_seconds=5.0,
            )
        ))
        return ConnectionProbeResult(connection_stage="completed", stage_durations_ms=self._durations, models=models)
```

`RequestsProviderTransport.get_json()` 必须复用 endpoint/path 校验、禁止重定向、限制响应大小并使用独立超时。`provider_auth` 只读取 `/user/balance` 的 `is_available` 布尔值，不持久化或返回金额；`model_discovery` 将 `/models` 返回的 ID 与本地已计价允许列表求交。`ProviderInvocation` 增加 `thinking: Literal["enabled", "disabled"] | None`，`DeepSeekProvider` 将其序列化为 `{"thinking": {"type": "disabled"}}`；连接测试使用 `max_tokens=1`、非业务提示词和非思考模式。失败只保留安全分类，不保留响应正文。

`ModelProviderService.test()` 用单调时钟测量 Secret 读取并把毫秒数传入探测器，确保 `secret_read` 是真实阶段而不是固定值。官方接口依据：[查询余额](https://api-docs.deepseek.com/zh-cn/api/get-user-balance/)、[模型列表](https://api-docs.deepseek.com/api/list-models)、[Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)。

- [ ] **Step 4: 将探测结果写回 Provider 记录**

在 `ModelProviderRecord` 与 `ProviderPatch` 增加 `connection_stage` 和 `stage_durations_ms`；字段均为服务端拥有，`stage_durations_ms` 仅允许已知阶段且每项为非负整数。`ModelProviderService._test_record()` 用 `DeepSeekConnectionProbe` 替换单次 30 秒调用。

- [ ] **Step 5: 运行探测和 Provider API 测试**

Run: `python -m pytest tests/test_provider_connection_probe.py tests/test_provider_http_transport.py tests/test_model_provider_api.py tests/test_deepseek_provider.py -q`

Expected: PASS，所有异常响应均无 Secret、原厂正文、内部地址或堆栈。

- [ ] **Step 6: 提交**

```powershell
git add backend/provider_connection_probe.py backend/provider_client.py backend/deepseek_provider.py backend/model_provider_service.py backend/model_providers.py tests/test_provider_connection_probe.py tests/test_provider_http_transport.py tests/test_model_provider_api.py
git commit -m "feat(providers): add staged DeepSeek diagnostics"
```

### Task 3: DeepSeek 设置页状态与重新录入体验

**Files:**
- Modify: `web/src/providerConnectionsViewModel.js`
- Modify: `web/src/ProviderConnectionsPage.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/providerConnectionsViewModel.test.mjs`
- Test: `web/tests/finops-pricing-routing-remediation.spec.mjs`

**Interfaces:**
- Consumes: `secret_status`, `connection_state`, `connection_stage`, `stage_durations_ms`, `safe_error_category`。
- Produces: `credentialLabel`, `connectionLabel`, `stageLabel`, `primaryAction`，不显示内部错误正文。

- [ ] **Step 1: 写入视图模型失败测试**

```javascript
test("missing DeepSeek credential asks for re-entry and disables test", () => {
  const item = providerConnectionsView({ items: [{
    provider_type: "deepseek", secret_status: "missing",
    connection_state: "invalid", connection_stage: "secret_read",
  }] }).items[0];
  assert.equal(item.credentialLabel, "需要重新录入 Key");
  assert.equal(item.canTest, false);
  assert.equal(item.primaryAction, "rotate_secret");
});
```

- [ ] **Step 2: 运行 Node 测试并确认失败**

Run: `cd web; node --test src/providerConnectionsViewModel.test.mjs`

Expected: FAIL，旧视图模型没有凭据事实状态。

- [ ] **Step 3: 实现三层状态卡片**

卡片固定展示“凭据”“连接”“最近检测”三行；只有 `secret_status === "stored"` 才显示“凭据已安全保存”。`missing` 时主按钮为“重新录入 Key”，检测按钮禁用；失败文案从安全映射表生成：

```javascript
const SAFE_PROVIDER_ERRORS = Object.freeze({
  provider_secret_missing: "需要重新录入 Key",
  provider_secret_write_denied: "安全凭据无法保存",
  authentication_failed: "身份认证失败，请更新 Key",
  insufficient_balance: "原厂账户余额不足",
  provider_timeout: "原厂服务响应超时",
  rate_limited: "请求过于频繁，请稍后重试",
  provider_unavailable: "原厂服务暂时不可用",
});
```

- [ ] **Step 4: 增加 Playwright 验收**

模拟 `missing -> rotate -> stored -> connected`，断言 API Key 输入值从未出现在 DOM 截图、console 或网络响应中。

- [ ] **Step 5: 运行前端测试**

Run: `cd web; node --test src/providerConnectionsViewModel.test.mjs src/finopsApi.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5211'; npx playwright test tests/finops-pricing-routing-remediation.spec.mjs`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add web/src/providerConnectionsViewModel.js web/src/ProviderConnectionsPage.jsx web/src/styles.css web/src/providerConnectionsViewModel.test.mjs web/tests/finops-pricing-routing-remediation.spec.mjs
git commit -m "feat(web): clarify DeepSeek credential and probe states"
```

### Task 4: DeepSeek 候选路由与 Azure 有界兜底

**Files:**
- Modify: `backend/provider_apim.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/provider_fallback.py`
- Modify: `backend/finops/candidate_acceptance.py`
- Test: `tests/test_provider_apim.py`
- Test: `tests/test_maf_provider_routing.py`
- Test: `tests/test_provider_fallback.py`
- Test: `tests/test_finops_candidate_acceptance.py`

**Interfaces:**
- Consumes: 已连接、受支持、已计价的 DeepSeek Provider Model。
- Produces: typed DeepSeek APIM candidate revision；不接受 XML、脚本或任意资源 ID。
- Produces: 最多一次 Azure fallback，仅限未输出、无副作用的超时、429、5xx。

- [ ] **Step 1: 写入候选门禁和兜底矩阵测试**

```python
@pytest.mark.parametrize("category,allowed", [
    ("provider_timeout", True), ("rate_limited", True),
    ("provider_unavailable", True), ("authentication_failed", False),
    ("insufficient_balance", False), ("invalid_parameters", False),
])
def test_external_route_fallback_matrix(category, allowed):
    error = ProviderFailure(category, retryable=allowed, status_code=429 if category == "rate_limited" else None)
    assert may_fallback(error, output_started=False, side_effect_started=False) is allowed

def test_candidate_rejects_unpriced_or_disconnected_deepseek_provider():
    with pytest.raises(ValueError, match="provider_candidate_ineligible"):
        validate_deepseek_candidate(connection_state="degraded", governance_state="governed", support_state="supported", price_key=None)
```

- [ ] **Step 2: 运行定向测试并确认候选校验缺口**

Run: `python -m pytest tests/test_provider_apim.py tests/test_maf_provider_routing.py tests/test_provider_fallback.py tests/test_finops_candidate_acceptance.py -q`

- [ ] **Step 3: 实现候选门禁**

候选适配器只从服务端 Provider 记录生成固定 DeepSeek revision，要求：`connection_state=connected`、`governance_state=governed`、`support_state=supported`、`price_key` 非空。生产环境变量仍为 0；候选调用通过独立 revision 和候选 model policy 完成。

- [ ] **Step 4: 实现一次性兜底并记录安全原因**

```python
if may_fallback(error, output_started=result.output_started, side_effect_started=False):
    with model_route_scope(route=azure_fallback, selection="fallback", fallback_reason=error.category):
        return invoke_once(azure_fallback)
raise error
```

不得对认证、余额、参数、配置冲突进行静默兜底。

- [ ] **Step 5: 运行路由与候选测试并提交**

Run: `python -m pytest tests/test_provider_apim.py tests/test_maf_provider_routing.py tests/test_provider_fallback.py tests/test_finops_candidate_acceptance.py -q`

```powershell
git add backend/provider_apim.py backend/maf_agents.py backend/provider_fallback.py backend/finops/candidate_acceptance.py tests/test_provider_apim.py tests/test_maf_provider_routing.py tests/test_provider_fallback.py tests/test_finops_candidate_acceptance.py
git commit -m "feat(providers): gate DeepSeek candidate routing"
```

### Task 5: 单次会话 bootstrap 与两级缓存

**Files:**
- Modify: `backend/cache_store.py`
- Modify: `backend/finops/assistant_store.py`
- Modify: `backend/finops/sql_assistant.py`
- Create: `backend/finops/assistant_bootstrap.py`
- Modify: `backend/finops/router.py`
- Test: `tests/test_finops_assistant_store.py`
- Test: `tests/test_finops_assistant_history_api.py`
- Create: `tests/test_finops_assistant_bootstrap_cache.py`

**Interfaces:**
- Produces: `AssistantBootstrap(conversation, messages, loaded_at, expires_at)`。
- Produces: `GET /api/finops/assistant/bootstrap?workspace_id=demo-corpus` 形式的授权接口。
- Produces: `AssistantBootstrapCache.load(scope, loader)` 与 `invalidate(scope)`。

- [ ] **Step 1: 写入单次 SQL 和隔离测试**

```python
def test_sql_bootstrap_uses_one_execute_and_returns_latest_messages(store, connection):
    result = store.bootstrap(scope, message_limit=40)
    assert connection.cursor_value.execute.call_count == 1
    assert result.conversation.conversation_ref == "foc_latest"

def test_bootstrap_cache_key_isolated_by_tenant_actor_and_workspace(cache, loader):
    first = cache.load(AssistantScope(tenant_ref="t1", actor_ref="a1", workspace_id="w1"), loader)
    second = cache.load(AssistantScope(tenant_ref="t1", actor_ref="a2", workspace_id="w1"), loader)
    assert first.cache_key != second.cache_key

def test_cache_failure_falls_back_to_sql_and_is_not_persisted(cache, loader):
    cache.backend.get_json.side_effect = RuntimeError("redis unavailable")
    result = cache.load(AssistantScope(tenant_ref="t1", actor_ref="a1", workspace_id="w1"), loader)
    assert result.conversation is not None
    cache.backend.set_json.assert_not_called()
```

- [ ] **Step 2: 运行测试并确认接口缺失**

Run: `python -m pytest tests/test_finops_assistant_store.py tests/test_finops_assistant_history_api.py tests/test_finops_assistant_bootstrap_cache.py -q`

- [ ] **Step 3: 在 Store 中实现一次查询 bootstrap**

```python
class AssistantBootstrap(BaseModel):
    conversation: AssistantConversation | None = None
    messages: list[AssistantMessage] = Field(default_factory=list)
    loaded_at: datetime

def bootstrap(self, scope: AssistantScope, *, message_limit: int = 40) -> AssistantBootstrap:
    return self._load_latest_conversation_and_messages(scope, message_limit=max(1, min(message_limit, 40)))
```

SQL 使用一个 CTE 选最新有效 conversation，再连接倒序 `TOP (?)` 消息，Python 恢复时间顺序；不得列出其他 conversation 后再发第二次查询。

- [ ] **Step 4: 实现 Redis 快照与精确失效**

缓存键只使用 tenant/actor/workspace 的 SHA-256 摘要，TTL 为 300 秒。`append`、`clear`、新 conversation 创建和过期清理后调用 `invalidate(scope)`。`cache_store.delete(key)` 删除精确键，不使用宽泛扫描。

- [ ] **Step 5: 增加 bootstrap 路由并验证权限**

路由先执行 `_assistant_scope()`，返回 `conversation_ref`、有限消息、`loaded_at`、`expires_at` 和 `cache_status`；member 只能加载自己的 workspace 历史。

- [ ] **Step 6: 运行后端测试并提交**

Run: `python -m pytest tests/test_finops_assistant_store.py tests/test_finops_assistant_history_api.py tests/test_finops_assistant_bootstrap_cache.py -q`

```powershell
git add backend/cache_store.py backend/finops/assistant_store.py backend/finops/sql_assistant.py backend/finops/assistant_bootstrap.py backend/finops/router.py tests/test_finops_assistant_store.py tests/test_finops_assistant_history_api.py tests/test_finops_assistant_bootstrap_cache.py
git commit -m "feat(finops): add cached assistant bootstrap"
```

### Task 6: 紧凑运营快照与 quick/deep 路由

**Files:**
- Modify: `backend/finops/assistant.py`
- Modify: `backend/finops/agent_inputs.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/demo_workspace_seed.py`
- Modify: `web/src/modelRoutingViewModel.js`
- Modify: `web/src/ModelRoutingPage.jsx`
- Test: `tests/test_finops_assistant.py`
- Test: `tests/test_finops_agent_inputs.py`
- Test: `tests/test_finops_api.py`
- Test: `tests/test_finops_demo_workspace_seed.py`
- Test: `web/src/modelRoutingViewModel.test.mjs`

**Interfaces:**
- Extends: `AssistantRequest.mode: Literal["quick", "deep"] = "quick"`。
- Produces: `build_finops_assistant_input(query, query_service, metric_context, evidence_refs)`，只含当前指标、缓存 bootstrap 摘要、最多三条证据和最近六轮历史。
- Extends: `_finops_model_route_scope(workspace_id, agent_id, execution_kind)`。

- [ ] **Step 1: 写入上下文大小与路由测试**

```python
def test_quick_context_does_not_query_full_trends_or_breakdowns(query_service):
    payload = build_finops_assistant_input(query, query_service, metric_context=context, evidence_refs=["req_1"])
    query_service.trends.assert_not_called()
    query_service.breakdowns.assert_not_called()
    assert len(json.dumps(payload, ensure_ascii=False)) < 24_000

def test_quick_uses_direct_reply_and_deep_uses_full_analysis(route_spy, client, assistant_payload):
    client.post("/api/finops/assistant/query", json=assistant_payload | {"mode": "quick"})
    client.post("/api/finops/assistant/query", json=assistant_payload | {"mode": "deep"})
    assert route_spy.execution_kinds == ["direct_reply", "full_analysis"]
```

- [ ] **Step 2: 运行定向测试并确认旧实现会读取完整趋势/分解**

Run: `python -m pytest tests/test_finops_assistant.py tests/test_finops_agent_inputs.py tests/test_finops_api.py -q`

- [ ] **Step 3: 实现紧凑快照**

`build_finops_assistant_input()` 从已授权的 `query_service.bootstrap(query)` 读取当前页面同源快照，只投影 `overview.metrics` 中当前指标必要字段、当前 scope/window、最多三条 evidence catalog 和知识边界；不再调用 31 天趋势、部门分解、workspace 分解或请求页。

- [ ] **Step 4: 实现双速模型作用域和输出上限**

```python
execution_kind = "direct_reply" if body.mode == "quick" else "full_analysis"
route_agent_id = None if body.mode == "quick" else analysis_agent_id("finops")
with _finops_model_route_scope(
    workspace_id=workspace_id,
    agent_id=route_agent_id,
    execution_kind=execution_kind,
):
    response = service.answer(request=body, evidence_payload=evidence_payload)
```

`FinOpsAssistantService.answer()` quick 使用 `max_output_tokens=650`，deep 使用 `1200`，两者继续校验相同的 `AssistantAnswerOutput`。

- [ ] **Step 5: 更新演示 workspace 推荐策略**

`assignments.direct_reply` 指向 Luna/GPT-5.1 快速路由；`df-finops-analyst` agent assignment 继续指向 Terra，保持管理员可版本化修改。

设置页在“Agent 模型分配”之前增加“运营 AI 模型”小节：快速回答读取/写入 `assignments.direct_reply`，深度分析读取/写入 `agent_assignments.df-finops-analyst`。两个选择器沿用同一 `base_revision` 保存和 409 冲突重载，不新增另一套配置表。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest tests/test_finops_assistant.py tests/test_finops_agent_inputs.py tests/test_finops_api.py tests/test_finops_demo_workspace_seed.py -q`

Run: `cd web; node --test src/modelRoutingViewModel.test.mjs`

```powershell
git add backend/finops/assistant.py backend/finops/agent_inputs.py backend/finops/router.py backend/finops/demo_workspace_seed.py web/src/modelRoutingViewModel.js web/src/ModelRoutingPage.jsx tests/test_finops_assistant.py tests/test_finops_agent_inputs.py tests/test_finops_api.py tests/test_finops_demo_workspace_seed.py web/src/modelRoutingViewModel.test.mjs
git commit -m "feat(finops): add compact dual-speed assistant"
```

### Task 7: 真实 NDJSON 阶段流

**Files:**
- Create: `backend/finops/assistant_stream.py`
- Modify: `backend/finops/router.py`
- Test: `tests/test_finops_assistant_stream.py`
- Test: `tests/test_finops_api.py`

**Interfaces:**
- Produces: `POST /api/finops/assistant/query/stream`，Content-Type `application/x-ndjson`。
- Produces events: `evidence_loading`, `context_ready`, `model_processing`, `answer_validating`, `completed|failed`。
- Consumes: Task 6 的共同 `_execute_assistant_query()`，非流式接口继续兼容。
- Extends: `AssistantRequest.request_ref` 使用 `^fai_[A-Za-z0-9_-]{8,96}$`，作为一轮消息的幂等键。

- [ ] **Step 1: 写入阶段顺序、幂等和断线测试**

```python
def test_stream_events_are_real_and_ordered(client):
    events = ndjson(client.post("/api/finops/assistant/query/stream", json=payload))
    assert [item["event"] for item in events] == [
        "evidence_loading", "context_ready", "model_processing",
        "answer_validating", "completed",
    ]
    assert events[-1]["data"]["conversation_ref"].startswith("foc_")

def test_same_request_ref_does_not_persist_duplicate_user_turn(client, assistant_payload, store):
    body = assistant_payload | {"request_ref": "fai_retry_0001"}
    client.post("/api/finops/assistant/query/stream", json=body)
    client.post("/api/finops/assistant/query/stream", json=body)
    assert [item.content for item in store.messages if item.role == "user"].count(body["question"]) == 1
```

- [ ] **Step 2: 运行测试并确认路由不存在**

Run: `python -m pytest tests/test_finops_assistant_stream.py tests/test_finops_api.py -q`

Expected: FAIL with 404。

- [ ] **Step 3: 提取共同执行器并实现 StreamingResponse**

```python
async def event_stream():
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    def emit(event: str, data: dict[str, object] | None = None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": data or {}})
    producer = asyncio.create_task(asyncio.to_thread(_execute_assistant_query, body, request, emit))
    while not producer.done() or not queue.empty():
        event = await queue.get()
        yield json.dumps(event, ensure_ascii=False) + "\n"
    await producer

return StreamingResponse(event_stream(), media_type="application/x-ndjson")
```

`_execute_assistant_query()` 在开始证据查询前发 `evidence_loading`，快照完成后发 `context_ready`；`FinOpsAssistantService.answer()` 在调用模型前发 `model_processing`，在拿到 raw structured output、开始 Pydantic/证据范围校验前发 `answer_validating`，完成后发 `completed`。因此阶段来自真实代码边界而不是定时器。公共失败事件只含 `safe_error_category` 和可重试标志。`request_ref` 使用安全格式并在持久化层防止重复写入。

- [ ] **Step 4: 运行流式与回归测试并提交**

Run: `python -m pytest tests/test_finops_assistant_stream.py tests/test_finops_api.py tests/test_finops_assistant.py -q`

```powershell
git add backend/finops/assistant_stream.py backend/finops/router.py tests/test_finops_assistant_stream.py tests/test_finops_api.py
git commit -m "feat(finops): stream assistant progress stages"
```

### Task 8: 运营 AI 即时历史、双速模式和真实进度

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/finopsAssistantHistory.js`
- Modify: `web/src/FinOpsAssistant.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/finopsApi.test.mjs`
- Test: `web/src/finopsAssistantHistory.test.mjs`
- Test: `web/src/finopsAssistant.test.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `/assistant/bootstrap` 和 `/assistant/query/stream`。
- Produces: `prefetchFinOpsAssistantHistory(workspaceId)` 单请求去重与五分钟内存快照。
- Produces: UI mode `quick|deep`、真实 progress label、保留问题的安全重试。

- [ ] **Step 1: 写入单次 bootstrap 和缓存不强刷测试**

```javascript
test("warm history renders immediately without forced network refresh", async () => {
  writeFinOpsAssistantHistory("ws-a", { conversationRef: "foc_a", messages: [{ role: "assistant", content: "cached" }] });
  const loadBootstrap = mock.fn();
  const value = await prefetchFinOpsAssistantHistory("ws-a", { loadBootstrap });
  assert.equal(value.messages[0].content, "cached");
  assert.equal(loadBootstrap.mock.callCount(), 0);
});
```

- [ ] **Step 2: 运行 Node 测试并确认旧实现仍串行加载**

Run: `cd web; node --test src/finopsApi.test.mjs src/finopsAssistantHistory.test.mjs src/finopsAssistant.test.mjs`

- [ ] **Step 3: 切换到 bootstrap API 并在页面进入时预取**

删除 conversations -> messages 串行加载；`FinOpsPortal` 确认 workspace 后立即调用 prefetch。缓存存在时直接渲染，不再传入 `force: Boolean(cached)`；后台校准只在 TTL 过期时发生。

- [ ] **Step 4: 实现 NDJSON 消费和双速控件**

```javascript
await streamFinOpsAssistant(payload, {
  onEvent(event) {
    if (event.event === "completed") appendAssistant(event.data);
    else setProgress(event.event);
  },
  signal: abortController.signal,
});
```

默认“快速回答”，显式按钮切换“深度分析”；阶段文案分别为“正在读取证据”“已准备上下文”“模型正在分析”“正在校验回答”。关闭面板不删除历史，失败保留用户问题和重试入口。

- [ ] **Step 5: 增加桌面/移动 Playwright 性能感知验收**

覆盖：预加载完成后打开即见历史、慢 bootstrap 不阻塞主页面、quick/deep 请求 mode 正确、阶段顺序真实、失败后可重试、历史切换 workspace 不串数据。

- [ ] **Step 6: 运行前端门禁并提交**

Run: `cd web; node --test`

Run: `cd web; npm run build`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5212'; npx playwright test tests/finops-operations-management.spec.mjs`

```powershell
git add web/src/api.js web/src/finopsAssistantHistory.js web/src/FinOpsAssistant.jsx web/src/FinOpsPortal.jsx web/src/styles.css web/src/finopsApi.test.mjs web/src/finopsAssistantHistory.test.mjs web/src/finopsAssistant.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "feat(web): add instant dual-speed operations AI"
```

### Task 9: 用户显示器比例与 ROI/风险视觉统一

**Files:**
- Modify: `web/src/styles.css`
- Modify: `web/src/finops/DecisionCharts.jsx`
- Modify: `web/src/finops/RoiDecisionPage.jsx`
- Test: `web/src/finopsLayout.test.mjs`
- Test: `web/src/finopsDecisionViewModel.test.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: 现有真实 ROI、成本、风险与证据数据，不增加静态演示数值。
- Produces: `finops-shell-width`, `finops-space`, `finops-radius`, `finops-card-padding` 视觉令牌。

- [ ] **Step 1: 写入比例和裁切失败测试**

```javascript
assert.match(css, /--finops-shell-width:\s*1500px/);
assert.match(css, /--finops-grid-gap:\s*14px/);
assert.match(css, /\.finops-decision-roi-panel-head small[^}]*font-size:\s*10\.5px/s);
assert.match(css, /\.finops-decision-risk-evidence-card p[^}]*font-size:\s*11px/s);
```

Playwright 在 `2037×1104` CSS viewport、`deviceScaleFactor=1.5` 下采集约 `3056×1656` 的主截图，并在 `1440×900`、`1024×768`、`390×844` 验证：无横向溢出、Tooltip 在视口内、AI 浮窗不挡刷新按钮、风险阶段线不穿过数字节点。

- [ ] **Step 2: 运行布局测试并记录旧版截图**

Run: `cd web; node --test src/finopsLayout.test.mjs src/finopsDecisionViewModel.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5213'; npx playwright test tests/finops-operations-management.spec.mjs --grep "visual proportions"`

- [ ] **Step 3: 建立比例令牌和 12 栏语义网格**

```css
.finops-page {
  --finops-shell-width: 1500px;
  --finops-grid-gap: 14px;
  --finops-radius: 10px;
  --finops-card-padding: 18px;
}
.finops-content { width: min(var(--finops-shell-width), 100%); margin-inline: auto; }
.finops-grid { gap: var(--finops-grid-gap); }
.finops-grid-wide,
.finops-decision-roi-columns,
.finops-decision-risk-columns {
  grid-template-columns: minmax(0, 8fr) minmax(320px, 4fr);
}
```

在 1024px 以下改为单列；不使用 transform/zoom 缩放整页。

- [ ] **Step 4: 统一卡片和图表比例**

KPI 高度 `136–148px`，普通图表 `280–300px`，普通卡片 10px 圆角和 18px 内边距。移除同层级重复卡片边框；正文不得低于 10.5px，核心说明不得低于 11px。

- [ ] **Step 5: 精修 ROI 价值桥与风险证据**

价值桥继续使用 `收益 − AI 运营投入 = 净收益`，三个 term 共用同一数值尺度，并在结果条明确 ROI 与回收周期；不得恢复旧版“中线左右不明”的长条。风险阶段使用四个节点，无贯穿数字的横线；证据列表使用 2:1 主辅布局并保证技术详情折叠不溢出。

- [ ] **Step 6: 运行全分辨率视觉验收并提交**

Run: `cd web; node --test src/finopsLayout.test.mjs src/finopsDecisionViewModel.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5213'; npx playwright test tests/finops-operations-management.spec.mjs`

Expected screenshots: `operations-3056-display.png`、`operations-1440.png`、`operations-1024.png`、`operations-mobile.png`，均无裁切和重叠。

```powershell
git add web/src/styles.css web/src/finops/DecisionCharts.jsx web/src/finops/RoiDecisionPage.jsx web/src/finopsLayout.test.mjs web/src/finopsDecisionViewModel.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(web): align operations visual proportions"
```

### Task 10: 全量门禁、零流量候选与性能验收

**Files:**
- Modify: `backend/finops/candidate_acceptance.py`
- Create: `docs/validation/2026-08-08-deepseek-ai-session-candidate-runbook.md`
- Test: `tests/test_finops_candidate_acceptance.py`

**Interfaces:**
- Produces: 脱敏候选验收报告，记录版本、revision、时间分布和安全分类，不记录 Key 或原厂正文。
- Produces: 生产切换前人工批准门禁。

- [ ] **Step 1: 增加候选验收断言**

验收器必须检查：Provider `secret_status=stored`、DeepSeek connected/governed/priced、一次候选调用进入统计、一次有界 Azure fallback、历史冷热采样、quick/deep 路由、跨 workspace 隔离、外部生产路由仍关闭。

- [ ] **Step 2: 运行全量本地门禁**

Run: `python -m pytest -q`

Expected: 全部通过，只有仓库既有明确 skip。

Run: `cd web; node --test`

Expected: 全部通过。

Run: `cd web; npm run build`

Expected: 成功；既有 bundle size warning 可记录但不能掩盖错误。

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5214'; npx playwright test`

Expected: 全部通过，且未复用其他 worktree 的陈旧 4173 服务。

Run: `git diff --check`

Expected: 无空白错误或冲突标记。

- [ ] **Step 3: 扫描 Secret 和测试产物**

Run: `git grep -n -I -E "(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|Bearer [A-Za-z0-9._-]{20,})" -- . ':!docs/superpowers'`

Expected: 无真实凭据命中；任何测试 fixture 字符串必须人工确认不是可用 Secret。

- [ ] **Step 4: 部署零流量 candidate**

创建不可变 backend/web 镜像和新 Container Apps revision。候选 suffix 使用 `dsai-` 加当前 Git 短 SHA；创建后通过 `az containerapp revision list` 解析完整 revision 名，并显式设置候选权重为 0、当前生产 revision 为 100。ACR 名称从目标 Container App 当前镜像域名解析，不写死已迁移环境的旧 ACR。仅在目标 Key Vault 范围给后端托管身份授予 `Key Vault Secrets Officer`；不得在订阅或资源组范围授予。保持：

```text
DF_FINOPS_ACTIONS_ENABLED=0
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
```

- [ ] **Step 5: 用户在候选页面重新录入 DeepSeek Key**

实施人员不接收、读取、复制或记录 Key。重新录入后运行连接检测、模型发现和 candidate acceptance。

- [ ] **Step 6: 采样性能与前端显示**

- 同一 workspace 冷/温打开历史各 3 次：温缓存目标 `<1s`，冷加载目标 `<5s`。
- quick 连续至少 10 次：目标 5–12s，样本 P95 `<20s`。
- deep 连续至少 5 次：首个真实阶段事件 `<1s`，目标完成 `<45s`。
- 在用户显示器环境采集 `3056×1656` 原始截图，并对照四个关键页面与 AI 浮窗。
- 验证 DeepSeek 调用 Token、模型、缓存和估算成本进入正确统计。

- [ ] **Step 7: 生成候选验收报告并提交文档**

```powershell
git add backend/finops/candidate_acceptance.py tests/test_finops_candidate_acceptance.py docs/validation/2026-08-08-deepseek-ai-session-candidate-runbook.md
git commit -m "docs: add DeepSeek assistant candidate gate"
```

- [ ] **Step 8: 停在生产人工批准门禁**

候选通过后只报告 revision、测试结果、性能分位数、截图路径、Secret 扫描结果和回滚目标。未经用户明确批准，不切换生产流量，不启用 `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED`。

# DeepSeek、计价与 Entra 身份闭环设计

日期：2026-08-09  
状态：用户已确认 A 方案，待实施计划  
范围：DataForge 生产环境中的模型提供商、Agent 模型路由、请求级估算成本、登录身份展示和权限演示

## 1. 背景与生产证据

本设计补齐“提供商连接”和“真正可运营”之间的断层。2026-08-09 的生产检查确认：

- DeepSeek 凭据已安全保存，连接页面已发现 `deepseek-v4-flash` 和 `deepseek-v4-pro`。
- 两个模型均携带价格键，但提供商仍显示“待纳管”，最近一次安全错误类别为 `provider_unavailable`。
- 生产 `DF_MODEL_ROUTE_ALLOWLIST` 仅包含四条 Azure Foundry 路由；`DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`。
- Agent 模型选择和价格关联表只遍历允许路由，因此 DeepSeek 模型即使被发现和计价也不会显示。
- 当前 DeepSeek 价格目录记录了缓存未命中输入价和输出价，但界面没有独立展示缓存命中价，且价格来源链接错误地复用了 Azure Retail Prices。
- 用户已通过 Container Apps Easy Auth 登录；前端却请求 `/.auth/me` 并把响应当作 JSON。该路径落入 Web SPA 后解析失败，页面回退为 `Demo User / local`。
- 当前 Azure CLI 身份可读取 Microsoft Entra 用户目录，但没有 User Administrator 或 Global Administrator 目录角色，不能创建新的租户账号。

## 2. 目标

1. 让 DeepSeek 从“连接记录”经过显式纳管后进入 Agent 模型选择，并能执行真实调用。
2. 为 DeepSeek 请求按缓存命中输入、缓存未命中输入和输出 Token 分项估算成本。
3. 让页面展示由可信 Easy Auth 请求头解析出的真实 Entra 姓名、账号和授权来源，不再错误显示 `local`。
4. 使用真实 Entra 主体演示 Owner、Member、Viewer 权限差异，不提供绕过认证的前端角色切换器。
5. 保留 Azure 模型作为有界回退；不启用 FinOps 自动生产治理动作。

## 3. 非目标

- 不读取、回显、导出或提交 DeepSeek Key。
- 不把 DeepSeek 原厂费用伪装为 Azure 实际账单。
- 不允许连接成功后无审批自动进入生产路由。
- 不由 DataForge 越权创建 Entra 租户用户。
- 不实现任意提供商、任意脚本或任意网关策略上传。
- 不更改 Easy Auth 的信任边界；后端仍只信任 Web 代理附带共享校验的身份头。

## 4. 提供商生命周期与路由

DeepSeek 使用以下状态流：

```text
已保存凭据
  -> 连接检测
  -> 已发现模型
  -> 已关联价格
  -> 待纳管
  -> 管理员“纳入模型路由”
  -> 已纳管
  -> 工作区 / Agent 可选择
  -> 候选真实调用
  -> 生产启用
```

### 4.1 显式纳管

提供商管理页增加“纳入模型路由”动作。动作只接受提供商 ID、基础 revision 和预定义的模型选择，不接受 XML、脚本、URL 或资源 ID。服务端要求：

- 凭据状态为 `stored`；
- 存在 `last_success_at`；
- 模型属于服务端支持集合；
- 每个候选模型均能映射到激活价格 revision；
- 当前操作者是组织 FinOps 管理员且具备工作区 Owner 权限；
- 审计写入成功。

成功后将提供商 `governance_state` 更新为 `governed`，并生成租户范围的动态外部路由快照。连接失败不会自动撤销已批准路由，但会将运行健康度标为 degraded，阻止新的主路由选择并允许有界回退。

### 4.2 动态路由目录

静态 `DF_MODEL_ROUTE_ALLOWLIST` 继续定义 Azure 基线模型。查询模型路由时，服务端在当前租户范围内追加满足“已纳管、已发现、已计价”的外部模型：

- `deepseek-v4-flash`：chat、analysis、tools、json、thinking；
- `deepseek-v4-pro`：chat、analysis、tools、json、thinking。

动态外部路由不得写回全局环境变量，避免跨租户污染。策略保存时校验路由仍属于当前租户的有效快照。公共路由响应补充 `provider_type`、`provider_id`、`health_state`、`pricing_state` 和 `selectable_reason`。

### 4.3 实际执行与回退

代码部署和提供商纳管不自动启用真实外部调用。生产启用仍由 `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED` 控制。启用前完成候选调用、Token 对账、价格手算和回退验收。

外部调用沿用受治理 Provider Gateway。仅当尚未输出内容且没有副作用时，网络超时、限流或原厂 5xx 可以切换到工作区配置的 Azure 备用模型。认证失败、余额不足和参数错误直接返回受控错误，不静默回退。

## 5. DeepSeek 计价

### 5.1 价格结构

DeepSeek 价格 revision 使用原厂官方来源，并按每百万 Token 保存：

- `cached_input_per_million`：缓存命中输入；
- `input_per_million`：缓存未命中输入；
- `output_per_million`：输出；
- `currency`：USD；
- `source_url`：DeepSeek 官方 Models & Pricing；
- `effective_at`、`reviewed_at`、`revision`。

当前官方页面列出的价格为：

| 模型 | 缓存命中输入 | 缓存未命中输入 | 输出 |
| --- | ---: | ---: | ---: |
| DeepSeek V4 Flash | $0.0028 | $0.14 | $0.28 |
| DeepSeek V4 Pro | $0.003625 | $0.435 | $0.87 |

价格会变化，生产计算必须引用持久化 revision，不能在请求时临时抓取网页。

### 5.2 成本公式

```text
estimated_cost =
  cached_input_tokens / 1,000,000 * cached_input_price
  + non_cached_input_tokens / 1,000,000 * input_price
  + output_tokens / 1,000,000 * output_price
```

若原厂 usage 只返回总 input，没有命中/未命中拆分，则 input 按缓存未命中价格估算并标记 `partial`；不得猜测缓存命中。reasoning Token 如果已包含在 output 中不得重复计价。

### 5.3 前端呈现

价格关联表按提供商分组。DeepSeek 行显示三段价格、官方来源、revision、更新时间和映射状态。模型已发现但未计价时显示“未计价”，并提供行内编辑；已计价但未纳管时显示“已计价 · 待纳管”。

## 6. Entra 登录身份

### 6.1 服务端身份接口

新增只读 `GET /api/auth/session`。接口只调用现有 `actor_from_request(request, fallback=False)`，并要求 `is_trusted_tenant_identity(actor)`。成功响应（以下值仅为契约示例）：

```json
{
  "authenticated": true,
  "identity_provider": "microsoft_entra_id",
  "user": {
    "name": "Finance Admin",
    "email": "finance.admin@contoso.example"
  },
  "tenant_state": "trusted"
}
```

公共响应不返回原始 claims、访问 Token、组原始对象 ID、租户 ID或 actor ID。未收到可信身份时返回 401，不回退到默认 Owner。

前端以该接口作为生产身份来源；仅 localhost 且没有配置身份端点时才允许显示“本地预览身份”。生产接口暂时失败时显示“身份信息同步中/暂不可用”，不得显示 `local` 或 Demo User。

### 6.2 身份与访问页面

页面增加紧凑身份摘要：

- 当前登录账号；
- 身份提供方：Microsoft Entra ID；
- 当前工作区角色；
- 授权来源：直接成员或 Entra 组；
- 组解析状态与权限状态。

不展示原始 OID、tenant ID 或组 ID；技术详情仅在受权限保护的诊断抽屉显示安全引用。

## 7. 权限演示账号

本轮不伪造账号，也不增加前端“扮演角色”开关。演示使用真实第二 Entra 主体：

1. 优先从现有租户用户中选择一个专用或低风险账号；若没有，由租户管理员创建或邀请。
2. DataForge Owner 在“身份与访问”中把该主体加入演示工作区，初始角色设为 Viewer。
3. 演示时使用独立浏览器配置文件登录第二账号，验证只能读取被授权工作区、不能保存模型路由、不能纳管提供商。
4. Owner 将其提升为 Member，再展示允许的协作能力；生产治理写操作仍仅限 Owner/组织 FinOps 管理员。
5. 演示后可停用工作区成员映射，不删除 Entra 主体。

由于当前操作身份没有目录用户创建权限，实现可以完成搜索、邀请、角色映射和验收脚本，但创建新 Entra 用户需要租户管理员执行。

## 8. API 变更

新增：

- `GET /api/auth/session`
- `POST /api/model-providers/{provider_id}/govern`
- `POST /api/model-providers/{provider_id}/ungovern`

扩展：

- `GET /api/model-providers`：补充安全的 `route_eligibility` 与 `pricing_state`。
- `GET /api/workspaces/{workspace_id}/governance/model-routing`：返回当前租户动态外部路由。
- FinOps 价格目录：增加 Provider、缓存命中价格、来源和 revision。

所有写接口使用 `base_revision`、审计和当前租户范围；冲突返回 409。

## 9. 前端设计

视觉方向保持现有“干净的企业运营控制台”，不增加新的大面积卡片：

- Agent 模型下拉按 `Azure 托管模型`、`DeepSeek 原厂` 分组；每项显示健康点、计价状态和提供商标签。
- 不可选模型仍可见但禁用，并在同一行说明“待纳管”“连接异常”或“未计价”，避免用户误以为接入丢失。
- 模型提供商卡片的主要动作根据状态变化：检测连接、补全计价、纳入路由、暂停路由。
- 价格管理与模型分配放在同一个流程中，但使用二级折叠区，避免当前页面过长。
- 顶部账户菜单显示真实姓名、账号和“Microsoft Entra ID”；生产不出现 `local`。
- 身份与访问页使用一条身份摘要和一张成员权限表完成演示，不新增假身份切换器。

## 10. 测试与验收

### 10.1 自动化

- 身份接口拒绝未受信请求，正确解析可信代理身份，且不泄漏原始标识或 claims。
- 生产身份接口失败时前端不回退 Demo User；localhost 预览仍可用。
- DeepSeek 已发现但待纳管时在模型列表可见且不可选。
- 纳管动作校验凭据、最后成功时间、支持模型、价格和 revision。
- 动态路由按 tenant 隔离，不污染静态 Azure 路由。
- DeepSeek 缓存命中、未命中和输出成本手算一致；缺失拆分时返回 partial。
- 外部失败仅在安全条件满足时回退 Azure。
- Viewer、Member、Owner 的模型与提供商权限契约测试。
- Node、Vite、Python、Playwright 与 `git diff --check` 全量通过。

### 10.2 生产前验收

1. 真实 Azure 登录后顶部显示 Entra 姓名与账号，不显示 Demo User/local。
2. DeepSeek 两个模型在选择列表可见；未纳管时原因明确，纳管后可选。
3. 选择 DeepSeek 完成一次候选调用，记录 Provider、模型、Token、缓存和成本。
4. 按价格 revision 手算一笔包含缓存命中的请求，与 Portal 一致。
5. 制造一次可安全回退的外部错误，确认 Azure 备用模型接管且不重复计费。
6. 第二 Entra 主体只能看到授权工作区，Viewer 不能写模型或提供商配置。
7. 提交、镜像和日志中不存在 Key、PAT、Token 或原厂响应正文。

## 11. 发布与回滚

1. 先部署零流量 backend/web candidate。
2. 保持 `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0` 验证身份、目录、计价和纳管 UI。
3. 在候选环境启用外部路由，完成 DeepSeek 调用、成本和回退测试。
4. 生产切换应用版本后，单独启用外部路由；`DF_FINOPS_ACTIONS_ENABLED` 继续为 0。
5. 失败时先关闭外部路由，再把流量回退到上一健康 revision。Provider 配置和 Key Vault Secret 保留，不删除、不导出。

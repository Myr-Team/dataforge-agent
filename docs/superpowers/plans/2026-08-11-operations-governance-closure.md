# DataForge 运营治理闭环实施计划

> 本计划在隔离分支中按 TDD 执行；每个任务先建立失败证据，再做最小修复并运行对应回归。

**目标：** 修复当前生产中的组织权限、DeepSeek 路由、成员身份、Entra 组治理、成本筛选与图表问题，并补齐运行记录 Trace 和 External Agent 闭环。

**架构：** 后端新增统一租户管理员能力判定，Provider、邮件和组治理复用；FinOps 查询继续由现有 API 提供但统一筛选缓存键；Trace 由 Run 记录投影和按需 Azure Monitor 查询组成，前端嵌入现有运行记录。

---

## 任务 1：租户管理员与审计持久化

- 在 `tests/` 增加配置 Owner OID、可配置应用角色、混合工作区角色和未授权身份测试。
- 新建共享租户管理员能力模块，替换邮件、Provider、Entra 组路由中的重复/过严判定。
- 为 `routing_governed`、`routing_suspended` 增加审计原因码验证并保证失败关闭。
- 运行相关 Python 测试。

## 任务 2：DeepSeek 路由与价格

- 增加真实审计校验下 Provider 纳入/暂停治理测试。
- 验证连接健康、治理状态、模型目录、Agent 路由选择和价格映射使用一致模型 ID。
- 补充 DeepSeek V4 Flash/Pro 的前端选择和未计价回退测试。
- 运行 Provider、路由、价格 Python/Node/Playwright 测试。

## 任务 3：成员企业身份与邮件配置

- 为当前可信 Entra 用户、同租户验证成员、外部成员建立显示策略测试。
- 自动建议当前受信任用户的邮箱域，但只在 workspace Owner 明确保存后作为企业显示策略；不公开未验证身份。
- 让邮件配置/测试按钮使用统一组织管理员能力，并把自动提醒开关状态明确展示。
- 验证测试邮件 API 的真实送达状态与安全错误分类。

## 任务 4：身份与访问入口及 Graph 连接

- 在设置主页面增加直接“身份与访问”入口，保留模型治理抽屉中的标签页。
- 转发 Easy Auth access/id token 头；后端返回真实 `connected/consent_required/unavailable`。
- Graph 搜索和“建立组映射”仅创建 DataForge 映射；按目标工作区校验权限。
- 配置生产应用所需 Microsoft Graph 权限、登录 scope 与 token store；若目录权限不足则展示明确管理员同意步骤。

## 任务 5：成本筛选和趋势图

- 建立模型筛选改变总览 KPI、趋势和排名的失败测试，并断言请求带 `model`。
- 修复缓存键、加载器或视图状态中的旧口径复用。
- 把趋势基线移入绘图区、日期移到轴线下；保留缓存绿色、真实比例和视口内 Tooltip。
- 运行 Node、Vite 和桌面/移动 Playwright。

## 任务 6：运行记录 Trace

- 为 workspace 授权、跨 workspace 404、旧 Run、本地步骤、远端延迟、字段白名单和嵌套脱敏建立后端测试。
- 实现 `trace-view` 查询服务和有限 KQL 投影；Azure Monitor 失败不阻塞本地 Trace。
- 在运行记录加入同页 Trace 详情、返回上下文、Span 时间线和固定高度双向滚动 JSON。
- 从风险/成本证据链接到同一 Trace 状态。

## 任务 7：Agent 运行时和 External Agent

- 统一 Agent 注册清单，补齐 FinOps/ROI，修复工具 workspace 信任边界、Audit fail-open 和失效 MCP 默认地址。
- 实现 External Agent register/verify/status CLI，注册与应用启动解耦。
- 用真实受信任 workspace 运行 smoke，核对本地 Run、遥测 Span 和 Foundry External Agent ID。

## 任务 8：全量验收与生产发布

- 运行全量 `pytest -q`、`node --test`、`npm run build`、独立端口 Playwright、`git diff --check` 和密钥扫描。
- 构建不可变 Backend/Web 镜像，部署零流量 candidate。
- 验证健康、登录态、邮件配置、DeepSeek、组映射、成员邮箱、筛选统计、图表布局和 Trace。
- 保留旧 revision，切换生产后重复关键 smoke；任何关键失败立即回滚。
- 提交、推送并同步 GitHub PR/合并记录与生产 revision 证据。

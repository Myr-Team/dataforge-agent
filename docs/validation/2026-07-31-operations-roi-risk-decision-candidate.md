# Operations ROI / Risk Decision Candidate Validation

日期：2026-08-02

分支：`codex/operations-demo-readiness`

基线：`c73c973f0f42df094bb9ee7932c68a1dc465a0d4`

## 验收范围

- ROI 情景与已验证结果分离、价值桥、证据成熟度、单位效能趋势。
- 风险矩阵、四类差异化请求证据、整改草案保存/复核与 409 重载后重新复核。
- 5/30 分钟浏览器缓存、10 分钟可见页签刷新、隐藏页暂停、失败刷新保留旧结果。
- overview、cost、ROI、risk 四页非空展示、缓存命中信息、差异化数值与图形几何。
- 桌面、1366px 和 390px 移动端布局、键盘/悬停帮助、顶栏稳定与弹层边界。
- 专用 demo completeness fixture 逐项检查四页所有可见 metric、card、chart、table 与 queue；该断言不会应用到通用生产 empty/partial 状态。
- 四类风险分别下钻到自己的 `request_ref`；整改面板在 1366px 与 390px 均验证四边可达、内部滚动及关闭后焦点恢复。

## 自动化命令与结果

最终全量命令已在本地候选分支运行：

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
```

| 门禁 | 实际结果 |
|---|---|
| Python | `1683 passed, 1 skipped, 1 warning`（128.10s；warning 为既有 MAF ExperimentalWarning） |
| Node | 原全量 `250 passed, 0 failed`；本轮相关模块 focused `33 passed, 0 failed`（2.02s） |
| Vite | 本轮重新构建成功（1789 modules；保留既有 chunk size warning） |
| Playwright | 本轮全量 `46 passed`（85.8s） |
| `git diff --check` | 通过，无空白错误或冲突标记 |

## 浏览器证据

截图保存在本地 `output/playwright/`，不纳入提交：

- `operations-cost-analysis-desktop.png`
- `operations-roi-desktop.png`
- `operations-roi-1366.png`
- `operations-roi-mobile.png`
- `operations-risk-desktop.png`
- `operations-risk-1366.png`
- `operations-risk-mobile.png`
- `operations-roi-cache-first-desktop.png`
- `operations-remediation-reviewed-desktop.png`

已检查项目：无横向溢出；非等值数据具有不同柱高/条宽/气泡大小；微额非零单位成本不显示为 `$0.00`；帮助按钮可悬停及键盘聚焦；装饰状态点已移除；整改面板位于应用顶栏下方，并在桌面/移动端均可滚动到末尾及恢复触发按钮焦点。

## 尚未执行的 live 门禁

- **已部署 backend/web candidate：未执行。** 本任务未部署镜像、未切换流量。
- **登录态候选环境截图：未执行。** 当前截图使用受控浏览器 mock。
- **真实 Redis cold / fresh hit / stale + revalidation：未执行。** 自动化仅验证内存替身和浏览器缓存契约，不作为 live Redis 证据。
- **候选数据库 SQL migration twice：未执行。** 未连接候选数据库。
- **真实统一入口 correlation、4xx/5xx、慢请求、多模型与缓存 miss→hit：未执行。** mock 样本只验证展示契约。
- **生产治理动作审批、候选执行、验证与回滚：未执行。** `DF_FINOPS_ACTIONS_ENABLED` 保持默认关闭。

## 演示数据边界

受控 mock 提供 3 个 Agent、3 个模型、3 个专案和 4 类差异化风险证据，用于验证理想界面密度与图形比例。专用 demo completeness fixture 还补齐了趋势分类与四条风险队列，以便禁止演示页面出现空洞占位；这些补充只存在于浏览器测试路由，不修改生产 API 的诚实 empty/partial 语义。它们没有在客户 UI 标记为“演示数据”，但也不被记录为生产 observed 证据；成本与 ROI 场景保持 `estimated / partial` 状态。

生产演示工作区的真实 seed 是否达到同等密度尚未验证，不能由本地 mock 通过推断为已完成。

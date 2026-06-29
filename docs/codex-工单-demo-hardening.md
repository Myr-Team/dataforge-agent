# 📦 codex 工单 · DataForge Demo 健壮性 + 不自欺可见化（T1–T7 一次做完）

> 基线：`main` = `fda91ad`（优化第4批: 迭代收敛图）。
> 要求：T1–T7 **全部做完再发一个 PR**，不分批交。验收看实测证据，不看 checklist。

---

## 0. 运行环境建议（先看）

后端 Python/FastAPI + 前端 Vite，**部署目标是 Azure Container Apps = Linux**。PowerShell 能用但有坑，建议：

- **首选：WSL2(Ubuntu) 里干活**。原因：① 跟生产 Linux 一致；② 有 `grep/sed/jq/bash` 一致工具链；③ 没有 CRLF 换行符地雷；④ Python venv 行为和 CI 一致。把仓库 clone 在 **WSL 自己的文件系统里**（`~/`，别放 `/mnt/c`，跨盘 IO 很慢）。
- **若坚持 PowerShell**：`az / git / npm / python` 都跨平台，能用，但务必注意：
  1. **换行符**：Windows 编辑易塞 CRLF，进 Linux 容器会让 shell 脚本/Dockerfile 失效。设 `git config core.autocrlf input`，或在 `.gitattributes` 加 `* text=auto eol=lf`（脚本/Dockerfile 强制 LF）。
  2. **镜像构建在云端**：`az acr build` 是 ACR Tasks 在云上 Linux 构建，**本地 OS 不影响产出的镜像**——只有 build context（受 `.dockerignore` 约束）会被上传。Windows 风险因此大降。
  3. `vite build` 在本地跑，Windows 没问题；venv 激活是 `.venv\Scripts\Activate.ps1`。
  4. 多行命令别用 bash heredoc，用 PowerShell here-string `@" "@` 或直接写文件。
- **别混用**：git 操作固定用一个 shell，避免反复 CRLF 抖动污染 diff。

---

## 1. 全局规则

- 新建分支 `feat/demo-hardening`（基于最新 `main` = `fda91ad`），**T1–T7 全部做完再发一个 PR**，不要一项一交。
- **验收标准是"实测 + 证据"，不是过 checklist**。每项给出：实跑结果 / file:line 证据 / 截图，缺一不算完成。
- **红线**：
  1. 通用别固化——不许按数据名/关键词写死结论、分数、降档、机会；
  2. 改完真实跑一次完整分析验证；
  3. 不动 auth。

---

## 2. 任务（共 7 项）

### T1 · LLM 调用重试（核心，P0）
- **目标**：消除 Foundry 瞬时抖动导致整轮分析冒泡成 network error。
- **锚点**：`backend/foundry_client.py`——真正的 gpt-5.1 调用（含 `grounded_chat_stream`、`responses_stream` 及各非流式 agent 子调用）目前**无任何 retry/backoff**。
- **做法**：在 foundry_client 边界**集中**加一个重试 helper/装饰器，别散落各处。
  - 非流式调用：3 次指数退避（0.5s→1s→2s + jitter），**仅**对瞬时错误（429 / 5xx / 连接重置 / 超时）重试；
  - 流式主调用：**只在收到首个 token 之前**失败才重试，已出 token 不重放。
- **红线**：内容安全拒绝 / 4xx 业务错误**不**重试。
- **验收**：注入一次 429/连接错误（可临时 monkeypatch），分析仍完整跑通；日志显示重试发生且退避正确。

### T2 · 前端自动重试 + 不丢已出内容（P0）
- **锚点**：`web/src/api.js:streamChat`、`web/src/App.jsx` run() 的 catch（约 406–510，`streamErrorMessage` / "连接已断开" 约 486、catch 约 489）。
- **做法**：fetch 抛 `Failed to fetch`（原生网络瞬断）时**静默重试 1 次**；若本轮已流出部分内容，则**保留已渲染内容**，把错误降级为非阻断提示 + 一个"重试/继续"按钮，**不要整条红屏清空**。区分 AbortError(主动停止)、网络错、服务端 error 事件三类，文案各异。
- **验收**：演示中途断网再恢复，已出内容不消失、点重试能续；主动停止仍显示"已停止"。

### T3 · 后端不冷启动（P0 · infra）
- **做法**：`ca-dataforge-backend`（和 `ca-dataforge-web`）`minReplicas` 设为 1，演示期不 scale-to-zero。`az containerapp update --min-replicas 1 ...`。注明是**演示期临时配置**，演示后可调回 0 省钱。
- **验收**：容器空闲 30 分钟后，首次点"分析"无 5–15s 假死。

### T4 · 产物生成降级（P0）
- **锚点**：`backend/orchestrator.py` producer/concept-image 路径（约 3777 producer_task timeout 附近）、`backend/tools/render_pdf.py`、produce endpoint。
- **做法**：概念图生成失败（重试耗尽/超时）时**不让整个产物链失败**，仍产出 PDF 建议书，并在结果里明确标注"概念图生成失败，建议书已生成"。
- **验收**：强制图生成抛错，仍拿到可下载 PDF + 友好提示。

### T5 · "自我降档"可见化（P1 · 差异化核心）
- **目标**：把"不自欺"从话术变成评委**亲眼可见的一幕**。
- **做法**：当审计⇄复修环把过高结论纠正/降档时，在 trace/artifact 数据里携带 `verdict_before / verdict_after / downgrade_reason`，前端 `web/src/components.jsx`（VerdictHero 附近）显式呈现"⚠️ 审计已将结论从 X 降为 Y，因为证据不足以支撑 X"的一条醒目记录。
- **先核实**：审计环当前是否真会产生降档事件、数据模型是否已携带前后结论——没有就补上（与 T7 联动）。
- **红线**：降档判断必须由证据强度驱动，**不许按数据名/场景写死**。
- **验收**：跑一份证据弱的数据，**肉眼看到结论被压档 + 理由**。

### T6 · 杀手数据集验证（P1）
- **做法**：用候选**真实含噪**数据（亚马逊真信号那类，**不要**用合成陷阱集）各跑一遍，报告哪份能产出"**非显然的机会 + 一次可见的自我降档(T5)**"。产出 demo 首选数据集 + 证据截图。
- **验收**：换一份数据复跑不露馅（结论随证据变化，不是预设）。

### T7 · Gap Analysis：核验"不自欺"是真的（P1 · 防红队）
- **目标**：用 Intended-vs-Implemented 思路证明 verdict 确实被证据强度封顶、审计环真能 downgrade，而非装饰。
- **做法**：审 `backend/orchestrator.py` 评分/封顶/审计逻辑（含之前 A1 `_diversify_feasibility_scores_data`、`backend/feasibility_rubric.py`），给出 **file:line 证据** + 一个"证据弱 → 结论被压档"的可复现实跑。若发现其实没真正封顶（说一套做一套），**列为缺陷并在本 PR 修掉**。
- **验收**：交一段简短 gap 报告（设计意图 vs 代码实际）+ 实跑佐证。

---

## 3. 构建与部署
- 前端：`az acr build ... -t dataforge-web:hardening`（云端 Linux 构建，Windows 无忧）→ `az containerapp update -n ca-dataforge-web ...`。
- 后端同理 `dataforge-backend:hardening` → 更新 `ca-dataforge-backend`，记得带 `--min-replicas 1`（T3）。
- 资源：RG `rg-dataforge-dev`、ACR `acrdataforgedev`、容器 `ca-dataforge-backend / ca-dataforge-web`。
- 部署后**真实跑一次完整分析 + 产物**验证全链路。

---

## 4. 回报格式（一次性）
做完发一个 PR，PR 描述里逐项给：
- T1–T7 各自的**实测证据**（日志片段 / file:line / 截图）；
- 改了哪些文件；
- 部署的镜像 tag；
- T6 的 demo 数据集推荐；
- T7 的 gap 报告（设计 vs 实际 + 实跑）。

> Reviewer 会按红线和验收逐条核验，**不接受"已添加 XX 逻辑 ✓"这种无证据条目**。

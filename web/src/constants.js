import {
  Archive,
  Bot,
  Boxes,
  ClipboardList,
  Compass,
  FileText,
  FlaskConical,
  GitBranch,
  Home,
  Image,
  LineChart,
  ListChecks,
  Map,
  MessageSquare,
  Mic2,
  PackageCheck,
  PanelRight,
  Route,
  Search,
  Settings,
  Sparkles,
  Target,
  WalletCards,
} from "lucide-react";

export const NAV_ITEMS = [
  { id: "workspaces", label: "工作区", icon: Home },
  { id: "runs", label: "运行记录", icon: Route },
  { id: "conversations", label: "会话", icon: MessageSquare },
  { id: "artifacts", label: "产物", icon: Archive },
  { id: "settings", label: "设置", icon: Settings },
];

export const AGENTS = [
  { id: "df-coordinator", name: "Coordinator", zh: "协调器", role: "任务编排", icon: Bot },
  { id: "df-corpus-analyst", name: "Corpus Analyst", zh: "语料分析师", role: "检索与画像", icon: Search },
  { id: "df-feasibility-analyst", name: "Feasibility Analyst", zh: "可行性分析师", role: "评分与机会", icon: LineChart },
  { id: "df-market-researcher", name: "Market Researcher", zh: "市场研究员", role: "外部行情", icon: Compass },
  { id: "df-auditor", name: "Auditor", zh: "审计员", role: "证据核验", icon: ListChecks },
  { id: "df-producer", name: "Producer", zh: "产物生成器", role: "PDF / 图 / 语音", icon: PackageCheck },
];

export const PLAYBOOKS = [
  { id: "opportunity-tree", name: "机会树", prompt: "Opportunity Solution Tree", icon: GitBranch },
  { id: "jtbd", name: "JTBD", prompt: "Jobs To Be Done", icon: Target },
  { id: "pricing", name: "定价", prompt: "Pricing and SaaS economics", icon: WalletCards },
  { id: "roadmap", name: "路线图", prompt: "Roadmap planning", icon: Route },
  { id: "prd", name: "PRD", prompt: "PRD development", icon: FileText },
  { id: "experiment", name: "实验验证", prompt: "Validation experiment design", icon: FlaskConical },
];

export const QUESTION_STARTERS = [
  { id: "fit", label: "能产品化成什么？", prompt: "这批数据最适合产品化成什么机会？请给出证据、风险和下一步。" },
  { id: "segment", label: "先试点哪个客群？", prompt: "基于当前数据，哪个客群或场景最值得先试点？请说明证据强弱。" },
  { id: "evidence", label: "证据最强/最弱在哪里？", prompt: "请只根据工作区数据，列出支持产品化的最强证据和最大的证据缺口。" },
  { id: "prd", label: "生成 PRD 草案", prompt: "请把当前机会整理成 PRD 草案，包括目标用户、核心场景、功能边界和验收指标。" },
  { id: "roadmap", label: "做路线图", prompt: "请给出 30/60/90 天路线图，说明每一步依赖哪些数据证据。" },
  { id: "pricing", label: "评估定价", prompt: "请评估这个数据产品的定价路径、价值锚点和需要补充的市场证据。" },
  { id: "experiment", label: "设计验证实验", prompt: "请设计一个小规模验证实验，包含假设、样本、指标、成功门槛和风险控制。" },
  { id: "market", label: "看外部市场", prompt: "请结合工作区证据和外部市场信息，判断这个机会的差异化是否足够。" },
];

export const ARTIFACT_MODES = [
  { id: "chat", label: "只分析" },
  { id: "report", label: "报告" },
  { id: "full_package", label: "全套产物" },
];

export const ARTIFACT_GROUPS = [
  { id: "prd", title: "PRD", description: "目标用户、场景、功能边界", icon: ClipboardList },
  { id: "roadmap", title: "路线图", description: "30/60/90 天交付节奏", icon: Map },
  { id: "experiment", title: "实验计划", description: "假设、指标、样本和门槛", icon: FlaskConical },
  { id: "pricing", title: "定价建议", description: "价值锚点和商业化路径", icon: WalletCards },
  { id: "pdf", title: "项目书", description: "可下载 PDF 提案", icon: FileText },
  { id: "concept_image", title: "概念图", description: "产品概念视觉", icon: Image },
  { id: "audio_summary", title: "语音摘要", description: "可播放汇报摘要", icon: Mic2 },
];

export const DIMENSION_LABELS = {
  market: "市场机会",
  technical: "技术可行性",
  asset_data: "资产数据",
  resource_cost: "成本结构",
  differentiation_risk: "差异化能力",
};

export const CONFIDENCE_LABELS = {
  data_confirmed: "数据已证实",
  market_inferred: "市场推断",
  speculative: "待验证",
};

export const CONFIDENCE_DESCRIPTIONS = {
  data_confirmed: "来自上传数据或工作区资料，可作为内部事实使用。",
  market_inferred: "来自外部市场或 MCP/web 工具，只能作为市场参考。",
  speculative: "推理或缺口判断，需要补充数据后再确认。",
};

export const VERDICT_LABELS = {
  feasible: "可行",
  conditional: "有条件可行",
  not_yet_feasible: "暂不适合",
  completed: "完成",
  product_feasibility: "可行性分析",
};

export const INSPECTOR_TABS = [
  { id: "evidence", label: "Evidence", icon: PanelRight },
  { id: "trace", label: "Trace", icon: Route },
  { id: "output", label: "Output", icon: Boxes },
];

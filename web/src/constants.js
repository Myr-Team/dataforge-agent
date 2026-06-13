import {
  Archive,
  Bot,
  Boxes,
  Compass,
  FileText,
  GitBranch,
  LineChart,
  ListChecks,
  MessageSquare,
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
  { id: "workspaces", label: "Workspaces", icon: Boxes },
  { id: "runs", label: "Runs", icon: Route },
  { id: "conversations", label: "Conversations", icon: MessageSquare },
  { id: "artifacts", label: "Artifacts", icon: Archive },
  { id: "settings", label: "Settings", icon: Settings },
];

export const AGENTS = [
  { id: "df-coordinator", name: "协调器", role: "意图与编排", icon: Bot },
  { id: "df-corpus-analyst", name: "语料分析师", role: "检索与画像", icon: Search },
  { id: "df-feasibility-analyst", name: "可行性分析师", role: "评分与机会", icon: LineChart },
  { id: "df-market-researcher", name: "市场研究员", role: "外部行情", icon: Compass },
  { id: "df-auditor", name: "审计员", role: "证据核验", icon: ListChecks },
  { id: "df-producer", name: "产物生成器", role: "PDF / 图 / 语音", icon: PackageCheck },
];

export const PLAYBOOKS = [
  { id: "opportunity-tree", name: "机会树", prompt: "Opportunity Solution Tree", icon: GitBranch },
  { id: "jtbd", name: "JTBD", prompt: "Jobs To Be Done", icon: Target },
  { id: "pricing", name: "定价", prompt: "Pricing and SaaS economics", icon: WalletCards },
  { id: "roadmap", name: "路线图", prompt: "Roadmap planning", icon: Route },
  { id: "prd", name: "PRD", prompt: "PRD development", icon: FileText },
  { id: "experiment", name: "实验验证", prompt: "Validation experiment design", icon: Sparkles },
];

export const ARTIFACT_MODES = [
  { id: "chat", label: "只分析" },
  { id: "report", label: "报告" },
  { id: "full_package", label: "全套产物" },
];

export const DIMENSION_LABELS = {
  market: "市场",
  technical: "技术",
  asset_data: "资产数据",
  resource_cost: "成本",
  differentiation_risk: "差异化",
};

export const CONFIDENCE_LABELS = {
  data_confirmed: "数据已证实",
  market_inferred: "市场推断",
  speculative: "待验证",
};

export const VERDICT_LABELS = {
  feasible: "可行",
  conditional: "条件可行",
  not_yet_feasible: "暂不适合",
  completed: "完成",
  product_feasibility: "可行性分析",
};

export const INSPECTOR_TABS = [
  { id: "evidence", label: "Evidence", icon: PanelRight },
  { id: "trace", label: "Trace", icon: Route },
  { id: "output", label: "Output", icon: PackageCheck },
];

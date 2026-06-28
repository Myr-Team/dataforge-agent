import { driver } from "driver.js";
import "driver.js/dist/driver.css";

// 产品引导步骤。分析后才出现的元素(结论/产物/迭代)若当前未渲染，
// 自动降级为居中说明卡(不高亮),保证不报错、流程仍讲得清。
const STEPS = [
  {
    sel: '[data-tour="upload"]',
    title: "1 · 上传数据",
    desc: "把你的业务数据（CSV / Excel / JSON / 图片）丢进来，系统会自动做数据画像、建立检索索引。",
  },
  {
    sel: '[data-tour="analyze"]',
    title: "2 · 自动分析",
    desc: "一键让多个 Agent 协作，从你的数据里发现“能做成产品的机会”，并量化可行性。",
  },
  {
    sel: '[data-tour="pipeline"]',
    title: "3 · 数据解析状态",
    desc: "看数据从上传 → 格式识别 → 画像 → 检索入库 → Agent 就绪的全过程。",
  },
  {
    sel: '[data-tour="verdict"]',
    title: "4 · 可行性结论",
    desc: "五维评分 + 证据可溯源 + 审计自我修正；结论不会超过证据强度能支撑的档位。",
  },
  {
    sel: ['[data-tour="produce"]', '[data-tour="artifacts-nav"]'],
    title: "5 · 生成产物",
    desc: "拍板后一键产出可下载的 PDF 可行性建议书 + 产品概念图；左栏「产物」里可随时查看。",
  },
  {
    sel: '[data-tour="runs"]',
    title: "6 · 运行记录",
    desc: "每次分析自动存档，点任意一条即可一键恢复整段会话、回放推理过程。",
  },
  {
    sel: '[data-tour="iterate"]',
    title: "7 · 方案迭代",
    desc: "把试点跑出来的真实指标（客获率 / 客单价）回填进去，一版版逼近能落地的公司重点方案。",
  },
];

export function startTour() {
  const steps = STEPS.map((s) => {
    const selectors = Array.isArray(s.sel) ? s.sel : [s.sel];
    const found = typeof document !== "undefined"
      ? selectors.find((sel) => document.querySelector(sel))
      : null;
    const popover = { title: s.title, description: s.desc };
    return found ? { element: found, popover } : { popover };
  });
  const tour = driver({
    showProgress: true,
    allowClose: true,
    overlayColor: "rgba(15, 23, 42, 0.62)",
    stagePadding: 6,
    stageRadius: 10,
    popoverClass: "df-tour",
    nextBtnText: "下一步",
    prevBtnText: "上一步",
    doneBtnText: "完成",
    progressText: "{{current}} / {{total}}",
    steps,
  });
  tour.drive();
}

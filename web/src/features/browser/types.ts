/** 内置浏览器用到的数据结构。字段与后端 deck 模型一一对应。 */

export type BrowserSource = "artifact" | "web" | "deck";

/** 工作台的全部状态：独立页放 URL，智能体页面板放组件内。 */
export interface BrowserState {
  tab: BrowserSource;
  project: string;
  path: string;
  url: string;
}

export const BROWSER_TABS: { id: BrowserSource; label: string }[] = [
  { id: "artifact", label: "产物" },
  { id: "web", label: "网页" },
  { id: "deck", label: "演示" },
];

/** 按文件后缀决定落到哪个标签：pptx 进编辑器，其余进产物预览。 */
export function tabForPath(path: string): BrowserSource {
  return path.toLowerCase().endsWith(".pptx") ? "deck" : "artifact";
}

export interface TreeEntry {
  path: string;
  type: "file" | "dir";
  size?: string;
}

export interface ProjectInfo {
  id: string;
  name: string;
  files: number;
  size: string;
  updated_at: string;
}

export type SlideLayout =
  | "title"
  | "section"
  | "bullets"
  | "two_col"
  | "quote"
  | "table"
  | "stat"
  | "chart"
  | "timeline"
  | "grid"
  | "vs"
  | "end";

export interface DeckColumn {
  title: string;
  bullets: string[];
}

export interface DeckStat {
  value: string;
  label: string;
  note: string;
}

export interface DeckBarItem {
  label: string;
  value: string;
}

export interface DeckStep {
  label: string;
  text: string;
}

export interface DeckCard {
  title: string;
  text: string;
}

export interface DeckSlide {
  layout: SlideLayout;
  title?: string;
  subtitle?: string;
  kicker?: string;
  number?: string;
  quote?: string;
  variant?: string;
  note?: string;
  bullets?: string[];
  columns?: DeckColumn[];
  headers?: string[];
  rows?: string[][];
  stats?: DeckStat[];
  items?: DeckBarItem[];
  steps?: DeckStep[];
  cards?: DeckCard[];
  left?: DeckColumn;
  right?: DeckColumn;
}

export interface Deck {
  path: string;
  title: string;
  subtitle: string;
  author: string;
  theme: string;
  theme_inferred?: boolean;
  source: "sidecar" | "parsed";
  editable: "full" | "partial";
  slides: DeckSlide[];
  /** 服务端用 PowerPoint 真实渲染的逐页 JPG（项目内相对路径），还原渐变/圆角等视觉 */
  preview_images?: string[];
  bytes: number;
  updated_at: string;
}

export interface ThemePreset {
  id: string;
  name: string;
  bg: string;
  surface: string;
  accent: string;
  accent2: string;
  on_dark: string;
  on_dark_muted: string;
  ink: string;
  ink_muted: string;
  paper: string;
}

export interface ReadPayload {
  url: string;
  final_url: string;
  content_type: string;
  title: string;
  text: string;
  chars: number;
  kind: string;
}

export const LAYOUT_LABELS: Record<SlideLayout, string> = {
  title: "封面",
  section: "章节",
  bullets: "要点",
  two_col: "两栏",
  quote: "引用",
  table: "表格",
  stat: "大数字",
  chart: "条形图",
  timeline: "时间线",
  grid: "卡片矩阵",
  vs: "对比",
  end: "结尾",
};

/** 新建页时的默认内容，保证一加就能看。 */
export function blankSlide(layout: SlideLayout): DeckSlide {
  switch (layout) {
    case "title":
      return { layout, title: "新封面", subtitle: "" };
    case "section":
      return { layout, number: "", title: "章节标题", subtitle: "" };
    case "two_col":
      return {
        layout,
        title: "两栏对比",
        columns: [
          { title: "左边", bullets: ["要点一"] },
          { title: "右边", bullets: ["要点二"] },
        ],
      };
    case "quote":
      return { layout, quote: "一句值得单独占一页的话", subtitle: "" };
    case "stat":
      return {
        layout,
        title: "核心指标",
        stats: [
          { value: "38%", label: "指标一", note: "" },
          { value: "15%", label: "指标二", note: "" },
        ],
      };
    case "chart":
      return {
        layout,
        title: "数据对比",
        items: [
          { label: "项目一", value: "12" },
          { label: "项目二", value: "8" },
          { label: "项目三", value: "5" },
        ],
      };
    case "timeline":
      return {
        layout,
        title: "落地节奏",
        steps: [
          { label: "第一步", text: "做什么" },
          { label: "第二步", text: "再做什么" },
          { label: "第三步", text: "最后做什么" },
        ],
      };
    case "grid":
      return {
        layout,
        title: "要点矩阵",
        cards: [
          { title: "卡片一", text: "说明文字" },
          { title: "卡片二", text: "说明文字" },
          { title: "卡片三", text: "说明文字" },
          { title: "卡片四", text: "说明文字" },
        ],
      };
    case "vs":
      return {
        layout,
        title: "方案对比",
        left: { title: "方案 A", bullets: ["优势一"] },
        right: { title: "方案 B", bullets: ["优势一"] },
      };
    case "table":
      return { layout, title: "数据表", headers: ["项目", "数值"], rows: [["示例", "0"]] };
    case "end":
      return { layout, title: "谢谢", subtitle: "" };
    default:
      return { layout: "bullets", title: "新页面", bullets: ["第一条", "第二条"] };
  }
}

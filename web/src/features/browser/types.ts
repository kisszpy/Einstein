/** 内置浏览器用到的数据结构。字段与后端 deck 模型一一对应。 */

export type BrowserSource = "artifact" | "web" | "deck";

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

export type SlideLayout = "title" | "section" | "bullets" | "two_col" | "quote" | "table" | "end";

export interface DeckColumn {
  title: string;
  bullets: string[];
}

export interface DeckSlide {
  layout: SlideLayout;
  title?: string;
  subtitle?: string;
  kicker?: string;
  number?: string;
  quote?: string;
  bullets?: string[];
  columns?: DeckColumn[];
  headers?: string[];
  rows?: string[][];
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
    case "table":
      return { layout, title: "数据表", headers: ["项目", "数值"], rows: [["示例", "0"]] };
    case "end":
      return { layout, title: "谢谢", subtitle: "" };
    default:
      return { layout: "bullets", title: "新页面", bullets: ["第一条", "第二条"] };
  }
}

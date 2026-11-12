/**
 * AI PPT 制作用的数据结构。字段与后端 ppt 路由的 JSON 一一对应（snake_case 直传，
 * 与 browser/types.ts 的既有约定一致，不做 camelCase 转换层）。
 */

export type PptPhase =
  | "collecting"
  | "kb"
  | "outlining"
  | "ready"
  | "clarifying"
  | "deepening"
  | "rendering"
  | "composing"
  | "measuring"
  | "repairing"
  | "polishing"
  | "writing"
  | "done";

export type PptSessionStatus =
  | "idle"
  | "analyzing"
  | "awaiting_confirm"
  | "generating"
  | "done"
  | "failed";

export interface ThemePackSummary {
  id: string;
  /** builtin = 内置设计风格（无需上传），upload = 用户上传的公司模板包 */
  kind?: "builtin" | "upload";
  name: string;
  tagline?: string;
  owner_id: string;
  status: "ready" | "partial" | "failed";
  layout_count: number;
  fidelity_score: number;
  swatches: string[];
  /** 从模板版面抽样出来的基调与画布比例（裸 hex 色板 + "4:3" 这类标签） */
  mode?: "dark" | "light" | "";
  ratio?: string;
  created_at: string;
}

/** 模板风格抽取报告：与后端 template_style.extract 的 report 一一对应。 */
export interface TemplateStyleReport {
  version: number;
  mode: "dark" | "light";
  background: string;
  accent: string;
  accent2: string;
  chart: string[];
  cover_stops: string[];
  motif: string;
  fonts: { display: string; body: string; source_display: string; source_body: string };
  canvas: number[];
  ratio: string;
  slide_size: { width_in: number; height_in: number };
  margin_px: number;
  radius_px: number;
  /** 适配说明：字体替换、深封面统一为浅内页、异形画布归一等 */
  notes: string[];
  evidence: { backgrounds: number; fills: number; cards: number; layouts: number };
  summary_zh: string;
}

export interface ThemePackDetail extends ThemePackSummary {
  source_filename: string;
  slide_size: { width_in: number; height_in: number };
  palette: Record<string, string>;
  fonts: Record<string, string>;
  layouts: LayoutInfo[];
  style: Partial<TemplateStyleReport>;
  header_footer: { has_slide_number: boolean; has_date: boolean; snippets: string[] };
  fidelity: FidelityReport;
}

export interface LayoutInfo {
  index: number;
  name: string;
  kind: string;
  placeholders: Array<{
    idx: number;
    ph_type: string;
    left: number;
    top: number;
    width: number;
    height: number;
  }>;
  has_title: boolean;
  has_body: boolean;
}

export interface FidelityIssue {
  kind: string;
  slide_index: number;
  detail: string;
  fallback: string;
}

export interface FidelityReport {
  score: number;
  issues: FidelityIssue[];
  degradations: string[];
  summary_zh: string;
  /**
   * 渲染路径 = 风格来源 × 排版方式：
   * html/shapes 是内置风格，template/template-shapes 是按上传模板重绘，
   * 带 -shapes 的两条是回落（手写坐标 / 母版套版），排版不如实测。
   */
  engine?: "html" | "shapes" | "template" | "template-shapes";
  /** 成稿出处：生成时快照，模板日后被删也还能说清当时按谁的风格画的 */
  template?: { name: string; ratio: string; mode: "dark" | "light" };
  /** 美感检测与润色：检出多少、改掉多少、哪些改不了 */
  aesthetic?: AestheticReport;
  /** 按内容生成的设计方向（没选固定风格时才有） */
  direction?: DesignDirection;
  /** 逐页重排：哪些页由技能重新组织、哪些回退了 */
  composition?: {
    skill_pages: Array<{ index: number; why: string }>;
    fallbacks: string[];
  };
}

/** 这份稿子按内容生成的视觉方向（不是六套预置之一）。 */
export interface DesignDirection {
  key: string;
  name: string;
  thesis: string;
  mode: "dark" | "light";
  hue: number;
  chroma: number;
  hue2: number;
  density: "loose" | "normal" | "tight";
  motif: string;
}

/** 一条美感检测发现（来自 impeccable 引擎，已按幻灯片适用性策展）。 */
export interface AestheticFinding {
  rule: string;
  name: string;
  detail: string;
  selector?: string;
  /** 0 起的页号；-1 表示没能定位到具体页 */
  slide: number;
  severity: "error" | "warn";
}

export interface AestheticReport {
  before: { total: number; errors: number; warnings: number; by_rule: Record<string, number>; skipped: string };
  after: { total: number; errors: number; warnings: number; by_rule: Record<string, number>; skipped: string } | null;
  applied: {
    global: boolean;
    pages: number[];
    deferred: Array<{ index?: number | null; rule?: string; reason?: string }>;
    rejected: string[];
  };
  /** 本轮由哪个已安装技能驱动（名称/版本/命令）；技能缺失时是 fallback */
  skill?: { name: string; slug?: string; version?: string; command?: string; files?: string[]; chars?: number };
  /** 逐页 findings 全景（after 为准，没有 after 时是 before） */
  findings?: AestheticFinding[];
}

export type SourceKind = "web" | "kb";

export interface SourceRef {
  id: string;
  kind: SourceKind;
  title: string;
  url: string;
  kb_name: string;
  snippet: string;
}

export type PageLayout =
  | "cover"
  | "section"
  | "bullets"
  | "two_col"
  | "quote"
  | "table"
  | "stat"
  | "metric"
  | "chart"
  | "cards"
  | "timeline"
  | "end"
  | "appendix"
  | "auto";

export interface OutlinePage {
  id: string;
  title: string;
  kicker: string;
  layout: PageLayout;
  bullets: string[];
  note: string;
  sources: SourceRef[];
  layout_resolved: string;
}

export interface OutlineSection {
  id: string;
  title: string;
  pages: OutlinePage[];
}

/** 设计台旋钮：与后端 outline.StyleSpec / htmlkit.tokens.Knobs 一一对应。 */
export interface DesignKnobs {
  density: "loose" | "normal" | "dense";
  type_scale: number;
  radius: number;
  whitespace: number;
  accent: string;
  graphic: "auto" | "flat" | "gradient" | "outline";
  motif: boolean;
  footer: boolean;
  seed: number;
}

export interface OutlineStyle extends DesignKnobs {
  theme_pack_id: string;
  preset_theme: string;
  tone: string;
}

export interface Outline {
  title: string;
  subtitle: string;
  audience: string;
  lang: string;
  page_count: number;
  style: OutlineStyle;
  sources_policy: "page" | "appendix" | "both" | "off";
  version: number;
  sections: OutlineSection[];
  sources: SourceRef[];
}

// ---------------------------------------------------------------- 设计台

export interface DesignFamily {
  id: string;
  key: string;
  name: string;
  tagline: string;
  mode: "dark" | "light";
  motif: string;
  /** [bg, accent, accent2, paper]，都是 #RRGGBB */
  swatches: string[];
  chart: string[];
  /** template = 从上传的 .pptx 抽样出来的家族；builtin 的这三项是 16:9 / 无适配 */
  kind?: "builtin" | "template";
  ratio?: string;
  notes?: string[];
  fonts?: Partial<TemplateStyleReport["fonts"]>;
  /** 模板量出来的圆角与版心，选中时作为旋钮起点 */
  radius?: number;
  margin_px?: number;
}

export interface KnobRange {
  min: number;
  max: number;
  step?: number;
  default?: number;
}

export interface DesignCatalog {
  families: DesignFamily[];
  knobs: {
    density: string[];
    type_scale: KnobRange;
    radius: KnobRange;
    whitespace: KnobRange;
    graphic: string[];
    seed: KnobRange;
  };
  engine: { html: boolean; reason: string };
}

export interface LintIssue {
  rule: string;
  severity: "error" | "warn";
  detail: string;
  node_id: string;
}

export interface PageLintReport {
  index: number;
  layout: string;
  ok: boolean;
  fill: number;
  min_contrast: number;
  min_type: number;
  attempts: number;
  repairs: string[];
  issues: LintIssue[];
}

export interface LintSummary {
  pages: number;
  clean_pages: number;
  errors: number;
  warnings: number;
  rules: Record<string, number>;
  repairs: number;
  avg_fill: number;
  min_contrast: number;
}

export interface PreviewSlide {
  index: number;
  title: string;
  layout: string;
  chapter: string;
  image: string;
  report: PageLintReport;
}

export interface PreviewResult {
  available: boolean;
  reason?: string;
  slides: PreviewSlide[];
  total_slides?: number;
  layouts_used?: string[];
  lint?: LintSummary;
  tokens?: {
    key: string;
    mode: "dark" | "light";
    colors: Record<string, string>;
    chart: string[];
    knobs: DesignKnobs;
  };
}

export interface PptSession {
  id: string;
  title: string;
  project: string;
  expert_id: string;
  skill_ids: string[];
  kb_ids: string[];
  theme_pack_id: string;
  status: PptSessionStatus;
  clarify_count: number;
  page_count: number;
  total_slides: number;
  created_at: string;
  updated_at: string;
}

export interface DeckPayload {
  project: string;
  path: string;
  name: string;
  bytes: number;
  ticket: string;
  download_url: string;
}

export interface PptGeneration {
  generation_id: string;
  project: string;
  deck_path: string;
  total_slides: number;
  layouts_used: string[];
  fidelity: FidelityReport;
  status: string;
  error: string;
  created_at: string;
  /** 服务端缓存的逐页缩略图张数，0 = 没有（回落基础排版时） */
  thumb_count?: number;
  deck?: DeckPayload;
}

export interface PptMessage {
  id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    outline?: Outline;
    clarification?: { question: string; options: string[] };
    result?: PptGeneration;
  };
  created_at: string;
}

/** analyze 流的状态桶：reducer 纯函数处理，hook 只做订阅。 */
export interface AnalyzeStreamState {
  running: boolean;
  sessionId: string | null;
  phase: PptPhase | null;
  progress: number;
  phaseLabel: string;
  web: { status: string; query?: string; sources?: Array<{ title: string; url: string }> } | null;
  sources: SourceRef[];
  deltaText: string;
  outline: Outline | null;
  outlineMessageId: string | null;
  clarification: { question: string; options: string[] } | null;
  error: string | null;
  retryable: boolean;
}

export interface GenerateStreamState {
  running: boolean;
  phase: PptPhase | null;
  progress: number;
  phaseLabel: string;
  generation: PptGeneration | null;
  error: string | null;
  retryable: boolean;
}

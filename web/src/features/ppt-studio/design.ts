/**
 * 设计台旋钮的纯逻辑：默认值、取值域、clamp、随机、重置。
 *
 * 单独成模块是为了能脱离 DOM 单测——预览要真起 Chromium，一次约 1.5 秒，
 * 所以「旋钮改了什么」必须先在纯函数层锁死，UI 只负责把结果发出去。
 */

import type {
  DesignCatalog,
  DesignFamily,
  DesignKnobs,
  Outline,
  OutlineStyle,
  PageLayout,
} from "./types";

export const LAYOUT_LABEL: Record<PageLayout, string> = {
  cover: "封面",
  section: "章节页",
  bullets: "要点",
  two_col: "双栏",
  quote: "引用",
  table: "表格",
  stat: "数据",
  metric: "指标达成",
  chart: "图表",
  cards: "卡片",
  timeline: "时间轴",
  end: "结尾",
  auto: "自动",
  appendix: "附录",
};

export function layoutLabel(kind: string | undefined): string {
  return LAYOUT_LABEL[(kind ?? "") as PageLayout] ?? kind ?? "页面";
}

export const DEFAULT_KNOBS: DesignKnobs = {
  density: "normal",
  type_scale: 1,
  radius: 14,
  whitespace: 1,
  accent: "",
  graphic: "auto",
  motif: true,
  footer: true,
  seed: 0,
};

/** 后端不可达时的兜底取值域，与 GET /ppt/designs 保持一致。 */
export const FALLBACK_RANGES: DesignCatalog["knobs"] = {
  density: ["loose", "normal", "dense"],
  type_scale: { min: 0.88, max: 1.14, step: 0.02, default: 1 },
  radius: { min: 0, max: 28, step: 1, default: 14 },
  whitespace: { min: 0.8, max: 1.25, step: 0.05, default: 1 },
  graphic: ["auto", "flat", "gradient", "outline"],
  seed: { min: 0, max: 9999 },
};

export const DENSITY_LABEL: Record<DesignKnobs["density"], string> = {
  loose: "疏朗",
  normal: "标准",
  dense: "紧凑",
};

export const GRAPHIC_LABEL: Record<DesignKnobs["graphic"], string> = {
  auto: "自动",
  flat: "纯色",
  gradient: "渐变",
  outline: "描边",
};

/** 强调色候选：都取自六个家族的图表色，保证与任何家族都不打架。 */
export const ACCENT_PRESETS = [
  "#4F6BF6",
  "#22B8CF",
  "#1F9D6B",
  "#E8A03C",
  "#E11D63",
  "#8B5CF6",
];

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/** 后端会 clamp，但前端也要 clamp——否则滑块显示的值和成稿不一致。 */
export function normalizeKnobs(
  style: Partial<OutlineStyle> | undefined,
  ranges: DesignCatalog["knobs"] = FALLBACK_RANGES,
): DesignKnobs {
  const src = style ?? {};
  const density = String(src.density ?? DEFAULT_KNOBS.density);
  const graphic = String(src.graphic ?? DEFAULT_KNOBS.graphic);
  return {
    density: (ranges.density.includes(density) ? density : "normal") as DesignKnobs["density"],
    type_scale: round(
      clamp(Number(src.type_scale ?? 1), ranges.type_scale.min, ranges.type_scale.max),
      2,
    ),
    radius: Math.round(clamp(Number(src.radius ?? 14), ranges.radius.min, ranges.radius.max)),
    whitespace: round(
      clamp(Number(src.whitespace ?? 1), ranges.whitespace.min, ranges.whitespace.max),
      2,
    ),
    accent: /^#[0-9a-fA-F]{6}$/.test(String(src.accent ?? ""))
      ? String(src.accent).toUpperCase()
      : "",
    graphic: (ranges.graphic.includes(graphic) ? graphic : "auto") as DesignKnobs["graphic"],
    motif: src.motif !== false,
    footer: src.footer !== false,
    seed: Math.round(clamp(Number(src.seed ?? 0), ranges.seed.min, ranges.seed.max)),
  };
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/** 旋钮 → 新的 outline（只动 style，其余引用保持不变，避免整卡重渲染）。 */
export function withKnobs(outline: Outline, patch: Partial<DesignKnobs>): Outline {
  return { ...outline, style: { ...outline.style, ...patch } };
}

/** 换家族。patch 用来带上模板量出来的圆角等起点值，用户之后仍可继续拧。 */
export function withFamily(
  outline: Outline,
  familyId: string,
  patch: Partial<DesignKnobs> = {},
): Outline {
  return {
    ...outline,
    style: { ...outline.style, theme_pack_id: familyId, preset_theme: "", ...patch },
  };
}

/** 选中模板家族时的旋钮起点：量出来的圆角就是它的观感，别被默认 14px 盖掉。 */
export function familySeed(
  family: DesignFamily | undefined,
  ranges: DesignCatalog["knobs"] = FALLBACK_RANGES,
): Partial<DesignKnobs> {
  if (family?.kind !== "template" || typeof family.radius !== "number") return {};
  return {
    radius: Math.round(clamp(family.radius, ranges.radius.min, ranges.radius.max)),
  };
}

/** 后端色板一律是裸 hex（"0B1223"），直接塞进 style.background 会被当成非法颜色丢掉。 */
export function cssHex(value: string | undefined, fallback = "#000000"): string {
  const raw = (value ?? "").trim();
  if (/^#?[0-9a-fA-F]{3}$/.test(raw) || /^#?[0-9a-fA-F]{6}$/.test(raw)) {
    return raw.startsWith("#") ? raw : `#${raw}`;
  }
  return raw || fallback;
}

export interface RandomSource {
  (): number;
}

/** 「随机一版」：只动构图与观感，绝不动内容，也绝不出界。 */
export function randomizeKnobs(
  current: DesignKnobs,
  families: string[],
  ranges: DesignCatalog["knobs"] = FALLBACK_RANGES,
  random: RandomSource = Math.random,
): { knobs: DesignKnobs; familyId: string } {
  const pick = <T,>(list: T[]): T => list[Math.floor(random() * list.length) % list.length] as T;
  const step = (range: { min: number; max: number; step?: number }, digits: number) => {
    const unit = range.step ?? (range.max - range.min) / 10;
    const steps = Math.floor((range.max - range.min) / unit);
    return round(range.min + Math.floor(random() * (steps + 1)) * unit, digits);
  };
  return {
    familyId: families.length ? pick(families) : "builtin:midnight",
    knobs: {
      density: pick(ranges.density) as DesignKnobs["density"],
      type_scale: step(ranges.type_scale, 2),
      radius: Math.round(step(ranges.radius, 0)),
      whitespace: step(ranges.whitespace, 2),
      // 一半概率跟随家族色，避免每次随机都被自定义色盖掉家族气质
      accent: random() < 0.5 ? "" : pick(ACCENT_PRESETS),
      graphic: pick(ranges.graphic) as DesignKnobs["graphic"],
      motif: random() < 0.75,
      footer: current.footer,
      seed: Math.floor(random() * (ranges.seed.max + 1)),
    },
  };
}

/** 当前旋钮相对默认值改了几项——「恢复默认」按钮的禁用态与角标都看它。 */
export function changedKnobCount(knobs: DesignKnobs): number {
  return (Object.keys(DEFAULT_KNOBS) as Array<keyof DesignKnobs>).filter(
    (key) => knobs[key] !== DEFAULT_KNOBS[key],
  ).length;
}
import type { AlertKind, AlertRule } from "./types";

const KIND_LABELS: Record<string, string> = {
  chat: "对话",
  agent: "智能体",
  agent_pro: "智能体 Pro",
  v1_chat: "开放 API 对话",
  v1_agent: "开放 API 智能体",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

/** 12.3k / 4.1m，整数不带小数点。 */
export function compact(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1_000_000) return `${trimZero(n / 1_000_000)}m`;
  if (n >= 1000) return `${trimZero(n / 1000)}k`;
  return String(Math.round(n));
}

function trimZero(n: number): string {
  const text = n.toFixed(1);
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

export function money(n: number): string {
  return `¥${(Number.isFinite(n) ? n : 0).toFixed(2)}`;
}

export function percent(x: number): string {
  if (!Number.isFinite(x) || x <= 0) return "0%";
  const value = x * 100;
  return `${value < 1 ? value.toFixed(1) : Math.round(value)}%`;
}

/** ISO 时间 → 2026-11-09 14:03，跨时区细节不在这里处理。 */
export function timeText(iso: string | null): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

export function thresholdText(n: number): string {
  return String(Number(n.toFixed(2)));
}

/** 「错误率 ≥ 20%」「每日花费 ≥ ¥50」「不可用模型数 ≥ 1」。unit 决定量纲。 */
export function conditionText(rule: AlertRule, kinds: AlertKind[]): string {
  const meta = kinds.find((k) => k.kind === rule.kind);
  const label = rule.kind_label || meta?.label || rule.kind;
  const unit = meta?.unit ?? "";
  const value = thresholdText(rule.threshold);
  if (unit === "比例") return `${label} ≥ ${percent(rule.threshold)}`;
  if (unit === "元") return `${label} ≥ ¥${value}`;
  return `${label} ≥ ${value}`;
}

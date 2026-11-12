/**
 * 设计台：常驻可见的排版控制台 + 浏览器实测预览。
 *
 * 为什么常驻而不是收进设置弹窗：用户判断「这份 PPT 好不好看」靠的是眼睛，
 * 旋钮和成稿必须同屏。每次改动 debounce 后调 /preview，后端真起 Chromium
 * 量一遍 DOM，返回的就是最终写进 pptx 的那一版排版——不是近似示意图。
 *
 * 上传的公司模板也在家族列表里（后端把它的配色/字体/比例抽成一支家族），
 * 所以模板同样能拧旋钮，并把它量出来的圆角、字体替换与适配说明摊开给用户看。
 *
 * 浏览器不可用时降级为色板预览，并明确告知成稿走哪条排版路径，不假装实时。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Building2,
  Check,
  Copy,
  Dices,
  Layers,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "../api";
import {
  ACCENT_PRESETS,
  DEFAULT_KNOBS,
  DENSITY_LABEL,
  FALLBACK_RANGES,
  GRAPHIC_LABEL,
  changedKnobCount,
  cssHex,
  familySeed,
  normalizeKnobs,
  randomizeKnobs,
  withFamily,
  withKnobs,
} from "../design";
import type {
  DesignCatalog,
  DesignFamily,
  DesignKnobs,
  Outline,
  PreviewResult,
} from "../types";
import { SlidePreviewGrid, type PreviewTile } from "./SlidePreviewGrid";

const DEBOUNCE_MS = 650;
const FIRST_PAINT_MS = 80;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline gap-1.5">
        <span className="text-[11.5px] font-medium text-secondary">{label}</span>
        {hint && <span className="ml-auto font-mono text-[10.5px] text-tertiary">{hint}</span>}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function Slider({
  value,
  min,
  max,
  step,
  disabled,
  ariaLabel,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  ariaLabel: string;
  onChange: (value: number) => void;
}) {
  const fill = ((value - min) / Math.max(1e-6, max - min)) * 100;
  return (
    <input
      type="range"
      className="ppt-range"
      aria-label={ariaLabel}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      style={{ ["--ppt-fill" as string]: `${fill}%` }}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  );
}

function Segmented<T extends string>({
  value,
  options,
  disabled,
  ariaLabel,
  onChange,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  disabled?: boolean;
  ariaLabel: string;
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex overflow-hidden rounded-lg border border-line-control bg-surface-2"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          disabled={disabled}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "min-w-0 flex-1 truncate px-1.5 py-1 text-[11.5px] transition-colors duration-150 disabled:opacity-50",
            value === option.value
              ? "bg-brand text-brand-fg"
              : "text-secondary hover:bg-hover hover:text-primary",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({
  checked,
  label,
  disabled,
  onChange,
}: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex w-full items-center gap-2 rounded-lg border px-2 py-1 text-[11.5px] transition-colors duration-150 disabled:opacity-50",
        checked
          ? "border-brand/45 bg-brand-soft text-brand-ink"
          : "border-line-control bg-surface-2 text-tertiary hover:text-secondary",
      )}
    >
      <span
        className={cn(
          "relative h-3.5 w-6 shrink-0 rounded-full transition-colors duration-150",
          checked ? "bg-brand" : "bg-line-strong",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-surface-1 transition-all duration-150",
            checked ? "left-3" : "left-0.5",
          )}
        />
      </span>
      <span className="truncate">{label}</span>
    </button>
  );
}

function FamilyChip({
  item,
  active,
  accent,
  disabled,
  onSelect,
}: {
  item: DesignFamily;
  active: boolean;
  accent: string;
  disabled?: boolean;
  onSelect: () => void;
}) {
  const template = item.kind === "template";
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      onClick={onSelect}
      title={item.tagline}
      className={cn(
        "group flex items-center gap-1.5 rounded-lg border p-1 text-left transition-all duration-150 disabled:opacity-50",
        active
          ? "border-brand/55 bg-brand-soft"
          : "border-line-control bg-surface-1 hover:border-brand/35 hover:bg-hover",
      )}
    >
      <span
        className="relative block h-6 w-9 shrink-0 overflow-hidden rounded"
        style={{ background: cssHex(item.swatches[0], "#0B1223") }}
        aria-hidden
      >
        <span
          className="absolute -right-1.5 top-1/2 block h-6 w-6 -translate-y-1/2 rotate-45"
          style={{
            // 未选中的家族要显示它自己的强调色，否则六个色卡长一个样，没法挑
            background: cssHex(active ? accent || item.swatches[1] : item.swatches[1], "#4F6BF6"),
          }}
        />
        <span
          className="absolute bottom-0 left-0 block h-1/3 w-full"
          style={{ background: cssHex(item.swatches[2], "#FFFFFF"), opacity: 0.55 }}
        />
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1">
        {template && <Building2 size={10} className="shrink-0 text-tertiary" />}
        <span className="min-w-0 truncate text-[11px] text-primary">{item.name}</span>
        {template && item.ratio && (
          <span className="shrink-0 rounded bg-surface-3 px-1 font-mono text-[9.5px] leading-4 text-tertiary">
            {item.ratio}
          </span>
        )}
      </span>
      {active && <Check size={10} className="shrink-0 text-brand-ink" />}
    </button>
  );
}

/** 模板适配说明：字体替换、画布归一这些「和原模板不完全一样」的地方必须写在明面上。 */
function TemplateBrief({ family }: { family: DesignFamily }) {
  const fonts = family.fonts ?? {};
  const notes = family.notes ?? [];
  return (
    <div className="mt-1.5 rounded-lg border border-line bg-surface-1 p-2">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10.5px] text-tertiary">
        <span className="flex items-center gap-1 text-secondary">
          <Type size={10} />
          {fonts.body || "字体未识别"}
        </span>
        {fonts.display && fonts.display !== fonts.body && <span>标题 {fonts.display}</span>}
        {family.ratio && <span className="font-mono">画布 {family.ratio}</span>}
        {typeof family.margin_px === "number" && (
          <span className="font-mono">版心边距 {family.margin_px}px</span>
        )}
        <span className={notes.length ? "text-warning" : "text-success"}>
          {notes.length ? `${notes.length} 处适配` : "与原模板一致"}
        </span>
      </div>
      {notes.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {notes.map((note) => (
            <li key={note} className="flex items-start gap-1.5 text-[10.5px] leading-4 text-warning">
              <AlertTriangle size={10} className="mt-0.5 shrink-0" />
              <span className="min-w-0 flex-1">{note}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Props {
  sessionId: string | null;
  outline: Outline;
  onChange: (outline: Outline) => void;
  catalog: DesignCatalog | null;
  disabled?: boolean;
}

export function DesignConsole({ sessionId, outline, onChange, catalog, disabled }: Props) {
  const ranges = catalog?.knobs ?? FALLBACK_RANGES;
  const families = useMemo(() => catalog?.families ?? [], [catalog]);
  const templates = useMemo(() => families.filter((f) => f.kind === "template"), [families]);
  const builtins = useMemo(() => families.filter((f) => f.kind !== "template"), [families]);
  const knobs = useMemo(() => normalizeKnobs(outline.style, ranges), [outline.style, ranges]);
  // 空风格 = 后端解析成默认内置风格；families[0] 现在可能是上传模板，不能拿它当默认
  const familyId =
    outline.style.theme_pack_id ||
    builtins[0]?.id ||
    "builtin:midnight";
  const family = families.find((f) => f.id === familyId);
  const isTemplate = family?.kind === "template";
  const engineReady = catalog?.engine.html ?? false;

  const [limit, setLimit] = useState(6);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [tick, setTick] = useState(0);
  const firstPaint = useRef(true);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const signature = useMemo(() => JSON.stringify(outline), [outline]);

  useEffect(() => {
    if (!sessionId || !engineReady) return;
    const controller = new AbortController();
    const delay = firstPaint.current ? FIRST_PAINT_MS : DEBOUNCE_MS;
    firstPaint.current = false;
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .previewDesign(sessionId, outline, limit, controller.signal)
        .then((result) => {
          setPreview(result);
          setError(result.available ? null : (result.reason ?? "实测排版不可用"));
        })
        .catch((err: Error) => {
          if (err.name === "AbortError") return;
          setError(err.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // signature 是 outline 的内容指纹：outline 每次编辑都是新对象，但内容没变就不重测
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, sessionId, engineReady, limit, tick]);

  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);

  const patch = useCallback(
    (next: Partial<DesignKnobs>) => onChange(withKnobs(outline, next)),
    [onChange, outline],
  );

  const tiles: PreviewTile[] = useMemo(
    () =>
      (preview?.slides ?? []).map((slide) => ({
        key: `${slide.index}-${slide.image.length}`,
        src: slide.image,
        index: slide.index,
        title: slide.title,
        layout: slide.layout,
        chapter: slide.chapter,
        report: slide.report,
      })),
    [preview],
  );

  const palette = preview?.tokens?.colors;
  const accentNow = cssHex(knobs.accent || palette?.accent || family?.swatches[1], "#4F6BF6");
  const chartHues = preview?.tokens?.chart ?? family?.chart ?? [];
  const summary = preview?.lint;
  const changed = changedKnobCount(knobs);

  const randomize = () => {
    // 选中公司模板时不能摇走：用户要的是「我的模板，换一种排布」
    const next = randomizeKnobs(knobs, builtins.map((f) => f.id), ranges);
    onChange(withFamily(outline, isTemplate ? familyId : next.familyId, next.knobs));
  };

  const copyStyle = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(outline.style, null, 2));
      setCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("当前浏览器不允许写入剪贴板，可手动记录旋钮数值");
    }
  };

  return (
    <section
      aria-label="设计台"
      className="rounded-xl border border-line bg-surface-2/70 p-3"
      style={{ ["--ppt-accent" as string]: accentNow }}
    >
      {/* 头部：一眼看到「这是实测预览」+ 全局动作 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-primary">
          <Layers size={13} style={{ color: accentNow }} />
          设计台
        </span>
        <span
          className={cn(
            "flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px]",
            engineReady ? "bg-success/10 text-success" : "bg-warning/10 text-warning",
          )}
          title={engineReady ? "浏览器实测排版已启用" : (catalog?.engine.reason || "浏览器实测排版不可用")}
        >
          {engineReady ? <Check size={9} /> : <AlertTriangle size={9} />}
          {engineReady ? "实测排版" : "基础排版"}
        </span>
        {isTemplate && (
          <span
            className="flex max-w-[220px] items-center gap-1 rounded bg-brand-soft px-1.5 py-0.5 text-[10.5px] text-brand-ink"
            title={`按上传模板「${family?.name}」的风格重绘`}
          >
            <Building2 size={9} className="shrink-0" />
            <span className="truncate">{family?.name}</span>
            {family?.ratio && <span className="shrink-0 font-mono">{family.ratio}</span>}
          </span>
        )}
        {loading && (
          <span className="flex items-center gap-1 text-[11px] text-tertiary">
            <Loader2 size={11} className="animate-spin" /> 正在实测…
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            disabled={disabled}
            onClick={randomize}
            title="随机换一版设计（只动观感，不动内容）"
            className="flex items-center gap-1 rounded-lg border border-line-control bg-surface-1 px-2 py-1 text-[11.5px] text-secondary transition-colors hover:border-brand/45 hover:text-brand-ink disabled:opacity-50"
          >
            <Dices size={12} /> 随机一版
          </button>
          <button
            type="button"
            disabled={disabled || changed === 0}
            onClick={() => patch(DEFAULT_KNOBS)}
            title={changed ? `恢复 ${changed} 项旋钮的默认值` : "旋钮已是默认值"}
            className="flex items-center gap-1 rounded-lg border border-line-control bg-surface-1 px-2 py-1 text-[11.5px] text-secondary transition-colors hover:border-brand/45 hover:text-primary disabled:opacity-40"
          >
            <RotateCcw size={12} /> 重置{changed > 0 && ` ${changed}`}
          </button>
          <button
            type="button"
            onClick={() => void copyStyle()}
            title="复制当前风格配置（JSON），可粘贴给同事复用"
            className="flex items-center gap-1 rounded-lg border border-line-control bg-surface-1 px-2 py-1 text-[11.5px] text-secondary transition-colors hover:border-brand/45 hover:text-primary"
          >
            {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
            {copied ? "已复制" : "复制风格"}
          </button>
        </div>
      </div>

      {/* 预览：主体价值放最前面 */}
      <div className="mt-2.5">
        {engineReady ? (
          <>
            <SlidePreviewGrid tiles={tiles} loading={loading} skeleton={Math.min(limit, 6)} />
            {tiles.length === 0 && !loading && (
              <p className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-[11.5px] text-tertiary">
                还没有预览。填写标题后这里会出现浏览器实测的真实页面。
              </p>
            )}
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-line bg-surface-1 p-3">
            <div className="flex h-16 overflow-hidden rounded-md">
              {(family?.swatches ?? ["#0B1223", "#4F6BF6", "#22B8CF", "#FFFFFF"]).map((hex) => (
                <span key={hex} className="flex-1" style={{ background: cssHex(hex) }} />
              ))}
            </div>
            <p className="mt-2 text-[11.5px] leading-5 text-tertiary">
              当前环境未启用浏览器实测排版（{catalog?.engine.reason || "未知原因"}），
              {isTemplate
                ? "成稿回落到模板母版套版：配色与画布比例仍是模板原样，但旋钮不再生效。"
                : "成稿走基础形状排版，只有「信息密度」会生效。"}
            </p>
          </div>
        )}
      </div>

      {/* 体检摘要 + 色板：旋钮改动的第一反馈，比预览回来得快 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="flex items-center gap-1">
          <span
            className="h-4 w-4 rounded border border-line"
            style={{ background: accentNow }}
            title={`强调色 ${accentNow}`}
          />
          {chartHues.slice(0, 7).map((hue, i) => (
            <span
              key={`${hue}-${i}`}
              className="h-4 w-2.5 rounded-sm"
              style={{ background: cssHex(hue) }}
              title={`图表色 ${i + 1}`}
            />
          ))}
        </div>
        {summary && (
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10.5px] text-tertiary">
            <span className={summary.errors ? "text-danger" : "text-success"}>
              体检 {summary.clean_pages}/{summary.pages} 页通过
            </span>
            {summary.warnings > 0 && <span className="text-warning">{summary.warnings} 条提示</span>}
            {summary.repairs > 0 && <span>{summary.repairs} 处自动修复</span>}
            <span>最低对比度 {summary.min_contrast.toFixed(1)}:1</span>
          </span>
        )}
        {preview?.total_slides ? (
          <span className="ml-auto text-[10.5px] text-tertiary">
            抽样 {tiles.length} / 全 {preview.total_slides} 页
          </span>
        ) : null}
      </div>

      {error && (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-warning/10 px-2 py-1.5 text-[11px] leading-5 text-warning">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
          {engineReady && sessionId && (
            <button
              type="button"
              onClick={() => setTick((v) => v + 1)}
              className="shrink-0 underline-offset-2 hover:underline"
            >
              重试
            </button>
          )}
        </p>
      )}

      {/* 家族选择：色卡即预览。公司模板排最前——用户传了模板就是冲着它来的 */}
      <div className="mt-3">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[11.5px] font-medium text-secondary">设计家族</span>
          {family && <span className="truncate text-[10.5px] text-tertiary">{family.tagline}</span>}
        </div>

        {templates.length > 0 && (
          <>
            <p className="mt-1.5 text-[10.5px] text-tertiary">公司模板（已抽取风格，可继续拧旋钮）</p>
            <div className="mt-1 grid grid-cols-2 gap-1.5">
              {templates.map((item) => (
                <FamilyChip
                  key={item.id}
                  item={item}
                  active={item.id === familyId}
                  accent={knobs.accent}
                  disabled={disabled}
                  onSelect={() => onChange(withFamily(outline, item.id, familySeed(item, ranges)))}
                />
              ))}
            </div>
            {isTemplate && family && <TemplateBrief family={family} />}
            <p className="mt-2 text-[10.5px] text-tertiary">内置风格</p>
          </>
        )}

        <div className="mt-1 grid grid-cols-3 gap-1.5">
          {builtins.map((item) => (
            <FamilyChip
              key={item.id}
              item={item}
              active={item.id === familyId}
              accent={knobs.accent}
              disabled={disabled}
              onSelect={() => onChange(withFamily(outline, item.id))}
            />
          ))}
        </div>
      </div>

      {/* 旋钮区：常驻展开，绝不收进弹窗 */}
      <div className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2.5 sm:grid-cols-2">
        <Field label="信息密度">
          <Segmented
            ariaLabel="信息密度"
            value={knobs.density}
            disabled={disabled}
            options={(ranges.density as DesignKnobs["density"][]).map((value) => ({
              value,
              label: DENSITY_LABEL[value] ?? value,
            }))}
            onChange={(density) => patch({ density })}
          />
        </Field>

        <Field label="图形风格">
          <Segmented
            ariaLabel="图形风格"
            value={knobs.graphic}
            disabled={disabled}
            options={(ranges.graphic as DesignKnobs["graphic"][]).map((value) => ({
              value,
              label: GRAPHIC_LABEL[value] ?? value,
            }))}
            onChange={(graphic) => patch({ graphic })}
          />
        </Field>

        <Field label="字号倍率" hint={`${knobs.type_scale.toFixed(2)}×`}>
          <Slider
            ariaLabel="字号倍率"
            value={knobs.type_scale}
            min={ranges.type_scale.min}
            max={ranges.type_scale.max}
            step={ranges.type_scale.step ?? 0.02}
            disabled={disabled}
            onChange={(type_scale) => patch({ type_scale })}
          />
        </Field>

        <Field label="留白倍率" hint={`${knobs.whitespace.toFixed(2)}×`}>
          <Slider
            ariaLabel="留白倍率"
            value={knobs.whitespace}
            min={ranges.whitespace.min}
            max={ranges.whitespace.max}
            step={ranges.whitespace.step ?? 0.05}
            disabled={disabled}
            onChange={(whitespace) => patch({ whitespace })}
          />
        </Field>

        <Field label="卡片圆角" hint={`${knobs.radius}px`}>
          <Slider
            ariaLabel="卡片圆角"
            value={knobs.radius}
            min={ranges.radius.min}
            max={ranges.radius.max}
            step={ranges.radius.step ?? 1}
            disabled={disabled}
            onChange={(radius) => patch({ radius })}
          />
        </Field>

        <Field label="构图变体" hint={`#${knobs.seed}`}>
          <div role="group" aria-label="构图变体" className="flex items-center gap-1">
            <button
              type="button"
              disabled={disabled || knobs.seed <= ranges.seed.min}
              aria-label="上一个构图变体"
              onClick={() => patch({ seed: Math.max(ranges.seed.min, knobs.seed - 1) })}
              className="rounded-md border border-line-control bg-surface-1 p-1 text-secondary transition-colors hover:border-brand/45 disabled:opacity-40"
            >
              <Minus size={11} />
            </button>
            <button
              type="button"
              disabled={disabled || knobs.seed >= ranges.seed.max}
              aria-label="下一个构图变体"
              onClick={() => patch({ seed: Math.min(ranges.seed.max, knobs.seed + 1) })}
              className="rounded-md border border-line-control bg-surface-1 p-1 text-secondary transition-colors hover:border-brand/45 disabled:opacity-40"
            >
              <Plus size={11} />
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => patch({ seed: Math.floor(Math.random() * (ranges.seed.max + 1)) })}
              className="flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md border border-line-control bg-surface-1 px-1.5 py-1 text-[11px] text-secondary transition-colors hover:border-brand/45 hover:text-brand-ink disabled:opacity-50"
            >
              <Sparkles size={10} /> 换一种排布
            </button>
          </div>
        </Field>

        <Field label="强调色" hint={knobs.accent || "跟随家族"}>
          <div role="group" aria-label="强调色" className="flex items-center gap-1">
            <label
              className="relative h-6 w-6 shrink-0 cursor-pointer overflow-hidden rounded-md border border-line-control"
              style={{ background: accentNow }}
              title="自定义强调色"
            >
              <input
                type="color"
                aria-label="自定义强调色"
                disabled={disabled}
                value={/^#[0-9A-Fa-f]{6}$/.test(accentNow) ? accentNow : "#4F6BF6"}
                onChange={(e) => patch({ accent: e.target.value.toUpperCase() })}
                className="absolute inset-0 cursor-pointer opacity-0"
              />
            </label>
            {ACCENT_PRESETS.map((hex) => (
              <button
                key={hex}
                type="button"
                disabled={disabled}
                aria-label={`强调色 ${hex}`}
                aria-pressed={knobs.accent === hex}
                onClick={() => patch({ accent: hex })}
                className={cn(
                  "h-5 w-5 shrink-0 rounded-full border transition-transform duration-150 hover:scale-110 disabled:opacity-50",
                  knobs.accent === hex ? "border-primary" : "border-line",
                )}
                style={{ background: hex }}
              />
            ))}
            <button
              type="button"
              disabled={disabled || !knobs.accent}
              onClick={() => patch({ accent: "" })}
              title="跟随家族强调色"
              className="ml-auto shrink-0 rounded-md border border-line-control bg-surface-1 px-1.5 py-0.5 text-[10.5px] text-tertiary transition-colors hover:text-primary disabled:opacity-40"
            >
              跟随
            </button>
          </div>
        </Field>

        <Field label="预览页数" hint={`${limit} 页`}>
          <Segmented
            ariaLabel="预览页数"
            value={String(limit)}
            options={[
              { value: "3", label: "3 页" },
              { value: "6", label: "6 页" },
              { value: "9", label: "9 页" },
            ]}
            onChange={(value) => setLimit(Number(value))}
          />
        </Field>

        <Field label="装饰与页脚">
          <div className="flex gap-1.5">
            <Toggle
              label="母题装饰"
              checked={knobs.motif}
              disabled={disabled}
              onChange={(motif) => patch({ motif })}
            />
            <Toggle
              label="页脚页码"
              checked={knobs.footer}
              disabled={disabled}
              onChange={(footer) => patch({ footer })}
            />
          </div>
        </Field>

        <Field label="实测预览">
          <button
            type="button"
            disabled={disabled || !engineReady || !sessionId || loading}
            onClick={() => setTick((v) => v + 1)}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-line-control bg-surface-1 px-2 py-1 text-[11.5px] text-secondary transition-colors hover:border-brand/45 hover:text-brand-ink disabled:opacity-40"
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            {loading ? "实测中…" : "重新实测当前设计"}
          </button>
        </Field>
      </div>
    </section>
  );
}
/**
 * PPT 可视化编辑器。
 *
 * 数据是「结构化 deck」而不是二进制：每次保存都由服务端按 deck 重新排版
 * 生成 pptx，所以界面上看到的就是最终版式，不会出现文本框错位。
 * 外部导入的 pptx 首次保存会被归一成本产品主题，接口用 editable 标明，
 * 这里要如实提示。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Download,
  Loader2,
  Plus,
  Redo2,
  Save,
  SlidersHorizontal,
  Trash2,
  Undo2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SlideCanvas } from "./SlideCanvas";
import * as api from "../api";
import {
  LAYOUT_LABELS,
  blankSlide,
  type Deck,
  type DeckSlide,
  type SlideLayout,
  type ThemePreset,
} from "../types";

const LAYOUTS: SlideLayout[] = [
  "title",
  "section",
  "bullets",
  "stat",
  "chart",
  "timeline",
  "grid",
  "vs",
  "two_col",
  "quote",
  "table",
  "end",
];

const FALLBACK_THEME: ThemePreset = {
  id: "midnight",
  name: "午夜蓝",
  bg: "0F172A",
  surface: "1E293B",
  accent: "6366F1",
  accent2: "22D3EE",
  on_dark: "F8FAFC",
  on_dark_muted: "94A3B8",
  ink: "0F172A",
  ink_muted: "64748B",
  paper: "FFFFFF",
};

interface DeckEditorProps {
  project: string;
  path: string;
  onClose: () => void;
  /** 面板模式（半屏宽）：缩略图改顶部胶片条，属性面板改成可切换抽屉 */
  compact?: boolean;
}

export function DeckEditor({ project, path, onClose, compact = false }: DeckEditorProps) {
  const [deck, setDeck] = useState<Deck | null>(null);
  const [themes, setThemes] = useState<ThemePreset[]>([FALLBACK_THEME]);
  const [selected, setSelected] = useState(0);
  /** 设计稿模式：展示 PowerPoint 真实渲染图（还原渐变/圆角等），否则展示可编辑大纲 */
  const [designMode, setDesignMode] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [propsOpen, setPropsOpen] = useState(false);

  const past = useRef<Deck[]>([]);
  const future = useRef<Deck[]>([]);
  const deckRef = useRef<Deck | null>(null);
  const [, setHistoryTick] = useState(0);

  const apply = useCallback((next: Deck | null) => {
    deckRef.current = next;
    setDeck(next);
    setHistoryTick((n) => n + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    apply(null);
    setError("");
    past.current = [];
    future.current = [];
    api
      .readDeck(project, path)
      .then((data) => {
        if (!alive) return;
        apply(data);
        setSelected(0);
        setDirty(false);
      })
      .catch((exc: Error) => alive && setError(exc.message));
    api
      .listThemes()
      .then((list) => alive && list.length > 0 && setThemes(list))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [project, path, apply]);

  const theme = useMemo(
    () => themes.find((item) => item.id === deck?.theme) ?? themes[0] ?? FALLBACK_THEME,
    [themes, deck?.theme],
  );

  const slide: DeckSlide | null = deck ? (deck.slides[selected] ?? null) : null;
  const previews = deck?.preview_images ?? [];
  const hasDesign = previews.length > 0;
  // 外部生成的 pptx（slidep/导入）没有 sidecar，解析模型只是有损近似——
  // 与 WorkBuddy 一致：预览即真相，只展示真实渲染，不提供会「重排」的编辑
  const readOnlyDesign = deck?.source === "parsed" && hasDesign;
  const showDesign = readOnlyDesign || (hasDesign && designMode);
  // img 标签带不了 Authorization 头，设计稿图片靠 browse ticket 鉴权
  const [ticket, setTicket] = useState("");
  useEffect(() => {
    if (!hasDesign) return;
    let alive = true;
    api.getTicket().then((value) => {
      if (alive) setTicket(value);
    });
    return () => {
      alive = false;
    };
  }, [hasDesign]);

  // 票据 15 分钟过期：img 挂掉（401）时批量重签一次，页面停留超时后翻页不再裂图
  const refreshFails = useRef(0);
  const refreshTimer = useRef<number | null>(null);
  const onPreviewError = useCallback(() => {
    if (refreshTimer.current || refreshFails.current >= 2) return;
    refreshFails.current += 1;
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null;
      api.getTicket(true).then((v) => setTicket(v));
    }, 300);
  }, []);
  const onPreviewLoad = useCallback(() => {
    refreshFails.current = 0;
  }, []);

  /** 所有修改都走这里：入栈历史 → 标记未保存。 */
  const mutate = useCallback(
    (updater: (draft: Deck) => Deck) => {
      const current = deckRef.current;
      if (!current) return;
      const next = updater(current);
      if (next === current) return;
      past.current = [...past.current.slice(-49), current];
      future.current = [];
      apply(next);
      setDirty(true);
      setNotice("");
    },
    [apply],
  );

  const patchSlide = useCallback(
    (patch: Partial<DeckSlide>) => {
      mutate((current) => ({
        ...current,
        slides: current.slides.map((item, i) => (i === selected ? { ...item, ...patch } : item)),
      }));
    },
    [mutate, selected],
  );

  const undo = () => {
    const previous = past.current.pop();
    if (!previous) return;
    if (deckRef.current) future.current.push(deckRef.current);
    apply(previous);
    setDirty(true);
  };

  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    if (deckRef.current) past.current.push(deckRef.current);
    apply(next);
    setDirty(true);
  };

  const save = async () => {
    if (!deck || saving) return;
    setSaving(true);
    setError("");
    try {
      const fresh = await api.saveDeck(project, path, deck);
      apply(fresh);
      past.current = [];
      future.current = [];
      setDirty(false);
      setNotice(
        fresh.editable === "partial"
          ? "已保存：外部版式已按当前主题重排，下次可继续编辑"
          : "已保存",
      );
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
      if (meta && event.key.toLowerCase() === "z" && !event.shiftKey) {
        const target = event.target as HTMLElement;
        if (target.isContentEditable) return; // 输入框内交给浏览器原生撤销
        event.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (error && !deck) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-[13px]">
        <p className="text-danger">{error}</p>
        <button onClick={onClose} className="text-brand hover:underline">
          返回列表
        </button>
      </div>
    );
  }

  if (!deck || !slide) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[13px] text-tertiary">
        <Loader2 size={14} className="animate-spin" />
        正在解析演示文稿…
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 工具条 */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-line px-2.5 py-2">
        <button
          onClick={onClose}
          className="flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[12.5px] text-secondary hover:bg-surface-3 hover:text-primary"
          title="返回演示列表"
        >
          <ArrowLeft size={13} /> {!compact && "演示列表"}
        </button>
        <span className="h-4 w-px shrink-0 bg-line-strong" />
        <input
          value={deck.title}
          onChange={(event) => mutate((current) => ({ ...current, title: event.target.value }))}
          className="min-w-0 max-w-[280px] flex-1 rounded-md bg-transparent px-2 py-1 text-[13px] font-medium outline-none hover:bg-surface-3 focus:bg-surface-3"
          placeholder="演示标题"
        />
        {!compact && (
          <span className="hidden text-[11.5px] text-tertiary sm:inline">
            {deck.slides.length} 页 · {deck.path}
          </span>
        )}

        <div className="ml-auto flex shrink-0 items-center gap-1">
          {hasDesign && !readOnlyDesign && (
            <div
              className="flex shrink-0 items-center rounded-md border border-line bg-surface-1 text-[12px]"
              role="group"
              aria-label="预览模式切换"
              title="设计稿为 PowerPoint 真实渲染；可编辑按结构化大纲显示"
            >
              <button
                onClick={() => setDesignMode(true)}
                className={cn(
                  "rounded-l-md px-2 py-1",
                  designMode ? "bg-brand/10 font-medium text-brand" : "text-secondary hover:bg-surface-3",
                )}
              >
                设计稿
              </button>
              <button
                onClick={() => setDesignMode(false)}
                className={cn(
                  "rounded-r-md px-2 py-1",
                  !designMode ? "bg-brand/10 font-medium text-brand" : "text-secondary hover:bg-surface-3",
                )}
              >
                可编辑
              </button>
            </div>
          )}
          {compact && (
            <button
              onClick={() => setPropsOpen((prev) => !prev)}
              className={cn(
                "rounded-md p-1.5 hover:bg-surface-3",
                propsOpen ? "text-brand" : "text-secondary",
              )}
              title="页面属性"
              aria-label="页面属性"
            >
              <SlidersHorizontal size={14} />
            </button>
          )}
          {!readOnlyDesign && (
            <select
              value={deck.theme}
              aria-label="主题配色"
              onChange={(event) =>
                mutate((current) => ({ ...current, theme: event.target.value }))
              }
              className="rounded-md border border-line bg-surface-1 px-1.5 py-1 text-[12px] outline-none"
              title="主题配色"
            >
              {themes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          )}
          {!readOnlyDesign && (
            <button
              onClick={undo}
              disabled={past.current.length === 0}
              className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
              title="撤销 (Ctrl+Z)"
            >
              <Undo2 size={14} />
            </button>
          )}
          {!readOnlyDesign && (
            <button
              onClick={redo}
              disabled={future.current.length === 0}
              className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
              title="重做"
            >
              <Redo2 size={14} />
            </button>
          )}
          <a
            href={api.downloadUrl(project, path)}
            className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
            title="下载 pptx"
          >
            <Download size={14} />
          </a>
          {!readOnlyDesign && (
            <button
              onClick={save}
              disabled={saving || !dirty}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium transition-colors",
                dirty
                  ? "bg-brand text-brand-fg hover:opacity-90"
                  : "bg-surface-3 text-tertiary",
              )}
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              {saving ? "保存中" : dirty ? "保存" : "已保存"}
            </button>
          )}
        </div>
      </div>

      {(notice || error) && (
        <div
          className={cn(
            "shrink-0 border-b px-4 py-1.5 text-[11.5px]",
            error
              ? "border-danger/30 bg-danger/10 text-danger"
              : "border-line bg-surface-2 text-secondary",
          )}
        >
          {error || notice}
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        {/* 缩略图（宽屏走左栏） */}
        {!compact && (
          <div className="flex w-[132px] shrink-0 flex-col gap-2 overflow-y-auto border-r border-line bg-surface-2 p-2.5">
            {readOnlyDesign ? (
              deck.slides.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setSelected(i)}
                  className={cn(
                    "overflow-hidden rounded-md border transition-colors",
                    i === selected ? "border-brand" : "border-transparent hover:border-line-strong",
                  )}
                  title={`第 ${i + 1} 页`}
                >
                  {ticket ? (
                    <img
                      src={api.previewUrl(project, previews[i] ?? "", ticket)}
                      alt={`第 ${i + 1} 页缩略图`}
                      className="block w-full"
                      loading="lazy"
                      onError={onPreviewError}
                      onLoad={onPreviewLoad}
                    />
                  ) : (
                    <div className="aspect-video w-full animate-pulse bg-surface-3" />
                  )}
                </button>
              ))
            ) : (
              <>
                {deck.slides.map((item, i) => (
                  <Thumb
                    key={i}
                    slide={item}
                    theme={theme}
                    index={i}
                    deckTitle={deck.title}
                    author={deck.author}
                    active={i === selected}
                    onClick={() => setSelected(i)}
                  />
                ))}
                <AddSlideButton
                  onClick={() => {
                    mutate((current) => ({
                      ...current,
                      slides: [...current.slides, blankSlide("bullets")],
                    }));
                    setSelected(deck.slides.length);
                  }}
                />
              </>
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {/* 缩略图（面板模式走顶部胶片条） */}
          {compact && (
            <div className="flex shrink-0 items-start gap-2 overflow-x-auto border-b border-line bg-surface-2 px-2.5 py-2">
              {readOnlyDesign
                ? deck.slides.map((_, i) => (
                    <button
                      key={i}
                      onClick={() => setSelected(i)}
                      className={cn(
                        "w-[92px] shrink-0 overflow-hidden rounded-md border transition-colors",
                        i === selected ? "border-brand" : "border-transparent hover:border-line-strong",
                      )}
                      title={`第 ${i + 1} 页`}
                    >
                      {ticket ? (
                        <img
                          src={api.previewUrl(project, previews[i] ?? "", ticket)}
                          alt={`第 ${i + 1} 页缩略图`}
                          className="block w-full"
                          loading="lazy"
                          onError={onPreviewError}
                          onLoad={onPreviewLoad}
                        />
                      ) : (
                        <div className="aspect-video w-full animate-pulse bg-surface-3" />
                      )}
                    </button>
                  ))
                : deck.slides.map((item, i) => (
                    <Thumb
                      key={i}
                      slide={item}
                      theme={theme}
                      index={i}
                      deckTitle={deck.title}
                      author={deck.author}
                      active={i === selected}
                      onClick={() => setSelected(i)}
                      className="w-[92px] shrink-0"
                    />
                  ))}
              {!readOnlyDesign && (
                <AddSlideButton
                  compact
                  onClick={() => {
                    mutate((current) => ({
                      ...current,
                      slides: [...current.slides, blankSlide("bullets")],
                    }));
                    setSelected(deck.slides.length);
                  }}
                />
              )}
            </div>
          )}

          {/* 画布 */}
          <div className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-page">
            <div className="flex items-center justify-between gap-2 px-4 py-2 text-[11.5px] text-tertiary">
              <span className="min-w-0 truncate">
                第 {selected + 1} / {deck.slides.length} 页
                {!showDesign && LAYOUT_LABELS[slide.layout]}
                {showDesign && (
                  <span className="ml-2 rounded bg-brand/10 px-1.5 py-0.5 text-brand">
                    真实渲染
                  </span>
                )}
                {deck.editable === "partial" && !showDesign && (
                  <span className="ml-2 rounded bg-warning/10 px-1.5 py-0.5 text-warning">
                    外部版式，保存将重排
                  </span>
                )}
              </span>
              <span className="hidden shrink-0 lg:inline">
                {showDesign ? "预览即文件真实效果；要改内容请回智能体重新生成" : "点文字直接改，改完失焦生效"}
              </span>
            </div>
            <div className={cn("mx-auto w-full px-4 pb-6", compact ? "max-w-[680px]" : "max-w-[920px]")}>
              {showDesign && ticket ? (
                <>
                  <img
                    src={api.previewUrl(project, previews[selected] ?? previews[0] ?? "", ticket)}
                    alt={`第 ${selected + 1} 页设计稿`}
                    className="w-full rounded-lg border border-line"
                    onError={onPreviewError}
                    onLoad={onPreviewLoad}
                  />
                  <p className="mt-2 text-center text-[11.5px] text-tertiary">
                    {readOnlyDesign
                      ? "PowerPoint 真实渲染，与文件完全一致；要修改内容请在智能体里重新生成"
                      : "设计稿 · PowerPoint 真实渲染（还原渐变/圆角/玻璃质感）；切「可编辑」改文字，保存会按主题重排"}
                  </p>
                </>
              ) : (
                <SlideCanvas
                  key={`${selected}-${slide.layout}`}
                  slide={slide}
                  theme={theme}
                  index={selected}
                  deckTitle={deck.title}
                  author={deck.author}
                  editable
                  onChange={patchSlide}
                  className="rounded-lg"
                />
              )}
            </div>
          </div>
        </div>

        {/* 属性面板：宽屏常驻，面板模式是可切换的浮层；设计稿只读模式下无编辑属性可调 */}
        {!readOnlyDesign && (!compact || propsOpen) && (
          <div
            className={cn(
              "shrink-0 overflow-y-auto bg-surface-1 p-3",
              compact
                ? "absolute right-0 top-0 z-20 h-full w-[264px] border-l border-line shadow-[-10px_0_30px_rgb(0_0_0/0.12)]"
                : "w-[268px] border-l border-line",
            )}
          >
          <div className="mb-3 flex items-center gap-1.5">
            <select
              value={slide.layout}
              onChange={(event) => {
                const layout = event.target.value as SlideLayout;
                mutate((current) => ({
                  ...current,
                  slides: current.slides.map((item, i) =>
                    i === selected ? { ...blankSlide(layout), title: item.title } : item,
                  ),
                }));
              }}
              className="flex-1 rounded-md border border-line bg-surface-2 px-2 py-1.5 text-[12.5px] outline-none"
            >
              {LAYOUTS.map((layout) => (
                <option key={layout} value={layout}>
                  {LAYOUT_LABELS[layout]}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                if (deck.slides.length <= 1) return;
                mutate((current) => ({
                  ...current,
                  slides: current.slides.filter((_, i) => i !== selected),
                }));
                setSelected(Math.max(0, selected - 1));
              }}
              disabled={deck.slides.length <= 1}
              className="rounded-md p-1.5 text-secondary hover:bg-surface-3 hover:text-danger disabled:opacity-30"
              title="删除本页"
            >
              <Trash2 size={14} />
            </button>
            <button
              onClick={() => {
                if (selected === 0) return;
                mutate((current) => {
                  const list = [...current.slides];
                  const [item] = list.splice(selected, 1);
                  if (item) list.splice(selected - 1, 0, item);
                  return { ...current, slides: list };
                });
                setSelected(selected - 1);
              }}
              disabled={selected === 0}
              className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
              title="上移"
            >
              <ChevronUp size={14} />
            </button>
            <button
              onClick={() => {
                if (selected >= deck.slides.length - 1) return;
                mutate((current) => {
                  const list = [...current.slides];
                  const [item] = list.splice(selected, 1);
                  if (item) list.splice(selected + 1, 0, item);
                  return { ...current, slides: list };
                });
                setSelected(selected + 1);
              }}
              disabled={selected >= deck.slides.length - 1}
              className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
              title="下移"
            >
              <ChevronDown size={14} />
            </button>
          </div>

          <Field label="标题">
            <input
              value={slide.title ?? ""}
              onChange={(event) => patchSlide({ title: event.target.value })}
              className={inputCls}
            />
          </Field>

          {slide.layout !== "two_col" && slide.layout !== "table" && (
            <Field label={slide.layout === "quote" ? "出处" : "副标题"}>
              <input
                value={slide.subtitle ?? ""}
                onChange={(event) => patchSlide({ subtitle: event.target.value })}
                className={inputCls}
              />
            </Field>
          )}

          {["bullets", "two_col", "table", "stat", "chart", "timeline", "grid", "vs"].includes(slide.layout) && (
            <Field label="小标签（可选）">
              <input
                value={slide.kicker ?? ""}
                onChange={(event) => patchSlide({ kicker: event.target.value })}
                className={inputCls}
                placeholder="例如：背景 / 数据"
              />
            </Field>
          )}

          {slide.layout === "section" && (
            <Field label="章节序号">
              <input
                value={slide.number ?? ""}
                onChange={(event) => patchSlide({ number: event.target.value })}
                className={inputCls}
                placeholder="留空则自动编号"
              />
            </Field>
          )}

          {slide.layout === "quote" && (
            <Field label="引文">
              <textarea
                value={slide.quote ?? ""}
                onChange={(event) => patchSlide({ quote: event.target.value })}
                rows={4}
                className={cn(inputCls, "resize-y leading-relaxed")}
              />
            </Field>
          )}

          {(slide.layout === "bullets" || slide.layout === "table") && (
            <Field
              label="要点（每行一条，以 - 开头为次级）"
              hint={slide.layout === "table" ? "表格页缺表头时按要点渲染" : undefined}
            >
              <textarea
                value={(slide.bullets ?? []).join("\n")}
                onChange={(event) =>
                  patchSlide({
                    bullets: event.target.value
                      .split("\n")
                      .map((line) => line.trim())
                      .filter(Boolean),
                  })
                }
                rows={7}
                className={cn(inputCls, "resize-y leading-relaxed")}
              />
            </Field>
          )}

          {slide.layout === "two_col" && (
            <>
              {[0, 1].map((ci) => {
                const col = slide.columns?.[ci] ?? { title: "", bullets: [] };
                return (
                  <div key={ci} className="mb-3 rounded-md bg-surface-2 p-2">
                    <p className="mb-1.5 text-[11px] text-tertiary">
                      {ci === 0 ? "左栏" : "右栏"}
                    </p>
                    <input
                      value={col.title}
                      onChange={(event) => {
                        const columns = [0, 1].map((index) => ({
                          ...(slide.columns?.[index] ?? { title: "", bullets: [] }),
                        }));
                        const target = columns[ci] ?? { title: "", bullets: [] };
                        columns[ci] = { ...target, title: event.target.value };
                        patchSlide({ columns });
                      }}
                      placeholder="栏标题"
                      className={cn(inputCls, "mb-1.5")}
                    />
                    <textarea
                      value={col.bullets.join("\n")}
                      onChange={(event) => {
                        const columns = [0, 1].map((index) => ({
                          ...(slide.columns?.[index] ?? { title: "", bullets: [] }),
                        }));
                        const target = columns[ci] ?? { title: "", bullets: [] };
                        columns[ci] = {
                          ...target,
                          bullets: event.target.value
                            .split("\n")
                            .map((line) => line.trim())
                            .filter(Boolean),
                        };
                        patchSlide({ columns });
                      }}
                      rows={4}
                      className={cn(inputCls, "resize-y leading-relaxed")}
                    />
                  </div>
                );
              })}
            </>
          )}

          {slide.layout === "stat" && (
            <Field label="指标（每行一条：数值 | 标签 | 备注）">
              <textarea
                value={(slide.stats ?? []).map((s) => [s.value, s.label, s.note].join(" | ")).join("\n")}
                onChange={(event) =>
                  patchSlide({
                    stats: event.target.value
                      .split("\n")
                      .map((line) => line.split("|").map((cell) => cell.trim()))
                      .filter((cells) => cells[0])
                      .map((cells) => ({ value: cells[0] ?? "", label: cells[1] ?? "", note: cells[2] ?? "" })),
                  })
                }
                rows={4}
                className={cn(inputCls, "resize-y leading-relaxed")}
              />
            </Field>
          )}

          {slide.layout === "chart" && (
            <>
              <Field label="数据（每行一条：标签 | 数值）">
                <textarea
                  value={(slide.items ?? []).map((it) => `${it.label} | ${it.value}`).join("\n")}
                  onChange={(event) =>
                    patchSlide({
                      items: event.target.value
                        .split("\n")
                        .map((line) => line.split("|").map((cell) => cell.trim()))
                        .filter((cells) => cells[0])
                        .map((cells) => ({ label: cells[0] ?? "", value: cells[1] ?? "" })),
                    })
                  }
                  rows={5}
                  className={cn(inputCls, "resize-y leading-relaxed")}
                />
              </Field>
              <Field label="脚注（可选）">
                <input
                  value={slide.note ?? ""}
                  onChange={(event) => patchSlide({ note: event.target.value })}
                  className={inputCls}
                  placeholder="例如：数据截至最近季度末"
                />
              </Field>
            </>
          )}

          {slide.layout === "timeline" && (
            <Field label="节点（每行一条：标签 | 说明）">
              <textarea
                value={(slide.steps ?? []).map((s) => `${s.label} | ${s.text}`).join("\n")}
                onChange={(event) =>
                  patchSlide({
                    steps: event.target.value
                      .split("\n")
                      .map((line) => line.split("|").map((cell) => cell.trim()))
                      .filter((cells) => cells[0])
                      .map((cells) => ({ label: cells[0] ?? "", text: cells[1] ?? "" })),
                  })
                }
                rows={5}
                className={cn(inputCls, "resize-y leading-relaxed")}
              />
            </Field>
          )}

          {slide.layout === "grid" && (
            <Field label="卡片（每行一条：标题 | 说明）">
              <textarea
                value={(slide.cards ?? []).map((c) => `${c.title} | ${c.text}`).join("\n")}
                onChange={(event) =>
                  patchSlide({
                    cards: event.target.value
                      .split("\n")
                      .map((line) => line.split("|").map((cell) => cell.trim()))
                      .filter((cells) => cells[0])
                      .map((cells) => ({ title: cells[0] ?? "", text: cells[1] ?? "" })),
                  })
                }
                rows={5}
                className={cn(inputCls, "resize-y leading-relaxed")}
              />
            </Field>
          )}

          {slide.layout === "vs" && (
            <>
              {(["left", "right"] as const).map((key) => {
                const col = slide[key] ?? { title: "", bullets: [] };
                return (
                  <div key={key} className="mb-3 rounded-md bg-surface-2 p-2">
                    <p className="mb-1.5 text-[11px] text-tertiary">
                      {key === "left" ? "左侧" : "右侧"}
                    </p>
                    <input
                      value={col.title}
                      onChange={(event) => patchSlide({ [key]: { ...col, title: event.target.value } })}
                      placeholder="标题"
                      className={cn(inputCls, "mb-1.5")}
                    />
                    <textarea
                      value={col.bullets.join("\n")}
                      onChange={(event) =>
                        patchSlide({
                          [key]: {
                            ...col,
                            bullets: event.target.value
                              .split("\n")
                              .map((line) => line.trim())
                              .filter(Boolean),
                          },
                        })
                      }
                      rows={4}
                      className={cn(inputCls, "resize-y leading-relaxed")}
                    />
                  </div>
                );
              })}
            </>
          )}

          {slide.layout === "table" && (
            <>
              <Field label="表头（用 | 分隔）">
                <input
                  value={(slide.headers ?? []).join(" | ")}
                  onChange={(event) => {
                    const headers = event.target.value
                      .split("|")
                      .map((cell) => cell.trim())
                      .filter(Boolean);
                    patchSlide({ headers, rows: slide.rows ?? [] });
                  }}
                  className={inputCls}
                />
              </Field>
              <Field label="数据行（每行一组，用 | 分隔）">
                <textarea
                  value={(slide.rows ?? []).map((row) => row.join(" | ")).join("\n")}
                  onChange={(event) =>
                    patchSlide({
                      rows: event.target.value
                        .split("\n")
                        .filter((line) => line.trim())
                        .map((line) => line.split("|").map((cell) => cell.trim())),
                    })
                  }
                  rows={6}
                  className={cn(inputCls, "resize-y leading-relaxed")}
                />
              </Field>
            </>
          )}

          <Field label="作者 / 署名">
            <input
              value={deck.author}
              onChange={(event) => mutate((current) => ({ ...current, author: event.target.value }))}
              className={inputCls}
              placeholder="显示在封面上"
            />
          </Field>

          <p className="mt-4 border-t border-line pt-3 text-[11px] leading-relaxed text-tertiary">
            保存时按当前版式重新生成 pptx，文件 {Math.max(1, Math.round(deck.bytes / 1024))} KB。
            {deck.theme_inferred && " 主题由原文件推断，可能与原稿略有差异。"}
          </p>
          </div>
        )}
      </div>
    </div>
  );
}

function AddSlideButton({ onClick, compact = false }: { onClick: () => void; compact?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center justify-center gap-1 rounded-md border border-dashed border-line-strong text-[11.5px] text-tertiary hover:border-brand hover:text-brand",
        compact ? "h-[52px] w-[84px] shrink-0" : "py-2",
      )}
    >
      <Plus size={12} /> 加一页
    </button>
  );
}

function Thumb({
  slide,
  theme,
  index,
  deckTitle,
  author,
  active,
  onClick,
  className,
}: {
  slide: DeckSlide;
  theme: ThemePreset;
  index: number;
  deckTitle: string;
  author: string;
  active: boolean;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-label="幻灯片缩略图"
      className={cn(
        "group relative rounded-md ring-offset-1 transition-all",
        active ? "ring-2 ring-brand" : "ring-1 ring-line hover:ring-line-strong",
        className,
      )}
    >
      <span className="absolute -left-1 top-0 z-10 rounded-full bg-surface-1 px-1 text-[9.5px] text-tertiary">
        {index + 1}
      </span>
      <SlideCanvas
        slide={slide}
        theme={theme}
        index={index}
        deckTitle={deckTitle}
        author={author}
        className="w-full rounded-md"
      />
    </button>
  );
}

const inputCls =
  "w-full rounded-md border border-line bg-surface-2 px-2 py-1.5 text-[12.5px] outline-none focus:border-brand";

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
    <label className="mb-3 block">
      <span className="mb-1 block text-[11px] tracking-wide text-tertiary">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[10.5px] text-tertiary">{hint}</span>}
    </label>
  );
}

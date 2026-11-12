/**
 * 风格 / 模板选择器：内置设计风格（builtin:*，无需上传、带色卡预览）+ 公司模板上传。
 *
 * 内置风格与上传的主题包共用 ThemePackSummary 模型（后端 GET /ppt/templates 一次返回，
 * builtin 排前面）。value 为空时等价于默认风格「午夜蓝」——显示上要归一，否则用户
 * 看不出当前生效的是什么风格。
 *
 * 上传的模板会被抽出一支设计家族（基调 / 品牌色 / 字体 / 画布比例），所以列表里
 * 直接显示抽取结论，而不是版式数——用户关心的是「生成出来像不像我的模板」。
 */

import { useEffect, useRef, useState } from "react";
import { Check, ChevronUp, FileUp, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { cssHex } from "../design";
import * as api from "../api";
import type { ThemePackSummary } from "../types";

/** 与后端 designs.DEFAULT_KEY 对齐的默认风格 id。 */
export const DEFAULT_STYLE_ID = "builtin:midnight";

interface Props {
  value: string;
  onChange: (packId: string) => void;
  packs: ThemePackSummary[];
  onPacksChange: (packs: ThemePackSummary[]) => void;
  disabled?: boolean;
}

type UploadState =
  | { kind: "idle" }
  | { kind: "parsing" }
  | { kind: "ready"; id: string; name: string; hint: string }
  | { kind: "error"; message: string };

/** 模板抽取结论的一行摘要：浅色 · 4:3 · 主色 #1E6FD9。 */
function styleHint(pack: ThemePackSummary): string {
  const mode = pack.mode === "dark" ? "深色" : pack.mode === "light" ? "浅色" : "";
  return [mode, pack.ratio, pack.swatches[1] ? `主色 ${cssHex(pack.swatches[1])}` : ""]
    .filter(Boolean)
    .join(" · ");
}

/** 小色卡：bg 打底 + accent 斜条 + 第三色角块，一眼看出风格气质。 */
function StyleSwatch({ swatches, className }: { swatches: string[]; className?: string }) {
  const [bg, accent, paper] = [
    cssHex(swatches[0], "#0B1223"),
    cssHex(swatches[1], "#4F6BF6"),
    cssHex(swatches[2], "#FFFFFF"),
  ];
  return (
    <span
      className={cn("relative block overflow-hidden rounded-md border border-line", className)}
      style={{ background: bg }}
      aria-hidden
    >
      <span
        className="absolute -right-2 top-1/2 block h-8 w-8 -translate-y-1/2 rotate-45 rounded-sm"
        style={{ background: accent, opacity: 0.9 }}
      />
      <span
        className="absolute bottom-0 left-0 block h-1/3 w-full"
        style={{ background: paper, opacity: 0.14 }}
      />
    </span>
  );
}

export function TemplatePicker({ value, onChange, packs, onPacksChange, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [upload, setUpload] = useState<UploadState>({ kind: "idle" });

  const builtin = packs.filter((p) => p.kind === "builtin");
  const uploaded = packs.filter((p) => p.kind !== "builtin");

  // 空值按默认风格显示（后端同样把空值解析成午夜蓝）
  const effective = value || DEFAULT_STYLE_ID;
  // 恢复会话时 value 指向的包可能已被删除：回落到默认风格
  useEffect(() => {
    if (value && !packs.some((p) => p.id === value)) onChange("");
  }, [value, packs, onChange]);

  // 点外面收起浮层
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pptx")) {
      setUpload({ kind: "error", message: "只支持 .pptx 模板" });
      return;
    }
    setUpload({ kind: "parsing" });
    try {
      const pack = await api.uploadThemePack(file);
      onPacksChange([pack, ...packs.filter((p) => p.id !== pack.id)]);
      setUpload({ kind: "ready", id: pack.id, name: pack.name, hint: styleHint(pack) });
      onChange(pack.id);
    } catch (err) {
      setUpload({ kind: "error", message: (err as Error).message });
    }
  };

  const selected = packs.find((p) => p.id === effective);

  return (
    <div ref={boxRef} className="relative">
      <input
        ref={inputRef}
        type="file"
        accept=".pptx"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title="选择设计风格，或上传公司 .pptx 模板（≤32MB）"
        className={cn(
          "flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-surface-1 px-2 py-1.5 text-[12px] text-secondary transition-colors hover:border-brand/40 disabled:opacity-50",
          open && "border-brand/40",
        )}
      >
        <StyleSwatch swatches={selected?.swatches ?? []} className="h-4 w-6" />
        <span className="max-w-[96px] truncate">
          {selected ? selected.name : "默认风格"}
        </span>
        {selected?.ratio && (
          <span className="font-mono text-[10px] text-tertiary">{selected.ratio}</span>
        )}
        <ChevronUp size={11} className={cn("transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1.5 max-h-[340px] w-[356px] overflow-y-auto rounded-xl border border-line bg-surface-1 p-2 shadow-lg">
          {/* 内置风格 */}
          <p className="px-1 pb-1.5 pt-0.5 text-[11px] text-tertiary">内置风格</p>
          <div className="grid grid-cols-2 gap-1.5">
            {builtin.map((style) => {
              const active = style.id === effective;
              return (
                <button
                  key={style.id}
                  type="button"
                  onClick={() => {
                    onChange(style.id);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex items-center gap-2 rounded-lg border p-1.5 text-left transition-colors",
                    active
                      ? "border-brand/50 bg-brand/5"
                      : "border-line hover:border-line-strong hover:bg-hover",
                  )}
                >
                  <StyleSwatch swatches={style.swatches} className="h-7 w-11 shrink-0" />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1 text-[12px] font-medium text-primary">
                      {style.name}
                      {active && <Check size={11} className="text-brand-ink" />}
                    </span>
                    <span className="block truncate text-[10.5px] text-tertiary">
                      {style.tagline}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          {/* 公司模板 */}
          <p className="px-1 pb-1.5 pt-2.5 text-[11px] text-tertiary">
            公司模板（workspace 共享 · 按模板风格重绘）
          </p>
          {uploaded.length === 0 && upload.kind !== "parsing" && (
            <p className="px-1 pb-1.5 text-[11.5px] text-placeholder">
              还没有上传过模板，生成将使用内置风格
            </p>
          )}
          {uploaded.map((pack) => {
            const active = pack.id === effective;
            return (
              <button
                key={pack.id}
                type="button"
                onClick={() => {
                  onChange(pack.id);
                  setOpen(false);
                }}
                title={pack.tagline || styleHint(pack)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg border px-1.5 py-1.5 text-left transition-colors",
                  active
                    ? "border-brand/50 bg-brand/5"
                    : "border-transparent hover:border-line-strong hover:bg-hover",
                )}
              >
                <StyleSwatch swatches={pack.swatches} className="h-7 w-11 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-primary">{pack.name}</span>
                  <span className="block truncate text-[10.5px] text-tertiary">
                    {styleHint(pack) || `${pack.layout_count} 版式`}
                  </span>
                </span>
                <span
                  className="shrink-0 text-[10.5px] text-tertiary"
                  title={`模板解析保真度 ${pack.fidelity_score} 分`}
                >
                  {pack.fidelity_score}分
                </span>
                {active && <Check size={11} className="shrink-0 text-brand-ink" />}
              </button>
            );
          })}
          <button
            type="button"
            disabled={upload.kind === "parsing"}
            onClick={() => inputRef.current?.click()}
            className="mt-1 flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-1.5 py-1.5 text-[12px] text-secondary transition-colors hover:border-brand/40 hover:text-primary disabled:opacity-50"
          >
            {upload.kind === "parsing" ? (
              <>
                <Loader2 size={12} className="animate-spin" />
                解析中…
              </>
            ) : (
              <>
                <FileUp size={12} strokeWidth={1.9} />
                上传公司模板（.pptx）
              </>
            )}
          </button>
          {upload.kind === "ready" && (
            <p className="px-1 pt-1 text-[11px] leading-4 text-success">
              「{upload.name}」已就绪{upload.hint && ` · ${upload.hint}`}
            </p>
          )}
          {upload.kind === "error" && (
            <p className="truncate px-1 pt-1 text-[11px] text-danger" title={upload.message}>
              {upload.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

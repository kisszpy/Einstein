import type React from "react";
import { Plus } from "lucide-react";

import { cn } from "@/lib/utils";
import { SlideCanvas } from "./SlideCanvas";
import type { DeckSlide, ThemePreset } from "../types";

/**
 * 编辑器里反复出现的小零件。抽到一个文件不是为了「看起来整齐」，而是让
 * DeckEditor 只保留状态与布局，读它的人不必在 1200 行里翻这几个十行组件。
 */

export const inputCls =
  "w-full rounded-md border border-line bg-surface-2 px-2 py-1.5 text-[12.5px] outline-none focus:border-brand";

export function AddSlideButton({
  onClick,
  compact = false,
}: {
  onClick: () => void;
  compact?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center justify-center gap-1 rounded-md border border-dashed border-line-strong text-[12px] text-tertiary hover:border-brand hover:text-brand",
        compact ? "h-[52px] w-[84px] shrink-0" : "py-2",
      )}
    >
      <Plus size={12} /> 加一页
    </button>
  );
}

export function SlideThumb({
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

export function Field({
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
      <span className="mb-1 block text-[12px] tracking-wide text-tertiary">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-tertiary">{hint}</span>}
    </label>
  );
}

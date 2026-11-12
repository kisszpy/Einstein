/**
 * 幻灯片缩略图网格 + 放大看片。
 *
 * 两种来源共用：设计台的实时预览（data URL + 逐页体检）与结果卡的历史缩略图
 * （票据 URL，没有体检）。看片器用 Portal 挂到 body，键盘 ←/→ 翻页、Esc 关闭。
 */

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ChevronLeft, ChevronRight, Check, Maximize2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { layoutLabel } from "../design";
import type { PageLintReport } from "../types";

export interface PreviewTile {
  key: string;
  src: string;
  /** 0 起的页序，显示时 +1 */
  index: number;
  title: string;
  layout?: string;
  chapter?: string;
  report?: PageLintReport;
}

function StatusDot({ report }: { report?: PageLintReport }) {
  if (!report) return null;
  const errors = report.issues.filter((i) => i.severity === "error").length;
  const warns = report.issues.length - errors;
  if (errors > 0) {
    return (
      <span className="flex items-center gap-0.5 rounded bg-danger/90 px-1 py-px text-[9.5px] font-medium text-white">
        <AlertTriangle size={8} /> {errors}
      </span>
    );
  }
  if (warns > 0) {
    return (
      <span className="flex items-center gap-0.5 rounded bg-warning/90 px-1 py-px text-[9.5px] font-medium text-white">
        {warns} 提示
      </span>
    );
  }
  return (
    <span className="flex items-center gap-0.5 rounded bg-success/90 px-1 py-px text-[9.5px] font-medium text-white">
      <Check size={8} />
    </span>
  );
}

function Lightbox({
  tiles,
  start,
  onClose,
}: {
  tiles: PreviewTile[];
  start: number;
  onClose: () => void;
}) {
  const [cursor, setCursor] = useState(start);
  const tile = tiles[cursor];
  const move = useCallback(
    (delta: number) => setCursor((prev) => (prev + delta + tiles.length) % tiles.length),
    [tiles.length],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") move(-1);
      else if (e.key === "ArrowRight") move(1);
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [move, onClose]);

  if (!tile) return null;
  const issues = tile.report?.issues ?? [];
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-[#070a12]/88 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`第 ${tile.index + 1} 页：${tile.title}`}
        className="flex max-h-full w-full max-w-[1120px] flex-col gap-3"
      >
        <div className="flex items-center gap-3 text-[12.5px] text-white/70">
          <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[11px] text-white/90">
            {String(tile.index + 1).padStart(2, "0")} / {tiles.length}
          </span>
          <span className="min-w-0 flex-1 truncate text-white/90">{tile.title}</span>
          <span className="shrink-0 rounded bg-white/10 px-1.5 py-0.5 text-[11px]">
            {layoutLabel(tile.layout)}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭看片"
            className="shrink-0 rounded-md p-1.5 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        <div className="relative min-h-0 flex-1">
          <img
            src={tile.src}
            alt={`第 ${tile.index + 1} 页 ${tile.title}`}
            className="mx-auto max-h-[70vh] w-auto max-w-full rounded-lg shadow-[0_24px_80px_rgba(0,0,0,0.6)]"
          />
          {tiles.length > 1 && (
            <>
              <button
                type="button"
                onClick={() => move(-1)}
                aria-label="上一页"
                className="absolute left-0 top-1/2 -translate-y-1/2 rounded-full bg-black/45 p-2 text-white/80 transition-colors hover:bg-black/70 hover:text-white"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                type="button"
                onClick={() => move(1)}
                aria-label="下一页"
                className="absolute right-0 top-1/2 -translate-y-1/2 rounded-full bg-black/45 p-2 text-white/80 transition-colors hover:bg-black/70 hover:text-white"
              >
                <ChevronRight size={18} />
              </button>
            </>
          )}
        </div>

        {tile.report && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-white/6 px-3 py-2 text-[11.5px] text-white/65">
            <span>最小对比度 {tile.report.min_contrast.toFixed(1)}:1</span>
            <span>最小字号 {tile.report.min_type.toFixed(0)}px</span>
            <span>墨迹占比 {(tile.report.fill * 100).toFixed(0)}%</span>
            {tile.report.repairs.length > 0 && (
              <span className="text-white/85">自动修复：{tile.report.repairs.join(" → ")}</span>
            )}
            {issues.length === 0 && <span className="text-[#7ddba6]">逐页体检通过</span>}
            {issues.map((issue, i) => (
              <span
                key={`${issue.rule}-${i}`}
                className={issue.severity === "error" ? "text-[#ff9a9a]" : "text-[#f0c274]"}
              >
                {issue.detail}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function SlidePreviewGrid({
  tiles,
  loading,
  skeleton = 3,
  className,
}: {
  tiles: PreviewTile[];
  loading?: boolean;
  skeleton?: number;
  className?: string;
}) {
  const [openAt, setOpenAt] = useState<number | null>(null);
  // 上传模板可能是 4:3 甚至竖版：画布比例由缩略图自己说，写死 16:9 会把页脚裁掉
  const [ratio, setRatio] = useState(16 / 9);
  const boxRatio = Math.min(2.4, Math.max(0.72, ratio));
  const onSize = (img: HTMLImageElement) => {
    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
      setRatio(img.naturalWidth / img.naturalHeight);
    }
  };

  if (loading && tiles.length === 0) {
    return (
      <div className={cn("grid grid-cols-2 gap-2 sm:grid-cols-3", className)}>
        {Array.from({ length: skeleton }).map((_, i) => (
          <div
            key={i}
            className="animate-pulse rounded-lg border border-line bg-surface-3"
            style={{ animationDelay: `${i * 90}ms`, aspectRatio: boxRatio }}
          />
        ))}
      </div>
    );
  }

  return (
    <>
      <div className={cn("grid grid-cols-2 gap-2 sm:grid-cols-3", className)}>
        {tiles.map((tile, position) => (
          <button
            key={tile.key}
            type="button"
            onClick={() => setOpenAt(position)}
            title={`放大看第 ${tile.index + 1} 页：${tile.title}`}
            className={cn(
              "group relative block overflow-hidden rounded-lg border border-line bg-surface-3 text-left transition-all duration-150",
              "hover:-translate-y-0.5 hover:border-brand/45 hover:shadow-float",
              loading && "opacity-60",
            )}
          >
            {tile.src ? (
              <img
                src={tile.src}
                alt={`第 ${tile.index + 1} 页 ${tile.title}`}
                loading="lazy"
                onLoad={(e) => onSize(e.currentTarget)}
                className="block w-full object-contain"
                style={{ aspectRatio: boxRatio }}
              />
            ) : (
              <span
                className="flex w-full items-center justify-center text-[11px] text-placeholder"
                style={{ aspectRatio: boxRatio }}
              >
                无预览
              </span>
            )}
            <span className="pointer-events-none absolute left-1 top-1 rounded bg-black/55 px-1 py-px font-mono text-[9.5px] text-white/90">
              {String(tile.index + 1).padStart(2, "0")}
            </span>
            <span className="pointer-events-none absolute right-1 top-1">
              <StatusDot report={tile.report} />
            </span>
            <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-1 bg-gradient-to-t from-black/78 to-transparent px-1.5 pb-1 pt-4">
              <span className="min-w-0 flex-1 truncate text-[10.5px] text-white/90">
                {tile.title}
              </span>
              <span className="shrink-0 text-[9.5px] text-white/60">
                {layoutLabel(tile.layout)}
              </span>
            </span>
            <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all duration-150 group-hover:bg-black/25 group-hover:opacity-100">
              <Maximize2 size={16} className="text-white drop-shadow" />
            </span>
          </button>
        ))}
      </div>
      {openAt !== null && (
        <Lightbox tiles={tiles} start={openAt} onClose={() => setOpenAt(null)} />
      )}
    </>
  );
}
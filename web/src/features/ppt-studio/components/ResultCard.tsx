/**
 * 生成结果卡：成品缩略图 / 文件名 / 页数 / 版式摘要 / 模板出处与降级项（常显）/
 * 保真度徽章（可展开看逐页体检）/ 打开编辑器 / 下载 / 重新生成。
 *
 * 缩略图走 /api/ppt/thumbs/{gen}/{i}?ticket=…：<img> 带不了 Authorization，
 * 票据由 listGenerations 每次重签。项目被删时没有票据，只显示文字摘要。
 */

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  Images,
  Palette,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "../api";
import type { PptGeneration } from "../types";
import { SlidePreviewGrid, type PreviewTile } from "./SlidePreviewGrid";

interface Props {
  generation: PptGeneration;
  onOpenEditor: (generation: PptGeneration) => void;
  onRegenerate?: () => void;
  busy?: boolean;
}

export function fidelityTone(score: number): string {
  if (score >= 80) return "bg-success/10 text-success";
  if (score >= 60) return "bg-warning/10 text-warning";
  return "bg-danger/10 text-danger";
}

export function ResultCard({ generation, onOpenEditor, onRegenerate, busy }: Props) {
  const [showDetail, setShowDetail] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const fidelity = generation.fidelity;
  const deck = generation.deck;
  const failed = generation.status === "failed";
  const thumbCount = deck ? (generation.thumb_count ?? 0) : 0;
  const template = fidelity?.template;
  const measured = fidelity?.engine === "html" || fidelity?.engine === "template";
  const aesthetic = fidelity?.aesthetic;
  const direction = fidelity?.direction;

  const tiles: PreviewTile[] = useMemo(() => {
    const count = showAll ? thumbCount : Math.min(thumbCount, 6);
    return Array.from({ length: count }, (_, index) => ({
      key: `${generation.generation_id}-${index}`,
      src: api.thumbUrl(generation, index),
      index,
      title: `第 ${index + 1} 页`,
      layout: generation.layouts_used[index],
    }));
  }, [generation, showAll, thumbCount]);

  return (
    <div className="w-full max-w-[720px] rounded-xl border border-line bg-surface-1 p-3 sm:p-4">
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-medium text-primary" title={generation.deck_path}>
            {deck?.name ?? generation.deck_path.split("/").pop() ?? "演示文稿"}
          </p>
          <p className="mt-1 text-[12px] text-tertiary">
            {generation.total_slides} 页 · {generation.layouts_used.length} 种版式
            {deck && ` · ${(deck.bytes / 1024).toFixed(0)} KB`}
            {measured && " · 实测排版"}
            {fidelity?.composition?.skill_pages?.length
              ? ` · 技能重排 ${fidelity.composition.skill_pages.length} 页`
              : ""}
            {aesthetic?.after && aesthetic.after.total < aesthetic.before.total &&
              ` · 润色 ${aesthetic.before.total}→${aesthetic.after.total} 条`}
            {failed && " · 生成失败"}
          </p>
          {/* 出处与字体替换必须一眼可见：这两件事直接决定「像不像我的模板」，
              藏在保真度徽章后面等于没说 */}
          {direction?.name && (
            <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 text-[11.5px] text-secondary">
              <Sparkles size={11} className="shrink-0 translate-y-[1px] text-brand-ink" />
              <span className="shrink-0 font-medium text-primary">方向「{direction.name}」</span>
              <span className="min-w-0 text-tertiary">{direction.thesis}</span>
            </p>
          )}
          {template && (
            <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-secondary">
              <Palette size={11} className="shrink-0 text-brand-ink" />
              <span className="min-w-0 truncate">
                按「{template.name}」
                {fidelity?.engine === "template-shapes" ? "母版套版" : "风格重绘"}
              </span>
              <span className="shrink-0 text-tertiary">
                · {template.mode === "dark" ? "深色" : "浅色"} · {template.ratio}
              </span>
            </p>
          )}
          {fidelity && fidelity.degradations.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {fidelity.degradations.map((d, i) => (
                <li key={i} className="flex gap-1 text-[11.5px] leading-4 text-warning">
                  <AlertTriangle size={11} className="mt-[3px] shrink-0" />
                  <span className="min-w-0">{d}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {fidelity && (
          <button
            type="button"
            onClick={() => setShowDetail((v) => !v)}
            title="展开保真度报告"
            className={cn(
              "flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-opacity hover:opacity-80",
              fidelityTone(fidelity.score),
            )}
          >
            <ShieldCheck size={11} />
            保真度 {fidelity.score}
            {showDetail ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
        )}
      </div>

      {/* 成品缩略图：不用下载就能确认「生成的是我要的那一版」 */}
      {tiles.length > 0 && (
        <div className="mt-2.5">
          <SlidePreviewGrid tiles={tiles} />
          {thumbCount > 6 && (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="mt-1.5 flex items-center gap-1 text-[11.5px] text-tertiary transition-colors hover:text-brand-ink"
            >
              <Images size={11} />
              {showAll ? "收起" : `展开全部 ${thumbCount} 页`}
              {showAll ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            </button>
          )}
        </div>
      )}

      {/* 保真度明细：摘要 + 逐页告警（降级项已在卡头常显，这里不重复） */}
      {showDetail && fidelity && (
        <div className="mt-2 rounded-lg border border-line bg-surface-2 px-2.5 py-2">
          <p className="text-[11.5px] text-secondary">{fidelity.summary_zh}</p>
          {fidelity.issues.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {fidelity.issues.slice(0, 8).map((issue, i) => (
                <li key={i} className="text-[11px] text-tertiary">
                  第 {issue.slide_index + 1} 页：{issue.detail}（{issue.fallback}）
                </li>
              ))}
            </ul>
          )}
          {aesthetic && (aesthetic.findings?.length || aesthetic.applied.deferred.length) ? (
            <div className="mt-2 border-t border-line pt-1.5">
              <p className="flex flex-wrap items-center gap-x-1.5 text-[11px] text-tertiary">
                <Sparkles size={11} className="shrink-0 text-brand-ink" />
                美感检测
                {aesthetic.before.skipped
                  ? `未运行：${aesthetic.before.skipped}`
                  : aesthetic.after && aesthetic.after.total < aesthetic.before.total
                    ? `${aesthetic.before.total} → ${aesthetic.after.total} 条`
                    : aesthetic.after
                      ? `${aesthetic.before.total} 条${
                          aesthetic.applied.pages.length + (aesthetic.applied.global ? 1 : 0) > 0
                            ? `，已润色 ${aesthetic.applied.pages.length} 页`
                            : ""
                        }`
                      : `${aesthetic.before.total} 条待精修`}
              </p>
              {(aesthetic.findings ?? []).slice(0, 8).map((finding, i) => (
                <p key={i} className="mt-0.5 text-[11px] leading-4 text-tertiary">
                  <span className={finding.severity === "error" ? "text-warning" : "text-secondary"}>
                    {finding.name}
                  </span>
                  {finding.slide >= 0 && ` · 第 ${finding.slide + 1} 页`}
                  {finding.detail && ` · ${finding.detail}`}
                </p>
              ))}
              {aesthetic.applied.deferred.slice(0, 4).map((item, i) => (
                <p key={`d${i}`} className="mt-0.5 text-[11px] leading-4 text-tertiary">
                  需改结构{typeof item.index === "number" ? ` · 第 ${item.index} 页` : ""}
                  ：{item.reason}
                </p>
              ))}
              {aesthetic.skill?.name && aesthetic.skill.name !== "fallback" && (
                <p className="mt-1 text-[10.5px] text-tertiary">
                  由「{aesthetic.skill.name}」技能 · {aesthetic.skill.command} · v
                  {aesthetic.skill.version} 驱动
                </p>
              )}
              {aesthetic.skill?.name === "fallback" && (
                <p className="mt-1 text-[10.5px] text-tertiary">
                  未安装 impeccable 技能，本轮使用内置判据
                </p>
              )}
            </div>
          ) : null}
        </div>
      )}

      {failed && generation.error && (
        <p className="mt-2 rounded-lg bg-danger/10 px-2.5 py-1.5 text-[11.5px] text-danger">
          {generation.error}
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        {deck && (
          <a
            href={deck.download_url}
            title="下载 pptx（短期票据鉴权，15 分钟内有效）"
            className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] text-secondary transition-colors hover:border-brand/40 hover:text-primary"
          >
            <Download size={13} /> 下载
          </a>
        )}
        {deck && (
          <button
            type="button"
            onClick={() => onOpenEditor(generation)}
            className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] text-secondary transition-colors hover:border-brand/40 hover:text-primary"
          >
            <ExternalLink size={13} /> 打开编辑器
          </button>
        )}
        {onRegenerate && (
          <button
            type="button"
            onClick={onRegenerate}
            disabled={busy}
            title="按当前大纲再生成一份（同名文件自动追加 -2/-3，不会覆盖）"
            className="ml-auto flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12.5px] text-tertiary transition-colors hover:bg-hover hover:text-primary disabled:opacity-50"
          >
            <RefreshCw size={13} /> 重新生成
          </button>
        )}
      </div>
    </div>
  );
}

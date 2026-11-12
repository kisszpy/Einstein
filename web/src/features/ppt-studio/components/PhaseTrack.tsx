/**
 * 流水线阶段条。两条流水线各有一套步骤：
 *   取材：联网取材 → 知识库 → 规划大纲 → 完成
 *   成稿：内容深化 → 版式编排 → 浏览器实测 → 逐页体检 → 画面润色 → 写入 PPTX → 完成
 * 后端 phase 帧驱动；失败时整条变红并保留重试入口（由上层渲染按钮）。
 */

import { CheckCircle2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PptPhase } from "../types";

const ANALYZE_STEPS: PptPhase[] = ["collecting", "kb", "outlining", "done"];
const GENERATE_STEPS: PptPhase[] = [
  "deepening",
  "composing",
  "measuring",
  "repairing",
  "polishing",
  "writing",
  "done",
];

const LABEL: Record<string, string> = {
  collecting: "联网取材",
  kb: "知识库",
  outlining: "规划大纲",
  deepening: "内容深化",
  composing: "版式编排",
  measuring: "浏览器实测",
  repairing: "逐页体检",
  polishing: "画面润色",
  writing: "写入 PPTX",
  done: "完成",
};

/** 粗粒度阶段落到具体步骤上：回落基础排版时只发 rendering，也要能往前走。 */
const ALIAS: Record<string, PptPhase> = {
  rendering: "composing",
  ready: "outlining",
  clarifying: "outlining",
};

export function PhaseTrack({
  phase,
  progress,
  label,
  failed,
  mode = "analyze",
}: {
  phase: PptPhase | null;
  progress: number;
  label: string;
  failed?: boolean;
  mode?: "analyze" | "generate";
}) {
  const steps = mode === "generate" ? GENERATE_STEPS : ANALYZE_STEPS;
  const resolved = (phase ? (ALIAS[phase] ?? phase) : null) as PptPhase | null;
  const found = resolved ? steps.indexOf(resolved) : -1;
  const reached = found >= 0 ? found + 1 : 0;

  return (
    <div className="rounded-xl border border-line bg-surface-1 px-3.5 py-2.5">
      <div className="flex items-center gap-2 text-[12.5px]">
        {failed ? (
          <span className="flex items-center gap-1.5 text-danger">
            <Loader2 size={13} className="animate-spin" />
            {label || "处理失败"}
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-secondary">
            <Loader2 size={13} className="animate-spin text-brand-ink" />
            {label || "处理中…"}
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-tertiary">{progress}%</span>
      </div>

      <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-3">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300 ease-out",
            failed ? "bg-danger" : "bg-gradient-to-r from-brand-deep via-brand to-[#22b8cf]",
          )}
          style={{ width: `${Math.max(3, Math.min(100, progress))}%` }}
        />
      </div>

      <div className="mt-2 flex items-center gap-1">
        {steps.map((step, index) => {
          const done = index + 1 < reached || (progress >= 100 && !failed);
          const active = index + 1 === reached && !failed && progress < 100;
          return (
            <div key={step} className="flex min-w-0 flex-1 items-center gap-1">
              <span
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px] transition-colors duration-200",
                  done && !failed
                    ? "bg-brand text-brand-fg"
                    : active
                      ? "bg-brand-soft text-brand-ink ring-2 ring-brand/35"
                      : "bg-surface-3 text-tertiary",
                )}
              >
                {done && !failed ? <CheckCircle2 size={11} /> : index + 1}
              </span>
              <span
                className={cn(
                  "truncate text-[11px]",
                  active ? "text-primary" : done ? "text-secondary" : "text-tertiary",
                )}
              >
                {LABEL[step] ?? step}
              </span>
              {index < steps.length - 1 && <span className="h-px flex-1 bg-line" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
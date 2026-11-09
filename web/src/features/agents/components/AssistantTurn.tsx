/**
 * 智能体的一轮回答：步骤摘要（可展开）→ 正文 → 产物卡片 → 操作。
 * 版式对齐 WorkBuddy：工具过程默认收起成一行，产物以卡片挂在正文下方。
 */

import { useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Circle,
  Copy,
  Download,
  Info,
  Loader2,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { Markdown } from "@/features/chat/components/Markdown";
import { cn } from "@/lib/utils";
import {
  artifactKindLabel,
  downloadUrl,
  fileName,
  toolIcon,
  toolLabel,
  type AgentStep,
  type Artifact,
  type Citation,
} from "../types";

interface Props {
  steps: AgentStep[];
  answer: string;
  artifacts: Artifact[];
  citations?: Citation[];
  running: boolean;
  error: string | null;
  project: string;
  elapsedMs: number | null;
  onOpenFile: (path: string) => void;
  onRerun: () => void;
}

interface Row {
  call: AgentStep;
  result?: AgentStep;
}

function pairSteps(steps: AgentStep[]): Row[] {
  const rows: Row[] = [];
  // 计划帧夹在调用与结果之间，先剔掉否则配对会错位；note 是系统提示，不参与配对
  const toolSteps = steps.filter((s) => s.kind !== "plan" && s.kind !== "note");
  for (let i = 0; i < toolSteps.length; i += 1) {
    const step = toolSteps[i];
    if (!step || step.kind !== "tool_call") continue;
    const next = toolSteps[i + 1];
    rows.push({ call: step, result: next?.kind === "tool_result" ? next : undefined });
  }
  return rows;
}

function seconds(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export function AssistantTurn({
  steps,
  answer,
  artifacts,
  citations = [],
  running,
  error,
  project,
  elapsedMs,
  onOpenFile,
  onRerun,
}: Props) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Record<number, boolean>>({});
  const [copied, setCopied] = useState(false);

  const rows = useMemo(() => pairSteps(steps), [steps]);
  // 预算续接等系统提示（note 帧）：以一行弱化文字渲染，不打断步骤流
  const notes = useMemo(() => steps.filter((s) => s.kind === "note"), [steps]);
  // 计划会随执行反复重写，只取最新一版渲染
  const plan = useMemo(() => {
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const s = steps[i];
      if (s?.kind === "plan" && (s.items?.length ?? 0) > 0) return s.items ?? [];
    }
    return [];
  }, [steps]);
  const pending = useMemo(() => {
    if (!running || steps.length === 0) return null;
    const last = [...steps].reverse().find((s) => s.kind !== "plan");
    return last && last.kind === "tool_call" ? last : null;
  }, [running, steps]);

  const showRows = (running || open) && rows.length > 0;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用时静默 */
    }
  };

  return (
    <div className="group/turn mb-6">
      {plan.length > 0 && (
        <div className="mb-2 rounded-lg border border-line bg-surface-1 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11.5px] text-tertiary">
            <span>计划</span>
            <span className="tabular-nums">
              {plan.filter((p) => p.status === "completed").length}/{plan.length}
            </span>
          </div>
          <ul className="mt-1 space-y-0.5">
            {plan.map((item, i) => {
              const done = item.status === "completed";
              const doing = item.status === "in_progress";
              return (
                <li key={i} className="flex items-start gap-1.5 text-[12.5px] leading-relaxed">
                  {done ? (
                    <Check size={12} className="mt-[5px] shrink-0 text-tertiary" />
                  ) : doing ? (
                    <Loader2 size={11} className="mt-[6px] shrink-0 animate-spin text-brand" />
                  ) : (
                    <Circle size={8} className="mt-[8px] shrink-0 text-tertiary/60" />
                  )}
                  <span
                    className={cn(
                      "min-w-0",
                      done ? "text-tertiary line-through decoration-line" : doing ? "text-primary" : "text-secondary",
                    )}
                  >
                    {item.content}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {notes.length > 0 && (
        <div className="mb-2 space-y-1">
          {notes.map((n, i) => (
            <p key={i} className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-tertiary">
              <Info size={12} className="mt-[3px] shrink-0" />
              <span>{n.content}</span>
            </p>
          ))}
        </div>
      )}

      {steps.length > 0 && (
        <div className="mb-2">
          <button
            type="button"
            onClick={() => !running && setOpen((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-md py-0.5 text-[12.5px] transition-colors duration-150",
              running ? "cursor-default text-secondary" : "text-tertiary hover:text-secondary",
            )}
          >
            {running ? (
              <Loader2 size={13} className="animate-spin text-brand" />
            ) : (
              <Sparkles size={13} className="text-brand" />
            )}
            <span>
              {running
                ? pending
                  ? `${toolLabel(pending.name ?? "")}…`
                  : "正在思考…"
                : `已完成 ${rows.length} 个步骤`}
            </span>
            {!running && elapsedMs != null && elapsedMs > 0 && (
              <span className="text-tertiary">· {seconds(elapsedMs)}</span>
            )}
            {running && rows.length > 0 && <span className="text-tertiary">· {rows.length} 步</span>}
            {!running && rows.length > 0 && (
              <ChevronDown
                size={12}
                className={cn("transition-transform duration-150", open && "rotate-180")}
              />
            )}
          </button>

          {showRows && (
            <ol className="mt-1.5 space-y-px border-l border-line pl-2.5">
              {rows.map((row, i) => {
                const Icon = toolIcon(row.call.name ?? "");
                const failed = row.result?.content?.startsWith("错误") ?? false;
                const expanded = detail[i];
                return (
                  <li key={i}>
                    <button
                      type="button"
                      onClick={() => setDetail((prev) => ({ ...prev, [i]: !prev[i] }))}
                      className="group flex w-full items-start gap-2 rounded-lg px-1.5 py-1 text-left transition-colors duration-150 hover:bg-hover"
                    >
                      <Icon
                        size={13}
                        strokeWidth={1.9}
                        className={cn(
                          "mt-[3px] shrink-0",
                          failed ? "text-danger" : row.result ? "text-tertiary" : "text-brand",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline gap-1.5">
                          <span className="truncate text-[12.5px] text-secondary">
                            {toolLabel(row.call.name ?? "")}
                          </span>
                          {row.result?.duration_ms != null && (
                            <span className="shrink-0 text-[11px] text-tertiary">
                              {row.result.duration_ms}ms
                            </span>
                          )}
                          {!row.result && running && (
                            <Loader2 size={10} className="animate-spin text-tertiary" />
                          )}
                        </span>
                        <span className="mt-0.5 block truncate text-[11.5px] text-tertiary">
                          {(row.call.args ?? "").replace(/\s+/g, " ").slice(0, 120) || "—"}
                        </span>
                      </span>
                      <ChevronDown
                        size={11}
                        className={cn(
                          "mt-1 shrink-0 text-tertiary opacity-0 transition-all group-hover:opacity-100",
                          expanded && "rotate-180 opacity-100",
                        )}
                      />
                    </button>
                    {expanded && (
                      <div className="mb-1 ml-5 mr-1 space-y-1">
                        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-surface-3 p-2 text-[11.5px] leading-relaxed text-secondary">
                          {row.call.args || "—"}
                        </pre>
                        {row.result && (
                          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-surface-3 p-2 text-[11.5px] leading-relaxed text-secondary">
                            {(row.result.content ?? "").slice(0, 4000) || "—"}
                          </pre>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}

      {answer && (
        <div>
          <Markdown content={answer} />
          {running && (
            <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-brand align-text-bottom" />
          )}
        </div>
      )}

      {citations.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-[11.5px] font-medium tracking-wide text-tertiary">参考资料</p>
          {citations.map((cite) => (
            <div
              key={`${cite.chunk_id}-${cite.index}`}
              title={cite.text}
              className="flex items-center gap-1.5 truncate rounded-lg bg-surface-1 px-2.5 py-1.5 text-[12px] text-secondary"
            >
              <span className="shrink-0 font-medium text-brand">[{cite.index}]</span>
              <span className="truncate">{cite.document_name}</span>
              <span className="ml-auto shrink-0 text-[11px] text-tertiary">
                相关度 {Number(cite.score ?? 0).toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {artifacts.map((artifact) => (
            <div
              key={artifact.path}
              className="group flex items-center gap-2.5 rounded-xl border border-line bg-surface-1 px-2.5 py-2 transition-colors duration-150 hover:border-line-strong"
            >
              <button
                onClick={() => onOpenFile(artifact.path)}
                title="在右侧预览（不改页面）"
                className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-[12px] font-semibold text-brand-deep">
                  {fileName(artifact.path).split(".").pop()?.slice(0, 3).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-primary">
                    {fileName(artifact.path)}
                  </span>
                  <span className="block truncate text-[11px] text-tertiary">
                    {artifactKindLabel(artifact)}
                  </span>
                </span>
                <span className="mr-1 shrink-0 rounded-md px-1.5 py-1 text-[11.5px] text-tertiary transition-colors group-hover:text-brand">
                  预览
                </span>
              </button>
              {project && (
                <a
                  href={downloadUrl(project, artifact.path)}
                  download
                  title="下载"
                  className="shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-hover hover:text-primary"
                >
                  <Download size={13} strokeWidth={1.9} />
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-[12px] text-danger">{error}</p>
      )}

      {!running && (answer || artifacts.length > 0) && (
        <div className="mt-2 flex items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover/turn:opacity-100">
          {answer && (
            <button
              onClick={() => void copy()}
              title="复制回答"
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-tertiary transition-colors hover:bg-hover hover:text-secondary"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "已复制" : "复制"}
            </button>
          )}
          <button
            onClick={onRerun}
            title="用同样的任务再跑一次"
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-tertiary transition-colors hover:bg-hover hover:text-secondary"
          >
            <RotateCcw size={12} />
            重跑
          </button>
        </div>
      )}
    </div>
  );
}

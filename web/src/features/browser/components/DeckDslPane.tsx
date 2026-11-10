import { ListChecks, Loader2, Play } from "lucide-react";

import { cn } from "@/lib/utils";
import type * as api from "../api";

/**
 * DSL 编辑面板：源码 → 校验 → 重编译本页。
 *
 * 从 DeckEditor 里拆出来的原因很实际：它有一套独立的「诊断分级 + 状态文案」
 * 逻辑（error/fatal 标红、通过时说无问题、没有 DSL 记录时说明为什么不能编辑），
 * 埋在 1200 行的组件里既没法单测，也容易被下一次改动顺手改坏。
 */
export function DeckDslPane({
  dsl,
  hasDsl,
  busy,
  diagnostics,
  error,
  onDslChange,
  onLint,
  onApply,
}: {
  dsl: string;
  hasDsl: boolean;
  busy: boolean;
  diagnostics: api.DslDiagnostic[] | null;
  error: string;
  onDslChange: (value: string) => void;
  onLint: () => void;
  onApply: () => void;
}) {
  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-[12px] text-tertiary">
          {hasDsl
            ? "slidep DSL（JSX 风格，画布 1280×720）：先校验，无 error 再编译保存"
            : "该页没有 DSL 记录（非 slidep 生成），无法在此编辑，可在 PowerPoint 里直接改"}
        </p>
        <button
          onClick={onLint}
          disabled={busy || !hasDsl}
          className="flex shrink-0 items-center gap-1 rounded-md border border-line bg-surface-1 px-2.5 py-1.5 text-[12.5px] text-secondary hover:bg-surface-3 disabled:opacity-40"
        >
          <ListChecks size={13} /> 校验
        </button>
        <button
          onClick={onApply}
          disabled={busy || !hasDsl}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium transition-colors",
            hasDsl ? "bg-brand text-brand-fg hover:opacity-90" : "bg-surface-3 text-tertiary",
          )}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {busy ? "处理中" : "编译并保存"}
        </button>
      </div>
      <textarea
        value={dsl}
        onChange={(event) => onDslChange(event.target.value)}
        readOnly={!hasDsl}
        spellCheck={false}
        rows={16}
        className="w-full resize-y rounded-lg border border-line bg-surface-2 p-3 font-mono text-[12.5px] leading-relaxed outline-none focus:border-brand"
      />
      {(error || diagnostics) && (
        <div className="mt-2 rounded-md border border-line bg-surface-2 p-2.5 text-[12px] leading-relaxed">
          {error && <p className="text-danger">{error}</p>}
          {diagnostics && diagnostics.length === 0 && !error && (
            <p className="font-medium text-brand-deep dark:text-brand">校验通过，无诊断问题</p>
          )}
          <ul className="space-y-1">
            {(diagnostics ?? []).map((item, i) => {
              const level = String(item.severity ?? "").toLowerCase();
              const msg = String(item.message ?? "").slice(0, 240);
              return (
                <li
                  key={i}
                  className={cn(
                    level === "error" || level === "fatal" ? "text-danger" : "text-secondary",
                  )}
                >
                  <span className="mr-1.5 font-mono text-[11px] opacity-70">
                    [{item.ruleId ?? level}]
                  </span>
                  {msg}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

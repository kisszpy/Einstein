import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { CompareResult, SearchHit, SearchMode } from "@/features/kb/kb-types";
import { MODE_LABEL, SEARCH_MODES } from "@/features/kb/kb-types";
import { cn } from "@/lib/utils";

/** 检索测试台：单模式召回 + 三模式并排对比（同一 query 的 diff 与耗时）。 */
export function SearchBench({
  query,
  mode,
  hits,
  compare,
  busy,
  onQueryChange,
  onModeChange,
  onSearch,
  onCompare,
  onDismissCompare,
}: {
  query: string;
  mode: SearchMode;
  hits: SearchHit[] | null;
  compare: CompareResult | null;
  busy: boolean;
  onQueryChange: (value: string) => void;
  onModeChange: (mode: SearchMode) => void;
  onSearch: () => void;
  onCompare: () => void;
  onDismissCompare: () => void;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-medium">检索测试台</h3>
        <div className="flex gap-1">
          {SEARCH_MODES.map((m) => (
            <button
              key={m}
              onClick={() => onModeChange(m)}
              className={cn(
                "rounded-md border px-2 py-0.5 text-xs transition-colors duration-150",
                mode === m
                  ? "border-brand bg-brand-soft text-brand-ink "
                  : "border-line text-secondary hover:border-line-strong",
              )}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void onSearch()}
          placeholder="输入测试 query，查看召回效果"
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
        />
        <Button disabled={busy || !query.trim()} onClick={() => void onSearch()}>
          检索
        </Button>
        <Button variant="outline" disabled={busy || !query.trim()} onClick={() => void onCompare()}>
          对比
        </Button>
      </div>
      {hits && (
        <div className="mt-4 space-y-2">
          {hits.length === 0 ? (
            <p className="text-xs text-tertiary">无命中</p>
          ) : (
            hits.map((h) => (
              <div key={h.chunk_id} className="rounded-lg bg-surface-3 p-3">
                <div className="flex items-center justify-between gap-3 text-xs text-secondary">
                  <span className="truncate">{h.document_name}</span>
                  <span className="shrink-0 text-tertiary">
                    {h.vector_score != null && `vec ${h.vector_score.toFixed(3)}`}
                    {h.vector_score != null && h.lexical_score != null && " · "}
                    {h.lexical_score != null && `bm25 ${h.lexical_score.toFixed(2)}`}
                    {mode === "hybrid" && ` · rrf ${h.score.toFixed(4)}`}
                  </span>
                </div>
                <p className="mt-1 line-clamp-3 text-[13px] text-primary">{h.text}</p>
              </div>
            ))
          )}
        </div>
      )}
      {compare && (
        <div className="mt-4 space-y-3">
          {SEARCH_MODES.map((m) => {
            const rows = compare.results[m] ?? [];
            const ms = compare.timings_ms[m];
            return (
              <div key={m}>
                <p className="mb-1 text-xs text-secondary">
                  {MODE_LABEL[m]}
                  <span className="ml-2 text-tertiary">
                    {rows.length} 条{ms != null && ` · ${ms}ms`}
                  </span>
                </p>
                {rows.length === 0 ? (
                  <p className="rounded-lg bg-surface-3 p-2 text-xs text-tertiary">无命中</p>
                ) : (
                  <div className="space-y-1">
                    {rows.map((h, i) => (
                      <div key={h.chunk_id} className="rounded-lg bg-surface-3 p-2 text-xs">
                        <span className="text-tertiary">#{i + 1}</span>{" "}
                        <span className="text-secondary">{h.document_name}</span>
                        <p className="mt-0.5 line-clamp-2 text-primary">{h.text}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          <button
            onClick={onDismissCompare}
            className="text-xs text-tertiary transition-colors hover:text-secondary"
          >
            收起对比
          </button>
        </div>
      )}
    </Card>
  );
}

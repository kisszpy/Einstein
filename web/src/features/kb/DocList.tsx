import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LinkIcon } from "@/features/kb/kb-atoms";
import type { ChunkItem, DocItem, DocVersion } from "@/features/kb/kb-types";
import { shortTime } from "@/features/kb/kb-types";
import { relativeTime, sourceHost, type BatchRefreshResult } from "@/features/kb/kbShared";
import { cn } from "@/lib/utils";

const ROW_ACTION =
  "rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-surface-1 hover:text-primary disabled:opacity-50";

/**
 * 文档列表 + 就地展开的「切片 / 版本」面板。
 *
 * 这块是整页最长的一段 JSX（约 260 行），但它的状态全部来自上层：
 * 自己不发请求、不持状态，只负责把 docs / chunks / versions 画出来并把
 * 点击回传。这样「行内 busy」和「面板 busy」的区别就能在页面上写清楚，
 * 而不至于让用户觉得点删除没反应。
 */
export function DocList({
  docs,
  canWrite,
  batchBusy,
  batchResult,
  onRefreshBatch,
  onDismissBatch,
  openDoc,
  panel,
  chunks,
  chunkDrafts,
  versions,
  panelBusy,
  rowBusy,
  onTogglePanel,
  onClosePanel,
  onChunkDraft,
  onSaveChunk,
  onRefreshDoc,
  onReindexDoc,
  onRemoveDoc,
  onRollback,
}: {
  docs: DocItem[];
  canWrite: boolean;
  batchBusy: "stale" | "all" | null;
  batchResult: BatchRefreshResult | null;
  onRefreshBatch: (staleOnly: boolean) => void;
  onDismissBatch: () => void;
  openDoc: string | null;
  panel: "chunks" | "versions";
  chunks: ChunkItem[];
  chunkDrafts: Record<string, string>;
  versions: DocVersion[];
  panelBusy: boolean;
  rowBusy: string | null;
  onTogglePanel: (doc: DocItem, next: "chunks" | "versions") => void;
  onClosePanel: () => void;
  onChunkDraft: (chunkId: string, text: string) => void;
  onSaveChunk: (chunk: ChunkItem) => void;
  onRefreshDoc: (doc: DocItem) => void;
  onReindexDoc: (doc: DocItem) => void;
  onRemoveDoc: (doc: DocItem) => void;
  onRollback: (doc: DocItem, version: number) => void;
}) {
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-medium">文档（{docs.length}）</h3>
        {canWrite && docs.length > 0 && (
          <div className="flex gap-1">
            <Button
              variant="outline"
              disabled={batchBusy !== null}
              onClick={() => onRefreshBatch(true)}
            >
              {batchBusy === "stale" ? "检查中…" : "重抓过期文档"}
            </Button>
            <Button
              variant="outline"
              disabled={batchBusy !== null}
              onClick={() => onRefreshBatch(false)}
            >
              {batchBusy === "all" ? "重抓中…" : "全部重抓"}
            </Button>
          </div>
        )}
      </div>
      {batchResult && (
        <div className="mt-3 rounded-lg border border-line bg-surface-2 p-3 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="text-secondary">
              共 {batchResult.total} 篇，更新 {batchResult.changed} 篇，失败{" "}
              {batchResult.failed} 篇
            </span>
            <button
              onClick={onDismissBatch}
              className="shrink-0 text-tertiary transition-colors hover:text-secondary"
            >
              关闭
            </button>
          </div>
          {batchResult.items.some((it) => !it.ok) && (
            <div className="mt-2 space-y-1 border-t border-line pt-2">
              {batchResult.items
                .filter((it) => !it.ok)
                .map((it) => (
                  <p key={it.id} className="break-words text-danger">
                    「{it.name}」{it.error || "未知原因"}
                  </p>
                ))}
            </div>
          )}
        </div>
      )}
      {docs.length === 0 ? (
        <p className="mt-2 text-xs text-tertiary">还没有文档</p>
      ) : (
        <div className="mt-3 space-y-1">
          {docs.map((d) => {
            const expanded = openDoc === d.id;
            return (
              <div key={d.id} className="rounded-lg transition-colors duration-150 hover:bg-surface-3">
                <div className="group flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] text-primary">{d.name}</span>
                      <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[12px] text-tertiary">
                        v{d.version}
                      </span>
                    </div>
                    <div className="text-xs text-tertiary">
                      {d.chunk_count} 个切片
                      {d.source && ` · ${d.source}`}
                      {d.updated_at && ` · ${shortTime(d.updated_at)}`}
                    </div>
                    {d.source_url && (
                      <div className="mt-0.5 flex items-center gap-2 text-[12px]">
                        <a
                          href={d.source_url}
                          target="_blank"
                          rel="noreferrer"
                          title={d.source_url}
                          className="inline-flex min-w-0 items-center gap-1 text-secondary transition-colors duration-150 hover:text-brand-ink"
                        >
                          <LinkIcon className="h-3 w-3 shrink-0" />
                          <span className="truncate">{sourceHost(d.source_url)}</span>
                        </a>
                        {d.fetched_at && (
                          <span className="shrink-0 text-tertiary">
                            抓取于 {relativeTime(d.fetched_at)}
                          </span>
                        )}
                        {d.stale && (
                          <span className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-warning">
                            可能过期
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100">
                    {canWrite && (
                      <button
                        onClick={() => onTogglePanel(d, "chunks")}
                        className={cn(
                          "rounded-md px-1.5 py-1 text-xs transition-colors duration-150 hover:bg-surface-1 hover:text-primary",
                          expanded && panel === "chunks"
                            ? "text-brand-ink "
                            : "text-tertiary",
                        )}
                      >
                        切片
                      </button>
                    )}
                    <button
                      onClick={() => onTogglePanel(d, "versions")}
                      className={cn(
                        "rounded-md px-1.5 py-1 text-xs transition-colors duration-150 hover:bg-surface-1 hover:text-primary",
                        expanded && panel === "versions"
                          ? "text-brand-ink "
                          : "text-tertiary",
                      )}
                    >
                      版本
                    </button>
                    {canWrite && d.source_url && (
                      <button
                        disabled={rowBusy === d.id}
                        onClick={() => void onRefreshDoc(d)}
                        className={ROW_ACTION}
                      >
                        {rowBusy === d.id ? "抓取中…" : "重抓"}
                      </button>
                    )}
                    {canWrite && (
                      <button
                        disabled={rowBusy === d.id}
                        onClick={() => void onReindexDoc(d)}
                        className={ROW_ACTION}
                      >
                        {rowBusy === d.id ? "处理中…" : "重建"}
                      </button>
                    )}
                    {canWrite && (
                      <button
                        disabled={rowBusy === d.id}
                        onClick={() => void onRemoveDoc(d)}
                        className="rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                      >
                        删除
                      </button>
                    )}
                  </div>
                </div>

                {expanded && panel === "chunks" && (
                  <div className="mx-3 mb-2 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
                    <div className="flex items-center justify-between text-[12px] text-tertiary">
                      <span>切片（{chunks.length}）· 修改保存后会单条重嵌入</span>
                      <button onClick={onClosePanel} className="transition-colors hover:text-secondary">
                        收起
                      </button>
                    </div>
                    {panelBusy && <p className="py-3 text-center text-xs text-tertiary">加载中…</p>}
                    {!panelBusy && chunks.length === 0 && (
                      <p className="py-3 text-center text-xs text-tertiary">该文档没有切片</p>
                    )}
                    {chunks.map((c) => {
                      const draft = chunkDrafts[c.id] ?? c.text;
                      const dirty = draft !== c.text;
                      return (
                        <div key={c.id} className="rounded-lg border border-line bg-surface-1 p-2.5">
                          <div className="flex items-center justify-between gap-2 text-[12px] text-tertiary">
                            <span>
                              #{c.seq} · {draft.trim().length} 字
                              {c.parent_seq != null && ` · 父片 #${c.parent_seq}`}
                              {!c.has_vector && " · 无向量"}
                            </span>
                            <div className="flex items-center gap-1">
                              {dirty && (
                                <button
                                  onClick={() => onChunkDraft(c.id, c.text)}
                                  className="rounded-md px-1.5 py-0.5 transition-colors hover:bg-surface-3 hover:text-secondary"
                                >
                                  还原
                                </button>
                              )}
                              <button
                                disabled={!dirty || !draft.trim() || rowBusy === c.id}
                                onClick={() => void onSaveChunk(c)}
                                className="rounded-md px-1.5 py-0.5 text-brand-ink transition-colors  hover:bg-brand-soft disabled:opacity-40"
                              >
                                {rowBusy === c.id ? "保存中…" : "保存"}
                              </button>
                            </div>
                          </div>
                          <textarea
                            value={draft}
                            onChange={(e) => onChunkDraft(c.id, e.target.value)}
                            rows={3}
                            className="mt-1.5 w-full resize-y rounded-md border border-line bg-surface-1 p-2 text-[12px] leading-relaxed outline-none focus:border-line-strong"
                          />
                        </div>
                      );
                    })}
                  </div>
                )}

                {expanded && panel === "versions" && (
                  <div className="mx-3 mb-2 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
                    <div className="flex items-center justify-between text-[12px] text-tertiary">
                      <span>版本历史 · 当前 v{d.version}</span>
                      <button onClick={onClosePanel} className="transition-colors hover:text-secondary">
                        收起
                      </button>
                    </div>
                    {panelBusy && <p className="py-3 text-center text-xs text-tertiary">加载中…</p>}
                    {!panelBusy && versions.length === 0 && (
                      <p className="py-3 text-center text-xs text-tertiary">
                        还没有历史版本；同名文档内容变化或回滚时会生成快照。
                      </p>
                    )}
                    {versions.map((v) => (
                      <div
                        key={v.version}
                        className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface-1 px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="flex items-center gap-2 text-[12px] text-primary">
                            <span className="font-mono">v{v.version}</span>
                            {v.note && <span className="text-tertiary">{v.note}</span>}
                            {v.version === d.version && (
                              <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[12px] text-brand-ink ">
                                当前
                              </span>
                            )}
                          </p>
                          <p className="truncate text-[12px] text-tertiary">
                            {v.chunk_count} 切片 · {v.chars} 字
                            {v.source && ` · ${v.source}`} · {shortTime(v.created_at)}
                          </p>
                        </div>
                        {canWrite && (
                          <button
                            disabled={rowBusy === d.id}
                            onClick={() => void onRollback(d, v.version)}
                            className="shrink-0 rounded-md px-1.5 py-1 text-xs text-brand-ink transition-colors  duration-150 hover:bg-brand-soft disabled:opacity-50"
                          >
                            回滚到此版
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

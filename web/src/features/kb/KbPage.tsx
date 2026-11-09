import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { VisibilityBadge, VisibilitySelect, VISIBILITY_HINT, type Visibility } from "@/components/ui/visibility";
import { BatchImport } from "@/features/kb/BatchImport";
import { cn } from "@/lib/utils";

interface KBItem {
  id: string;
  name: string;
  description: string;
  visibility: string;
  owner_id: string;
  mine: boolean;
  document_count: number;
}

interface DocItem {
  id: string;
  name: string;
  chunk_count: number;
  version: number;
  source: string;
  status: string;
  updated_at: string;
}

interface ChunkItem {
  id: string;
  seq: number;
  text: string;
  chars: number;
  char_start: number;
  char_end: number;
  embedding_kind: string;
  has_vector: boolean;
  parent_seq: number | null;
}

interface DocVersion {
  version: number;
  chunk_count: number;
  chars: number;
  source: string;
  note: string;
  created_at: string;
}

interface SearchHit {
  chunk_id: string;
  document_name: string;
  text: string;
  score: number;
  vector_score: number | null;
  lexical_score: number | null;
}

type SearchMode = "hybrid" | "vector" | "lexical";

interface CompareResult {
  query: string;
  results: Partial<Record<SearchMode, SearchHit[]>>;
  timings_ms: Record<string, number>;
}

const MODE_LABEL: Record<SearchMode, string> = {
  hybrid: "混合",
  vector: "向量",
  lexical: "词法",
};

/** 从失败响应里取出可读原因；后端详情是纯文本时给出兜底文案。 */
async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* 非 JSON 响应 */
  }
  return `请求失败（HTTP ${res.status}）`;
}

function shortTime(iso: string): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

export function KbPage() {
  const [kbs, setKbs] = useState<KBItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [newKbName, setNewKbName] = useState("");
  const [newKbDesc, setNewKbDesc] = useState("");
  const [newKbVisibility, setNewKbVisibility] = useState<Visibility>("private");
  const [createOpen, setCreateOpen] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [docName, setDocName] = useState("");
  const [docContent, setDocContent] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [compare, setCompare] = useState<CompareResult | null>(null);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // ---- 文档管理：切片编辑 / 版本回滚 / 单篇重建 ----
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [panel, setPanel] = useState<"chunks" | "versions">("chunks");
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [chunkDrafts, setChunkDrafts] = useState<Record<string, string>>({});
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const [panelBusy, setPanelBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState<string | null>(null);

  const currentKb = kbs.find((k) => k.id === selected) ?? null;

  const loadKbs = useCallback(async () => {
    const res = await fetch("/api/kb");
    const data: KBItem[] = await res.json();
    setKbs(data);
    setSelected((prev) => prev ?? data[0]?.id ?? null);
  }, []);

  useEffect(() => {
    void loadKbs();
  }, [loadKbs]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  /** 导入结束后刷新文档与库计数 */
  const refreshDocs = useCallback(async () => {
    if (!selected) return;
    const [docsRes, kbsRes] = await Promise.all([
      fetch(`/api/kb/${selected}/documents`).then((r) => r.json()),
      fetch("/api/kb").then((r) => r.json()),
    ]);
    const nextDocs = docsRes as DocItem[];
    setDocs(nextDocs);
    setKbs(kbsRes as KBItem[]);
    // 文档已被删除时收起展开的面板，避免停留在不存在的目标上
    setOpenDoc((prev) => (prev && nextDocs.some((d) => d.id === prev) ? prev : null));
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    setHits(null);
    setError(null);
    setOpenDoc(null);
    void refreshDocs();
  }, [selected, refreshDocs]);

  const createKb = async () => {
    if (!newKbName.trim()) return;
    setBusy(true);
    setCreateError(null);
    try {
      const res = await fetch("/api/kb", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newKbName.trim(),
          description: newKbDesc.trim(),
          visibility: newKbVisibility,
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setNewKbName("");
      setNewKbDesc("");
      setNewKbVisibility("private");
      setCreateOpen(false);
      setNotice("知识库已创建");
      await loadKbs();
    } catch (e) {
      setCreateError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** 创建者改自己的库：名称 / 描述 / 可见范围走同一个 PATCH */
  const patchKb = async (kb: KBItem, body: { name?: string; description?: string; visibility?: string }) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${kb.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await readError(res));
      await loadKbs();
      if (body.visibility) setNotice(`「${kb.name}」可见范围已改为 ${VISIBILITY_HINT[body.visibility as Visibility]}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const addDoc = async () => {
    if (!selected || !docName.trim() || !docContent.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: docName.trim(), content: docContent }),
      });
      // 嵌入服务不可用时后端回 503，必须把原因显示出来，不能静默清空输入
      if (!res.ok) throw new Error(await readError(res));
      setDocName("");
      setDocContent("");
      await refreshDocs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const loadChunks = useCallback(
    async (docId: string) => {
      if (!selected) return;
      setPanelBusy(true);
      try {
        const res = await fetch(`/api/kb/${selected}/documents/${docId}/chunks`);
        if (!res.ok) throw new Error(await readError(res));
        const data = (await res.json()) as ChunkItem[];
        setChunks(data);
        setChunkDrafts(Object.fromEntries(data.map((c) => [c.id, c.text])));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPanelBusy(false);
      }
    },
    [selected],
  );

  const loadVersions = useCallback(
    async (docId: string) => {
      if (!selected) return;
      setPanelBusy(true);
      try {
        const res = await fetch(`/api/kb/${selected}/documents/${docId}/versions`);
        if (!res.ok) throw new Error(await readError(res));
        setVersions((await res.json()) as DocVersion[]);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPanelBusy(false);
      }
    },
    [selected],
  );

  const togglePanel = async (doc: DocItem, next: "chunks" | "versions") => {
    if (openDoc === doc.id && panel === next) {
      setOpenDoc(null);
      return;
    }
    setOpenDoc(doc.id);
    setPanel(next);
    setChunks([]);
    setVersions([]);
    if (next === "chunks") await loadChunks(doc.id);
    else await loadVersions(doc.id);
  };

  const saveChunk = async (chunk: ChunkItem) => {
    if (!selected) return;
    const text = (chunkDrafts[chunk.id] ?? "").trim();
    if (!text) {
      setError("切片文本不能为空");
      return;
    }
    setRowBusy(chunk.id);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/chunks/${chunk.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const saved = (await res.json()) as { id: string; text: string; chars: number };
      setChunks((prev) =>
        prev.map((c) => (c.id === saved.id ? { ...c, text: saved.text, chars: saved.chars } : c)),
      );
      setChunkDrafts((prev) => ({ ...prev, [saved.id]: saved.text }));
      setNotice(`切片 #${chunk.seq} 已保存并重嵌入`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  };

  const removeDoc = async (doc: DocItem) => {
    if (!selected) return;
    if (
      !window.confirm(`删除文档「${doc.name}」？其 ${doc.chunk_count} 个切片将一并删除，不可恢复。`)
    )
      return;
    setRowBusy(doc.id);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/documents/${doc.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await readError(res));
      const data = (await res.json()) as { ok: boolean; removed_chunks: number };
      setNotice(`已删除「${doc.name}」，清理 ${data.removed_chunks} 个切片`);
      await refreshDocs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  };

  const reindexDoc = async (doc: DocItem) => {
    if (!selected) return;
    setRowBusy(doc.id);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/documents/${doc.id}/reindex`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      const data = (await res.json()) as { ok: boolean; chunk_count: number };
      setNotice(`「${doc.name}」已重建，共 ${data.chunk_count} 个切片`);
      await refreshDocs();
      if (openDoc === doc.id && panel === "chunks") await loadChunks(doc.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  };

  const rollbackDoc = async (doc: DocItem, version: number) => {
    if (!selected) return;
    if (
      !window.confirm(
        `回滚到 v${version}？该版本内容会作为新版本重新入库，历史版本不会丢失。`,
      )
    )
      return;
    setRowBusy(doc.id);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/documents/${doc.id}/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = (await res.json()) as {
        version: number;
        chunk_count: number;
        rolled_back_to: number;
      };
      setNotice(`已回滚到 v${data.rolled_back_to}，当前 v${data.version}（${data.chunk_count} 个切片）`);
      await refreshDocs();
      await loadVersions(doc.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  };

  const runSearch = async () => {
    if (!selected || !query.trim()) return;
    setBusy(true);
    setError(null);
    setCompare(null);
    try {
      const res = await fetch("/api/kb/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kb_ids: [selected], query: query.trim(), top_k: 4, mode }),
      });
      if (!res.ok) {
        setHits(null);
        throw new Error(await readError(res));
      }
      setHits((await res.json()) as SearchHit[]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false); // 无论成功失败都要复位，否则按钮永久 disabled
    }
  };

  // 多策略并排对比：同一 query 在各检索模式下的召回 diff 与耗时
  const runCompare = async () => {
    if (!selected || !query.trim()) return;
    setBusy(true);
    setError(null);
    setHits(null);
    try {
      const res = await fetch("/api/kb/search_compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kb_ids: [selected],
          query: query.trim(),
          top_k: 4,
          modes: ["vector", "lexical", "hybrid"],
        }),
      });
      if (!res.ok) {
        setCompare(null);
        throw new Error(await readError(res));
      }
      setCompare((await res.json()) as CompareResult);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const removeKb = async (kb: KBItem) => {
    if (
      !window.confirm(
        `删除知识库「${kb.name}」？其 ${kb.document_count} 篇文档与全部切片将一并删除，不可恢复。`,
      )
    )
      return;
    setBusy(true);
    const res = await fetch(`/api/kb/${kb.id}`, { method: "DELETE" });
    setBusy(false);
    if (!res.ok) {
      window.alert("删除失败");
      return;
    }
    setSelected(null);
    setHits(null);
    await loadKbs();
  };

  return (
    <div className="flex h-full">
      {/* 知识库列表 */}
      <aside className="w-72 shrink-0 overflow-y-auto border-r border-line bg-surface-2 p-4">
        <h2 className="mb-3 text-[13px] font-medium">知识库</h2>
        <div className="space-y-1">
          {kbs.map((kb) => (
            <div key={kb.id} className="flex items-center gap-1">
              <button
                onClick={() => setSelected(kb.id)}
                className={cn(
                  "min-w-0 flex-1 rounded-lg px-3 py-2 text-left transition-colors duration-150",
                  selected === kb.id
                    ? "bg-brand-soft text-brand"
                    : "text-secondary hover:bg-surface-3 hover:text-primary",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 truncate text-[13px]">{kb.name}</span>
                  <VisibilityBadge value={kb.visibility} className="shrink-0" />
                </div>
                <div className="text-xs opacity-70">
                  {kb.document_count} 篇文档{kb.mine ? "" : " · 共享"}
                </div>
              </button>
              {selected === kb.id && (
                <button
                  title="删除知识库"
                  onClick={() => void removeKb(kb)}
                  className="shrink-0 rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger"
                >
                  删除
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="mt-4">
          <Button variant="outline" className="w-full" onClick={() => setCreateOpen(true)}>
            新建知识库
          </Button>
        </div>
      </aside>

      {/* 详情区 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex h-full items-center justify-center text-sm text-tertiary">
            选择或创建一个知识库开始
          </div>
        ) : (
          <div className="mx-auto max-w-[860px] space-y-6 px-8 py-8">
            {currentKb && (
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-[15px] font-medium">{currentKb.name}</h2>
                    <VisibilityBadge value={currentKb.visibility} />
                  </div>
                  {currentKb.description && (
                    <p className="mt-1 text-[12.5px] text-secondary">{currentKb.description}</p>
                  )}
                </div>
                {currentKb.mine && (
                  <label className="flex shrink-0 items-center gap-2 text-xs text-tertiary">
                    可见范围
                    <VisibilitySelect
                      value={currentKb.visibility}
                      disabled={busy}
                      onChange={(v) => void patchKb(currentKb, { visibility: v })}
                    />
                  </label>
                )}
              </div>
            )}
            {notice && (
              <div className="rounded-lg border border-brand/40 bg-brand-soft px-3.5 py-2.5 text-[12.5px] text-brand">
                {notice}
              </div>
            )}
            {error && (
              <div className="flex items-start justify-between gap-3 rounded-lg border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[12.5px] text-danger">
                <span className="min-w-0 break-words">{error}</span>
                <button
                  onClick={() => setError(null)}
                  className="shrink-0 text-[11px] opacity-70 transition-opacity hover:opacity-100"
                >
                  关闭
                </button>
              </div>
            )}
            {/* 添加文档 */}
            <Card className="p-5">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-[13px] font-medium">添加文档</h3>
                <span className="text-[11.5px] text-tertiary">可整目录导入</span>
              </div>
              <div className="mt-3">
                <BatchImport kbId={selected} onFinished={() => void refreshDocs()} />
              </div>
            </Card>

            {/* 手动粘贴：保留小文本快速验证的场景 */}
            <Card className="p-5">
              <details>
                <summary className="cursor-pointer text-[13px] font-medium">手动粘贴文本</summary>
                <div className="mt-3 flex items-center gap-3">
                  <input
                    value={docName}
                    onChange={(e) => setDocName(e.target.value)}
                    placeholder="文档名称"
                    className="h-9 w-48 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                  />
                </div>
                <textarea
                  value={docContent}
                  onChange={(e) => setDocContent(e.target.value)}
                  placeholder="粘贴文档内容，将自动切片并向量化…"
                  rows={4}
                  className="mt-3 w-full resize-y rounded-lg border border-line bg-surface-1 p-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                />
                <div className="mt-3 flex justify-end">
                  <Button
                    disabled={busy || !docName.trim() || !docContent.trim()}
                    onClick={() => void addDoc()}
                  >
                    入库
                  </Button>
                </div>
              </details>
            </Card>

            {/* 文档列表 */}
            <Card className="p-5">
              <h3 className="text-[13px] font-medium">文档（{docs.length}）</h3>
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
                              <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10.5px] text-tertiary">
                                v{d.version}
                              </span>
                            </div>
                            <div className="text-xs text-tertiary">
                              {d.chunk_count} 个切片
                              {d.source && ` · ${d.source}`}
                              {d.updated_at && ` · ${shortTime(d.updated_at)}`}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100">
                            <button
                              onClick={() => void togglePanel(d, "chunks")}
                              className={cn(
                                "rounded-md px-1.5 py-1 text-xs transition-colors duration-150 hover:bg-surface-1 hover:text-primary",
                                expanded && panel === "chunks" ? "text-brand" : "text-tertiary",
                              )}
                            >
                              切片
                            </button>
                            <button
                              onClick={() => void togglePanel(d, "versions")}
                              className={cn(
                                "rounded-md px-1.5 py-1 text-xs transition-colors duration-150 hover:bg-surface-1 hover:text-primary",
                                expanded && panel === "versions" ? "text-brand" : "text-tertiary",
                              )}
                            >
                              版本
                            </button>
                            <button
                              disabled={rowBusy === d.id}
                              onClick={() => void reindexDoc(d)}
                              className="rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-surface-1 hover:text-primary disabled:opacity-50"
                            >
                              {rowBusy === d.id ? "处理中…" : "重建"}
                            </button>
                            <button
                              disabled={rowBusy === d.id}
                              onClick={() => void removeDoc(d)}
                              className="rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                            >
                              删除
                            </button>
                          </div>
                        </div>

                        {expanded && panel === "chunks" && (
                          <div className="mx-3 mb-2 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
                            <div className="flex items-center justify-between text-[11.5px] text-tertiary">
                              <span>切片（{chunks.length}）· 修改保存后会单条重嵌入</span>
                              <button
                                onClick={() => setOpenDoc(null)}
                                className="transition-colors hover:text-secondary"
                              >
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
                                  <div className="flex items-center justify-between gap-2 text-[11px] text-tertiary">
                                    <span>
                                      #{c.seq} · {draft.trim().length} 字
                                      {c.parent_seq != null && ` · 父片 #${c.parent_seq}`}
                                      {!c.has_vector && " · 无向量"}
                                    </span>
                                    <div className="flex items-center gap-1">
                                      {dirty && (
                                        <button
                                          onClick={() =>
                                            setChunkDrafts((prev) => ({ ...prev, [c.id]: c.text }))
                                          }
                                          className="rounded-md px-1.5 py-0.5 transition-colors hover:bg-surface-3 hover:text-secondary"
                                        >
                                          还原
                                        </button>
                                      )}
                                      <button
                                        disabled={!dirty || !draft.trim() || rowBusy === c.id}
                                        onClick={() => void saveChunk(c)}
                                        className="rounded-md px-1.5 py-0.5 text-brand transition-colors hover:bg-brand-soft disabled:opacity-40"
                                      >
                                        {rowBusy === c.id ? "保存中…" : "保存"}
                                      </button>
                                    </div>
                                  </div>
                                  <textarea
                                    value={draft}
                                    onChange={(e) =>
                                      setChunkDrafts((prev) => ({ ...prev, [c.id]: e.target.value }))
                                    }
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
                            <div className="flex items-center justify-between text-[11.5px] text-tertiary">
                              <span>版本历史 · 当前 v{d.version}</span>
                              <button
                                onClick={() => setOpenDoc(null)}
                                className="transition-colors hover:text-secondary"
                              >
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
                                      <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[10.5px] text-brand">
                                        当前
                                      </span>
                                    )}
                                  </p>
                                  <p className="truncate text-[11px] text-tertiary">
                                    {v.chunk_count} 切片 · {v.chars} 字
                                    {v.source && ` · ${v.source}`} · {shortTime(v.created_at)}
                                  </p>
                                </div>
                                <button
                                  disabled={rowBusy === d.id}
                                  onClick={() => void rollbackDoc(d, v.version)}
                                  className="shrink-0 rounded-md px-1.5 py-1 text-xs text-brand transition-colors duration-150 hover:bg-brand-soft disabled:opacity-50"
                                >
                                  回滚到此版
                                </button>
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

            {/* 检索测试台 */}
            <Card className="p-5">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-medium">检索测试台</h3>
                <div className="flex gap-1">
                  {(Object.keys(MODE_LABEL) as SearchMode[]).map((m) => (
                    <button
                      key={m}
                      onClick={() => setMode(m)}
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-xs transition-colors duration-150",
                        mode === m
                          ? "border-brand bg-brand-soft text-brand"
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
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void runSearch()}
                  placeholder="输入测试 query，查看召回效果"
                  className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                />
                <Button disabled={busy || !query.trim()} onClick={() => void runSearch()}>
                  检索
                </Button>
                <Button variant="outline" disabled={busy || !query.trim()} onClick={() => void runCompare()}>
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
                  {(Object.keys(compare.results) as SearchMode[]).map((m) => {
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
                    onClick={() => setCompare(null)}
                    className="text-xs text-tertiary transition-colors hover:text-secondary"
                  >
                    收起对比
                  </button>
                </div>
              )}
            </Card>
          </div>
        )}
      </div>

      {/* 新建知识库 */}
      {createOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
          onClick={() => setCreateOpen(false)}
        >
          <Card
            className="w-full max-w-[420px] space-y-3 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-[13px] font-medium">新建知识库</h3>
            <input
              autoFocus
              value={newKbName}
              onChange={(e) => setNewKbName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void createKb()}
              placeholder="知识库名称"
              className="h-9 w-full rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
            />
            <textarea
              value={newKbDesc}
              onChange={(e) => setNewKbDesc(e.target.value)}
              placeholder="描述（可选）"
              rows={2}
              className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
            />
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-tertiary">可见范围</span>
                <VisibilitySelect value={newKbVisibility} onChange={setNewKbVisibility} />
              </div>
              <span className="text-[11.5px] text-tertiary">{VISIBILITY_HINT[newKbVisibility]}</span>
            </div>
            {createError && <p className="text-xs text-danger">{createError}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button disabled={busy || !newKbName.trim()} onClick={() => void createKb()}>
                创建
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

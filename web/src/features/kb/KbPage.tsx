import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { BatchImport } from "@/features/kb/BatchImport";
import { cn } from "@/lib/utils";

interface KBItem {
  id: string;
  name: string;
  document_count: number;
}

interface DocItem {
  id: string;
  name: string;
  chunk_count: number;
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

export function KbPage() {
  const [kbs, setKbs] = useState<KBItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [newKbName, setNewKbName] = useState("");
  const [docName, setDocName] = useState("");
  const [docContent, setDocContent] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadKbs = useCallback(async () => {
    const res = await fetch("/api/kb");
    const data: KBItem[] = await res.json();
    setKbs(data);
    setSelected((prev) => prev ?? data[0]?.id ?? null);
  }, []);

  useEffect(() => {
    void loadKbs();
  }, [loadKbs]);

  /** 导入结束后刷新文档与库计数 */
  const refreshDocs = useCallback(async () => {
    if (!selected) return;
    const [docsRes, kbsRes] = await Promise.all([
      fetch(`/api/kb/${selected}/documents`).then((r) => r.json()),
      fetch("/api/kb").then((r) => r.json()),
    ]);
    setDocs(docsRes as DocItem[]);
    setKbs(kbsRes as KBItem[]);
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    setHits(null);
    setError(null);
    void refreshDocs();
  }, [selected, refreshDocs]);

  const createKb = async () => {
    if (!newKbName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/kb", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newKbName.trim() }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setNewKbName("");
      await loadKbs();
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
      setDocs(await (await fetch(`/api/kb/${selected}/documents`)).json());
      await loadKbs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runSearch = async () => {
    if (!selected || !query.trim()) return;
    setBusy(true);
    setError(null);
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
                <div className="truncate text-[13px]">{kb.name}</div>
                <div className="text-xs opacity-70">{kb.document_count} 篇文档</div>
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
        <div className="mt-4 flex gap-2">
          <input
            value={newKbName}
            onChange={(e) => setNewKbName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void createKb()}
            placeholder="新建知识库名称"
            className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
          />
          <Button disabled={busy || !newKbName.trim()} onClick={() => void createKb()}>
            新建
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
                  {docs.map((d) => (
                    <div
                      key={d.id}
                      className="flex items-center justify-between rounded-lg px-3 py-2 text-[13px] hover:bg-surface-3"
                    >
                      <span className="text-primary">{d.name}</span>
                      <span className="text-xs text-tertiary">{d.chunk_count} 个切片</span>
                    </div>
                  ))}
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
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

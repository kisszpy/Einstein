import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
  const [busy, setBusy] = useState(false);

  const loadKbs = useCallback(async () => {
    const res = await fetch("/api/kb");
    const data: KBItem[] = await res.json();
    setKbs(data);
    if (!selected && data.length > 0) setSelected(data[0]!.id);
  }, [selected]);

  useEffect(() => {
    void loadKbs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    setHits(null);
    void fetch(`/api/kb/${selected}/documents`)
      .then((r) => r.json())
      .then(setDocs);
  }, [selected]);

  const createKb = async () => {
    if (!newKbName.trim()) return;
    setBusy(true);
    await fetch("/api/kb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newKbName.trim() }),
    });
    setNewKbName("");
    await loadKbs();
    setBusy(false);
  };

  const addDoc = async () => {
    if (!selected || !docName.trim() || !docContent.trim()) return;
    setBusy(true);
    await fetch(`/api/kb/${selected}/documents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: docName.trim(), content: docContent }),
    });
    setDocName("");
    setDocContent("");
    setDocs(
      await (await fetch(`/api/kb/${selected}/documents`)).json(),
    );
    await loadKbs();
    setBusy(false);
  };

  const uploadFile = async (file: File) => {
    if (!selected) return;
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    await fetch(`/api/kb/${selected}/documents/upload`, {
      method: "POST",
      body: form,
    });
    setDocs(await (await fetch(`/api/kb/${selected}/documents`)).json());
    await loadKbs();
    setBusy(false);
  };

  const runSearch = async () => {
    if (!selected || !query.trim()) return;
    setBusy(true);
    const res = await fetch("/api/kb/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_ids: [selected], query: query.trim(), top_k: 4 }),
    });
    setHits(await res.json());
    setBusy(false);
  };

  return (
    <div className="flex h-full">
      {/* 知识库列表 */}
      <aside className="w-72 shrink-0 overflow-y-auto border-r border-line bg-surface-2 p-4">
        <h2 className="mb-3 text-[13px] font-medium">知识库</h2>
        <div className="space-y-1">
          {kbs.map((kb) => (
            <button
              key={kb.id}
              onClick={() => setSelected(kb.id)}
              className={cn(
                "block w-full rounded-lg px-3 py-2 text-left transition-colors duration-150",
                selected === kb.id
                  ? "bg-brand-soft text-brand"
                  : "text-secondary hover:bg-surface-3 hover:text-primary",
              )}
            >
              <div className="text-[13px]">{kb.name}</div>
              <div className="text-xs opacity-70">{kb.document_count} 篇文档</div>
            </button>
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
            {/* 添加文档 */}
            <Card className="p-5">
              <h3 className="text-[13px] font-medium">添加文档</h3>
              <div className="mt-3 flex items-center gap-3">
                <input
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="文档名称"
                  className="h-9 w-48 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                />
                <label className="cursor-pointer rounded-lg border border-line px-3 py-2 text-[13px] text-secondary transition-colors duration-150 hover:border-line-strong hover:text-primary">
                  上传文档（PDF / Word / MD / TXT）
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt,.md,.markdown"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void uploadFile(f);
                      e.target.value = "";
                    }}
                  />
                </label>
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
              <h3 className="text-[13px] font-medium">检索测试台</h3>
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
                        <div className="flex items-center justify-between text-xs text-secondary">
                          <span>{h.document_name}</span>
                          <span>score {h.score.toFixed(4)}</span>
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

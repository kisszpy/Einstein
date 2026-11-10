import { useCallback, useEffect, useState } from "react";

import {
  type BatchRefreshResult,
  type StaleResult,
  readError,
} from "@/features/kb/kbShared";
import type {
  ChunkItem,
  CompareResult,
  DocItem,
  DocVersion,
  KBItem,
  SearchHit,
  SearchMode,
} from "@/features/kb/kb-types";
import { confirmDialog } from "@/lib/confirm";
import type { Visibility } from "@/components/ui/visibility";
import { VISIBILITY_HINT } from "@/components/ui/visibility";

/**
 * 知识库工作台的全部状态与副作用。
 *
 * 从 KbPage 里抽出来的唯一目的：让页面只剩「布局」。这个页面承载了
 * 7 种互相独立的操作（建库/改库/导文档/切片编辑/版本回滚/来源重抓/检索测试），
 * 每种的 busy、error、确认框都不同，混在 JSX 里既没法单测也没法一眼看出
 * 哪个操作漏了复位 busy。
 */
export function useKbWorkspace() {
  const [kbs, setKbs] = useState<KBItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [newKbName, setNewKbName] = useState("");
  const [newKbDesc, setNewKbDesc] = useState("");
  const [newKbVisibility, setNewKbVisibility] = useState<Visibility>("private");
  const [createOpen, setCreateOpen] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  /** 对外分享面板：开了才渲染，避免空 Dialog 抢焦点 */
  const [shareOpen, setShareOpen] = useState(false);
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
  // ---- 来源同步：批量重抓与结果摘要 ----
  const [batchBusy, setBatchBusy] = useState<"stale" | "all" | null>(null);
  const [batchResult, setBatchResult] = useState<BatchRefreshResult | null>(null);
  // ---- 授权面板 ----
  const [grantsOpen, setGrantsOpen] = useState(false);

  const currentKb = kbs.find((k) => k.id === selected) ?? null;
  // 只读库隐藏一切写操作；字段缺失时按可写处理，避免后端旧响应把页面锁死
  const canWrite = currentKb ? currentKb.my_role !== "read" : false;

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
    setGrantsOpen(false);
    setBatchResult(null);
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
      if (body.visibility) {
        setNotice(
          `「${kb.name}」可见范围已改为 ${VISIBILITY_HINT[body.visibility as Visibility]}`,
        );
      }
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
    const ok = await confirmDialog({
      title: "删除文档",
      message: `删除文档「${doc.name}」？其 ${doc.chunk_count} 个切片将一并删除，不可恢复。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
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

  /** 单篇重抓：失败后端回 400，detail 即原因。 */
  const refreshDoc = async (doc: DocItem) => {
    if (!selected) return;
    setRowBusy(doc.id);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${selected}/documents/${doc.id}/refresh`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      const data = (await res.json()) as { changed: boolean; version: number };
      if (data.changed) {
        setNotice(`「${doc.name}」已更新到第 ${data.version} 版`);
      } else {
        setNotice(`「${doc.name}」内容未变（已更新抓取时间）`);
      }
      await refreshDocs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRowBusy(null);
    }
  };

  /**
   * 批量重抓。stale_only 时先查过期清单，0 条直接提示不发请求；
   * 批量接口里单篇失败不会 4xx，结果看 items[].error。
   */
  const refreshBatch = async (staleOnly: boolean) => {
    if (!selected) return;
    setBatchBusy(staleOnly ? "stale" : "all");
    setError(null);
    setBatchResult(null);
    try {
      if (staleOnly) {
        const staleRes = await fetch(`/api/kb/${selected}/documents/stale?days=30`);
        if (!staleRes.ok) throw new Error(await readError(staleRes));
        const stale = (await staleRes.json()) as StaleResult;
        if (stale.count === 0) {
          setNotice("没有过期文档");
          return;
        }
      }
      const res = await fetch(`/api/kb/${selected}/documents/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: [], stale_only: staleOnly }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setBatchResult((await res.json()) as BatchRefreshResult);
      await refreshDocs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBatchBusy(null);
    }
  };

  const rollbackDoc = async (doc: DocItem, version: number) => {
    if (!selected) return;
    const ok = await confirmDialog({
      title: "回滚版本",
      message: `回滚到 v${version}？该版本内容会作为新版本重新入库，历史版本不会丢失。`,
      confirmLabel: "回滚",
    });
    if (!ok) return;
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
      setNotice(
        `已回滚到 v${data.rolled_back_to}，当前 v${data.version}（${data.chunk_count} 个切片）`,
      );
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
    const ok = await confirmDialog({
      title: "删除知识库",
      message: `删除知识库「${kb.name}」？其 ${kb.document_count} 篇文档与全部切片将一并删除，不可恢复。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    const res = await fetch(`/api/kb/${kb.id}`, { method: "DELETE" });
    setBusy(false);
    if (!res.ok) {
      setError(await readError(res));
      return;
    }
    setSelected(null);
    setHits(null);
    await loadKbs();
  };

  return {
    // 数据
    kbs,
    selected,
    setSelected,
    docs,
    currentKb,
    canWrite,
    // 新建库弹窗
    newKbName,
    setNewKbName,
    newKbDesc,
    setNewKbDesc,
    newKbVisibility,
    setNewKbVisibility,
    createOpen,
    setCreateOpen,
    createError,
    createKb,
    // 头部与提示
    busy,
    error,
    setError,
    notice,
    shareOpen,
    setShareOpen,
    grantsOpen,
    setGrantsOpen,
    patchKb,
    removeKb,
    // 手动粘贴
    docName,
    setDocName,
    docContent,
    setDocContent,
    addDoc,
    refreshDocs,
    // 文档面板
    openDoc,
    setOpenDoc,
    panel,
    chunks,
    chunkDrafts,
    setChunkDrafts,
    versions,
    panelBusy,
    rowBusy,
    togglePanel,
    saveChunk,
    removeDoc,
    reindexDoc,
    refreshDoc,
    rollbackDoc,
    // 批量重抓
    batchBusy,
    batchResult,
    setBatchResult,
    refreshBatch,
    // 检索台
    query,
    setQuery,
    mode,
    setMode,
    hits,
    compare,
    setCompare,
    runSearch,
    runCompare,
  };
}

export type KbWorkspace = ReturnType<typeof useKbWorkspace>;

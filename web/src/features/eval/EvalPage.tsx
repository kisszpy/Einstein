import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface EvalItem {
  query: string;
  expected_doc: string;
  expected_contains: string[];
}

interface EvalSet {
  id: string;
  kb_id: string;
  name: string;
  items: EvalItem[];
  created_at: string;
}

interface KB {
  id: string;
  name: string;
}

interface Report {
  set_id: string;
  name: string;
  ran_at: string;
  duration_ms: number;
  total: number;
  recall_at_1: number;
  recall_at_k: number;
  mrr: number;
  answer_pass_rate: number | null;
  items: {
    query: string;
    expected_doc: string;
    expected_contains: string[];
    rank: number | null;
    hit: boolean;
    sources: string[];
    answer: string;
    answer_pass: boolean | null;
  }[];
}

const EMPTY_ITEM = { query: "", expected_doc: "", expected_contains: "" };

export function EvalPage() {
  const [kbs, setKbs] = useState<KB[]>([]);
  const [sets, setSets] = useState<EvalSet[]>([]);
  const [selectedKb, setSelectedKb] = useState<string>("");
  const [selectedSet, setSelectedSet] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newItems, setNewItems] = useState([{ ...EMPTY_ITEM }]);
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadKbs = useCallback(async () => {
    const res = await fetch("/api/kb");
    const data: KB[] = await res.json();
    setKbs(data);
    setSelectedKb((prev) => prev || data[0]?.id || "");
  }, []);

  const loadSets = useCallback(async () => {
    const res = await fetch("/api/eval/sets");
    setSets(await res.json());
  }, []);

  useEffect(() => {
    void loadKbs();
    void loadSets();
  }, [loadKbs, loadSets]);

  const kbSets = sets.filter((s) => s.kb_id === selectedKb);

  const addItem = () => setNewItems((items) => [...items, { ...EMPTY_ITEM }]);
  const updateItem = (idx: number, patch: Partial<typeof EMPTY_ITEM>) =>
    setNewItems((items) => items.map((it, i) => (i === idx ? { ...it, ...patch } : it)));

  const create = async () => {
    if (!selectedKb) {
      setError("请先选择知识库");
      return;
    }
    if (!newName.trim()) {
      setError("评测集名称不能为空");
      return;
    }
    const items = newItems
      .filter((it) => it.query.trim())
      .map((it) => ({
        query: it.query.trim(),
        expected_doc: it.expected_doc.trim(),
        expected_contains: it.expected_contains
          .split(/[，,]/)
          .map((s) => s.trim())
          .filter(Boolean),
      }));
    if (items.length === 0) {
      setError("至少需要一条用例（填写问题）");
      return;
    }
    const res = await fetch("/api/eval/sets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: selectedKb, name: newName.trim(), items }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      setError(data?.detail ?? `创建失败：HTTP ${res.status}`);
      return;
    }
    setCreating(false);
    setNewName("");
    setNewItems([{ ...EMPTY_ITEM }]);
    setError(null);
    await loadSets();
  };

  const run = async (setId: string) => {
    setRunning(true);
    setReport(null);
    setError(null);
    try {
      const res = await fetch(`/api/eval/sets/${setId}/run`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.detail ?? `评测失败：HTTP ${res.status}`);
        return;
      }
      setReport(await res.json());
    } finally {
      setRunning(false);
    }
  };

  const remove = async (s: EvalSet) => {
    if (!window.confirm(`删除评测集「${s.name}」？`)) return;
    if (selectedSet === s.id) setSelectedSet("");
    await fetch(`/api/eval/sets/${s.id}`, { method: "DELETE" });
    await loadSets();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[960px] space-y-6 px-8 py-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-lg font-medium">评测</h1>
            <p className="mt-1 text-[13px] text-secondary">
              RAG 回归测试：改提示词、换模型前先跑一遍。检索质量看 Recall / MRR，回答质量看关键词通过率。
            </p>
          </div>
          <Button onClick={() => { setCreating(true); setError(null); }}>新建评测集</Button>
        </div>

        {/* 知识库选择 */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-tertiary">知识库：</span>
          {kbs.length === 0 && <span className="text-xs text-tertiary">暂无知识库</span>}
          {kbs.map((kb) => (
            <button
              key={kb.id}
              onClick={() => { setSelectedKb(kb.id); setSelectedSet(""); setReport(null); }}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs transition-colors duration-150",
                selectedKb === kb.id
                  ? "border-brand bg-brand-soft text-brand"
                  : "border-line text-secondary hover:border-line-strong",
              )}
            >
              {kb.name}
            </button>
          ))}
        </div>

        {/* 新建表单 */}
        {creating && (
          <Card className="space-y-3 p-5">
            <h3 className="text-[13px] font-medium">新建评测集</h3>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="评测集名称，如：产品手册基础问答"
              className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <div className="space-y-2">
              {newItems.map((it, idx) => (
                <div key={idx} className="grid grid-cols-[1.4fr_1fr_1fr] gap-2">
                  <input
                    value={it.query}
                    onChange={(e) => updateItem(idx, { query: e.target.value })}
                    placeholder="测试问题"
                    className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
                  />
                  <input
                    value={it.expected_doc}
                    onChange={(e) => updateItem(idx, { expected_doc: e.target.value })}
                    placeholder="期望命中文档名（子串）"
                    className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
                  />
                  <input
                    value={it.expected_contains}
                    onChange={(e) => updateItem(idx, { expected_contains: e.target.value })}
                    placeholder="回答须含关键词（逗号分隔）"
                    className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
                  />
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={addItem}>
                + 加一条用例
              </Button>
              {error && <p className="text-xs text-danger">{error}</p>}
            </div>
            <div className="flex gap-2">
              <Button onClick={() => void create()}>保存</Button>
              <Button variant="ghost" onClick={() => { setCreating(false); setError(null); }}>
                取消
              </Button>
            </div>
          </Card>
        )}

        {/* 评测集列表 */}
        <div className="space-y-2">
          {kbSets.length === 0 && !creating && (
            <Card className="p-8 text-center text-[13px] text-tertiary">
              当前知识库还没有评测集。用例格式：问题 + 期望命中的文档 + 回答必须包含的关键词。
            </Card>
          )}
          {kbSets.map((s) => (
            <Card key={s.id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-primary">{s.name}</span>
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-tertiary">
                      {s.items.length} 条用例
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-1 text-xs text-tertiary">
                    {s.items.map((it) => it.query).join(" / ")}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    disabled={running}
                    onClick={() => { setSelectedSet(s.id); void run(s.id); }}
                  >
                    {running && selectedSet === s.id ? "评测中…" : "运行评测"}
                  </Button>
                  <Button variant="ghost" onClick={() => void remove(s)}>
                    删除
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>

        {running && (
          <Card className="p-6 text-center text-[13px] text-tertiary">
            评测运行中：逐条检索 + 真模型作答，请稍候（每条约需 1-3 分钟）…
          </Card>
        )}

        {/* 评测报告 */}
        {report && (
          <Card className="space-y-4 p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-medium">
                报告 · {report.name}
                <span className="ml-2 text-xs font-normal text-tertiary">
                  {report.ran_at} UTC · {report.duration_ms} ms
                </span>
              </h3>
            </div>
            <div className="flex gap-3">
              <Metric label="Recall@1" value={String(report.recall_at_1)} />
              <Metric label={`Recall@${3}`} value={String(report.recall_at_k)} />
              <Metric label="MRR" value={String(report.mrr)} />
              <Metric
                label="回答通过率"
                value={report.answer_pass_rate != null ? String(report.answer_pass_rate) : "—"}
              />
            </div>
            <div className="space-y-2">
              {report.items.map((it, idx) => (
                <div key={idx} className="rounded-lg border border-line p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-[13px] text-primary">{it.query}</p>
                    <div className="flex shrink-0 gap-1">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-xs",
                          it.hit ? "bg-brand-soft text-brand" : "bg-danger/10 text-danger",
                        )}
                      >
                        {it.hit ? `检索命中 #${it.rank}` : "检索未命中"}
                      </span>
                      {it.answer_pass != null && (
                        <span
                          className={cn(
                            "rounded px-1.5 py-0.5 text-xs",
                            it.answer_pass ? "bg-brand-soft text-brand" : "bg-danger/10 text-danger",
                          )}
                        >
                          {it.answer_pass ? "回答通过" : "回答未通过"}
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-tertiary">
                    期望文档：{it.expected_doc || "—"} · 实际来源：{it.sources.join("、") || "—"}
                  </p>
                  {it.expected_contains.length > 0 && (
                    <p className="mt-0.5 text-xs text-tertiary">
                      关键词：{it.expected_contains.join("、")}
                    </p>
                  )}
                  <p className="mt-1.5 line-clamp-3 text-xs text-secondary">{it.answer}</p>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex-1 rounded-lg bg-surface-2 p-3">
      <p className="text-xs text-tertiary">{label}</p>
      <p className="mt-0.5 text-lg font-medium text-primary">{value}</p>
    </div>
  );
}

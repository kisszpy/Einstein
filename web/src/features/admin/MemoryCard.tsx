import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface MemoryItem {
  id: string;
  content: string;
  source: string;
  created_at: string;
}

interface FeedbackSummary {
  up: number;
  down: number;
  total: number;
}

/** 长期记忆管理：手动添加/删除跨会话记忆；顺带看对话赞踩汇总。 */
export function MemoryCard() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [draft, setDraft] = useState("");
  const [fb, setFb] = useState<FeedbackSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, f] = await Promise.all([
        fetch("/api/memory?limit=20").then((res) => (res.ok ? res.json() : [])),
        fetch("/api/feedback").then((res) => (res.ok ? res.json() : null)),
      ]);
      setItems(Array.isArray(m) ? m : []);
      if (f) setFb(f.summary as FeedbackSummary);
    } catch {
      /* 加载失败不阻塞页面 */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    const content = draft.trim();
    if (!content) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? `添加失败（HTTP ${res.status}）`);
      }
      setDraft("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    const res = await fetch(`/api/memory/${id}`, { method: "DELETE" });
    if (res.ok) setItems((prev) => prev.filter((m) => m.id !== id));
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-medium">长期记忆</h3>
        {fb && (
          <span className="text-xs text-tertiary">
            对话反馈 👍 {fb.up} · 👎 {fb.down}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-xs text-tertiary">
        保存到这里的事实会在相关对话中自动注入，跨会话生效。
      </p>
      <div className="mt-3 flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
          placeholder="例如：团队代码规范用 4 空格缩进"
          className="h-8 flex-1 rounded-lg border border-line bg-surface-1 px-2.5 text-xs outline-none placeholder:text-tertiary focus:border-brand"
        />
        <Button disabled={busy || !draft.trim()} onClick={() => void add()}>
          添加
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      {items.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {items.map((m) => (
            <li
              key={m.id}
              className="group flex items-start justify-between gap-2 text-xs leading-relaxed"
            >
              <span className="min-w-0 flex-1 text-secondary">{m.content}</span>
              <button
                onClick={() => void remove(m.id)}
                className="shrink-0 text-tertiary opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
                title="删除记忆"
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

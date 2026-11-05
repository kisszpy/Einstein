import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { getWorkspace, setWorkspace, subscribeWorkspace } from "@/lib/workspace";

interface Workspace {
  id: string;
  name: string;
  description: string;
  usage: Record<string, number>;
}

async function fetchWorkspaces(): Promise<Workspace[]> {
  const res = await fetch("/api/workspaces");
  if (!res.ok) throw new Error("加载工作区失败");
  return res.json();
}

export function WorkspaceSwitcher() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const canCreate = user?.role === "owner" || user?.role === "admin";
  const [active, setActive] = useState(getWorkspace());
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const { data: workspaces = [] } = useQuery({
    queryKey: ["workspaces"],
    queryFn: fetchWorkspaces,
    staleTime: 10_000,
  });

  useEffect(() => subscribeWorkspace(setActive), []);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const current = workspaces.find((w) => w.id === active);

  const switchTo = (id: string) => {
    setWorkspace(id);
    setOpen(false);
    // 各页面数据来源不统一（部分用 react-query，部分用 useState + useEffect），
    // 切工作区后整页刷新最稳，避免旧工作区的列表残留在界面上
    window.location.reload();
  };

  const create = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    const res = await fetch("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    setBusy(false);
    if (!res.ok) return;
    const created: Workspace = await res.json();
    setName("");
    await qc.invalidateQueries({ queryKey: ["workspaces"] });
    switchTo(created.id);
  };

  return (
    <div className="relative" ref={boxRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg border border-line bg-surface-1 px-3 py-1.5 text-[13px] text-secondary transition-colors duration-150 hover:border-line-strong hover:text-primary"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-brand" />
        <span className="max-w-[9rem] truncate">{current?.name ?? "默认工作区"}</span>
        <span className="text-tertiary">▾</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-20 mt-2 w-72 rounded-xl border border-line bg-surface-1 p-2 shadow-lg">
          <p className="px-2 pb-1 pt-1 text-[11px] uppercase tracking-wide text-tertiary">
            切换工作区
          </p>
          <div className="max-h-56 overflow-y-auto">
            {workspaces.map((w) => (
              <button
                key={w.id}
                onClick={() => switchTo(w.id)}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors duration-150",
                  w.id === active ? "bg-brand-soft text-brand" : "text-secondary hover:bg-surface-3",
                )}
              >
                <span className="truncate">{w.name}</span>
                <span className="ml-2 shrink-0 text-[11px] text-tertiary">
                  {w.usage.kb} 库 · {w.usage.expert} 专家
                </span>
              </button>
            ))}
          </div>

          <div className="mt-2 flex gap-2 border-t border-line pt-2">
            {canCreate ? (
              <>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void create()}
                  placeholder="新建工作区…"
                  className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-surface-2 px-2 text-[13px] outline-none focus:border-line-strong"
                />
                <button
                  disabled={busy || !name.trim()}
                  onClick={() => void create()}
                  className="h-8 shrink-0 rounded-lg bg-brand px-3 text-[13px] text-white transition-opacity duration-150 disabled:opacity-40"
                >
                  创建
                </button>
              </>
            ) : (
              <p className="px-2 py-1 text-[11.5px] text-tertiary">
                工作区由管理员分配，如需新增请联系管理员。
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { readError, type Grant, type GrantCandidate } from "@/features/kb/kbShared";
import { cn } from "@/lib/utils";

type GrantRole = Grant["role"];

const ROLE_OPTIONS: GrantRole[] = ["read", "write"];
const ROLE_LABEL: Record<GrantRole, string> = { read: "可读", write: "可写" };

const SELECT_CLS =
  "rounded-lg border border-line bg-surface-1 px-2 text-[12.5px] text-primary outline-none focus:border-line-strong disabled:opacity-50 [&>option]:bg-surface-1";

function roleNote(workspaceRole: string): string {
  if (workspaceRole === "owner") return "空间所有者";
  if (workspaceRole === "admin") return "空间管理员";
  return "";
}

export function GrantsPanel({ kbId, onClose }: { kbId: string; onClose: () => void }) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [candidates, setCandidates] = useState<GrantCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [addUserId, setAddUserId] = useState("");
  const [addRole, setAddRole] = useState<GrantRole>("read");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [grantsRes, candRes] = await Promise.all([
        fetch(`/api/kb/${kbId}/grants`),
        fetch(`/api/kb/${kbId}/grants/candidates`),
      ]);
      if (!grantsRes.ok) throw new Error(await readError(grantsRes));
      if (!candRes.ok) throw new Error(await readError(candRes));
      setGrants((await grantsRes.json()) as Grant[]);
      setCandidates((await candRes.json()) as GrantCandidate[]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** 人名优先从候选列表查，查不到（如已移出空间）退回 user_id 前 8 位。 */
  const nameOf = (userId: string): string => {
    const c = candidates.find((x) => x.user_id === userId);
    return c?.name || userId.slice(0, 8);
  };

  /** 新增与改权限走同一个 upsert 接口。 */
  const saveGrant = async (userId: string, role: GrantRole) => {
    setBusyUserId(userId);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${kbId}/grants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, role }),
      });
      if (!res.ok) throw new Error(await readError(res));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyUserId(null);
    }
  };

  const removeGrant = async (userId: string) => {
    setBusyUserId(userId);
    setError(null);
    try {
      const res = await fetch(`/api/kb/${kbId}/grants/${userId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await readError(res));
      setGrants((prev) => prev.filter((g) => g.user_id !== userId));
      setCandidates((prev) =>
        prev.map((c) => (c.user_id === userId ? { ...c, granted: "" as const } : c)),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyUserId(null);
    }
  };

  const addable = candidates.filter((c) => c.granted === "");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      onClick={onClose}
    >
      <Card className="w-full max-w-[460px] p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-[13px] font-medium">共享给成员</h3>
          <button
            onClick={onClose}
            className="text-xs text-tertiary transition-colors hover:text-secondary"
          >
            关闭
          </button>
        </div>
        {error && <p className="mt-2 break-words text-xs text-danger">{error}</p>}
        {loading ? (
          <p className="py-6 text-center text-xs text-tertiary">加载中…</p>
        ) : (
          <>
            <div className="mt-3 space-y-1">
              {grants.length === 0 ? (
                <p className="py-2 text-xs text-tertiary">还没有授权给任何成员</p>
              ) : (
                grants.map((g) => (
                  <div
                    key={g.user_id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-1 px-3 py-2"
                  >
                    <span className="min-w-0 truncate text-[13px] text-primary">
                      {nameOf(g.user_id)}
                    </span>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <select
                        value={g.role}
                        disabled={busyUserId === g.user_id}
                        onChange={(e) => void saveGrant(g.user_id, e.target.value as GrantRole)}
                        className={cn(SELECT_CLS, "h-8 text-xs")}
                      >
                        {ROLE_OPTIONS.map((r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r]}
                          </option>
                        ))}
                      </select>
                      <button
                        disabled={busyUserId === g.user_id}
                        onClick={() => void removeGrant(g.user_id)}
                        className="rounded-md px-1.5 py-1 text-xs text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                      >
                        移除
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
            <div className="mt-4 border-t border-line pt-3">
              <p className="text-xs text-tertiary">添加成员</p>
              <div className="mt-2 flex items-center gap-2">
                <select
                  value={addUserId}
                  onChange={(e) => setAddUserId(e.target.value)}
                  className={cn(SELECT_CLS, "h-9 min-w-0 flex-1")}
                >
                  <option value="">选择成员…</option>
                  {addable.map((c) => {
                    const note = roleNote(c.workspace_role);
                    return (
                      <option key={c.user_id} value={c.user_id}>
                        {c.name}
                        {note && `（${note}）`}
                      </option>
                    );
                  })}
                </select>
                <select
                  value={addRole}
                  onChange={(e) => setAddRole(e.target.value as GrantRole)}
                  className={cn(SELECT_CLS, "h-9")}
                >
                  {ROLE_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
                <Button
                  disabled={!addUserId || busyUserId !== null}
                  onClick={() => {
                    const uid = addUserId;
                    setAddUserId("");
                    void saveGrant(uid, addRole);
                  }}
                >
                  添加
                </Button>
              </div>
              {addable.length === 0 && (
                <p className="mt-1.5 text-[12px] text-tertiary">没有可添加的成员</p>
              )}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

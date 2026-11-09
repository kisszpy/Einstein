/**
 * 空间成员管理：成员清单、改角色、移除、发邀请码。
 *
 * 权限：role >= admin 才能改角色/移除/发码；member 只能看清单。
 * 个人空间没有「成员」概念（只有自己），调用方不应在此打开本面板。
 */

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";

interface SpaceMember {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  is_personal: boolean;
  joined_at: string;
  disabled: boolean;
}

const ROLE_LABEL: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读",
};

const ASSIGNABLE = ["admin", "member", "viewer"];

export function SpaceMembersDialog({
  workspaceId,
  workspaceName,
  myRole,
  onClose,
}: {
  workspaceId: string;
  workspaceName: string;
  myRole: string;
  onClose: () => void;
}) {
  const canManage = myRole === "owner" || myRole === "admin";
  const [members, setMembers] = useState<SpaceMember[]>([]);
  const [inviteRole, setInviteRole] = useState("member");
  const [invite, setInvite] = useState<{ code: string; max_uses: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const res = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/members`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data.detail ?? "加载成员失败");
      return;
    }
    setMembers(await res.json());
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const setRole = async (member: SpaceMember, role: string) => {
    setError("");
    const res = await fetch(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(member.user_id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      },
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "修改角色失败");
      return;
    }
    await load();
  };

  const remove = async (member: SpaceMember) => {
    const ok = await confirmDialog({
      title: "移出成员",
      message: `把 ${member.display_name} 移出「${workspaceName}」？`,
      confirmLabel: "移出",
      danger: true,
    });
    if (!ok) return;
    setError("");
    const res = await fetch(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(member.user_id)}`,
      { method: "DELETE" },
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "移除失败");
      return;
    }
    await load();
  };

  const createInvite = async () => {
    setBusy(true);
    setError("");
    const res = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/invites`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: inviteRole, max_uses: 10 }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setError(data.detail ?? "生成邀请码失败");
      return;
    }
    setInvite(data);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl border border-line bg-surface-1 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-[13.5px] font-medium">「{workspaceName}」成员</h3>
            <p className="mt-0.5 text-[12px] text-tertiary">
              {canManage ? "角色随时可调；移除后该账号立刻失去此空间的数据访问" : "只有空间管理员可以调整成员"}
            </p>
          </div>
          <button onClick={onClose} className="text-tertiary transition-colors hover:text-primary">
            ✕
          </button>
        </div>

        <div className="mt-3 max-h-64 overflow-y-auto">
          {members.map((m) => (
            <div key={m.user_id} className="flex items-center gap-2 border-t border-line py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-secondary">
                  {m.display_name}
                  {m.disabled && <span className="ml-1.5 text-[12px] text-danger">已禁用</span>}
                </p>
                <p className="truncate text-[12px] text-tertiary">{m.username}</p>
              </div>
              {canManage && m.role !== "owner" ? (
                <select
                  value={m.role}
                  onChange={(e) => void setRole(m, e.target.value)}
                  className="h-7 rounded-md border border-line bg-surface-2 px-1.5 text-[12px] outline-none"
                >
                  {ASSIGNABLE.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-tertiary">
                  {ROLE_LABEL[m.role] ?? m.role}
                </span>
              )}
              {canManage && m.role !== "owner" && (
                <button
                  onClick={() => void remove(m)}
                  className="shrink-0 text-[12px] text-tertiary transition-colors hover:text-danger"
                >
                  移除
                </button>
              )}
            </div>
          ))}
          {members.length === 0 && <p className="py-4 text-center text-[12px] text-tertiary">暂无成员</p>}
        </div>

        {canManage && (
          <div className="mt-3 rounded-lg border border-line bg-surface-2 p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-tertiary">邀请新成员</span>
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="h-7 rounded-md border border-line bg-surface-1 px-1.5 text-[12px] outline-none"
              >
                <option value="member">成员</option>
                <option value="viewer">只读</option>
              </select>
              <Button variant="outline" onClick={() => void createInvite()} disabled={busy}>
                生成邀请码
              </Button>
            </div>
            {invite && (
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 rounded bg-surface-3 px-2 py-1 text-[12.5px] text-primary">
                  {invite.code}
                </code>
                <button
                  onClick={() => void navigator.clipboard?.writeText(invite.code)}
                  className="text-[12px] text-tertiary transition-colors hover:text-primary"
                >
                  复制
                </button>
              </div>
            )}
            <p className="mt-1.5 text-[12px] text-tertiary">
              对方在控制台「加入空间」输入即可；角色随码上的设定。
            </p>
          </div>
        )}

        {error && <p className={cn("mt-2 text-[12px] text-danger")}>{error}</p>}
      </div>
    </div>
  );
}

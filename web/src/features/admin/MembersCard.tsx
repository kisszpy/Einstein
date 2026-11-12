import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { confirmDialog, promptDialog } from "@/lib/confirm";
import { cn, shortTime } from "@/lib/utils";

interface Member {
  id: string;
  username: string;
  display_name: string;
  role: string;
  role_label: string;
  workspaces: string[];
  created_at: string;
  last_login_at: string | null;
  disabled: boolean;
}

interface Workspace {
  id: string;
  name: string;
  role?: string;
  is_personal?: boolean;
}

const ROLES = [
  { value: "member", label: "成员" },
  { value: "viewer", label: "只读" },
  { value: "admin", label: "管理员" },
  { value: "owner", label: "所有者" },
];

const inputCls =
  "h-9 w-full rounded-lg border border-line bg-surface-1 px-2.5 text-[13px] outline-none transition-colors duration-150 placeholder:text-tertiary focus:border-brand";

export function MembersCard() {
  const { user } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState({
    username: "",
    display_name: "",
    password: "",
    role: "member",
    // 默认不并入任何共享空间：新账号只拿到自己的个人空间，团队空间靠显式勾选/邀请
    workspaces: [] as string[],
  });

  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const load = useCallback(async () => {
    const [m, w] = await Promise.all([
      fetch("/api/users").then((r) => (r.ok ? r.json() : [])),
      fetch("/api/workspaces").then((r) => (r.ok ? r.json() : [])),
    ]);
    setMembers(m);
    setSpaces(w);
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  if (!isAdmin) return null;

  const create = async () => {
    setError("");
    const res = await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "创建失败");
      return;
    }
    setDraft({ username: "", display_name: "", password: "", role: "member", workspaces: [] });
    setAdding(false);
    await load();
  };

  const toggleDisabled = async (m: Member) => {
    const res = await fetch(`/api/users/${m.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled: !m.disabled }),
    });
    if (res.ok) await load();
  };

  const resetPassword = async (m: Member) => {
    // 明文 prompt 输密码是安全和可用性双输：改带显隐切换的对话框
    const pw = await promptDialog({
      title: `重置 ${m.username} 的密码`,
      message: "至少 6 位；重置后该账号现有会话立即失效。",
      placeholder: "新密码",
      type: "password",
      confirmLabel: "重置",
      validate: (v) => (v.length < 6 ? "密码至少 6 位" : null),
    });
    if (!pw) return;
    const res = await fetch(`/api/users/${m.id}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "重置失败");
      return;
    }
    setNotice(`已重置 ${m.username} 的密码，其现有会话已失效`);
  };

  const remove = async (m: Member) => {
    const ok = await confirmDialog({
      title: "删除成员",
      message: `删除成员 ${m.username}？该账号的会话会立即失效。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    const res = await fetch(`/api/users/${m.id}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "删除失败");
      return;
    }
    await load();
  };

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-medium">成员与权限</h3>
          <p className="mt-0.5 text-[12px] text-tertiary">
            每个账号自动拥有一个私有个人空间（只有自己可见）。勾选下方共享空间才会加入团队；未被加入的空间对该账号完全不可见。
          </p>
        </div>
        <Button variant="outline" className="shrink-0" onClick={() => setAdding((v) => !v)}>
          {adding ? "收起" : "新增成员"}
        </Button>
      </div>

      {adding && (
        <div className="mt-3 rounded-lg border border-line bg-surface-2 p-3">
          <div className="grid grid-cols-2 gap-2.5">
            <input
              className={inputCls}
              placeholder="用户名（登录名）"
              value={draft.username}
              onChange={(e) => setDraft({ ...draft, username: e.target.value })}
            />
            <input
              className={inputCls}
              placeholder="显示名称（可选）"
              value={draft.display_name}
              onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
            />
            <input
              className={inputCls}
              placeholder="初始密码（至少 6 位）"
              value={draft.password}
              onChange={(e) => setDraft({ ...draft, password: e.target.value })}
            />
            <select
              className={inputCls}
              value={draft.role}
              onChange={(e) => setDraft({ ...draft, role: e.target.value })}
            >
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>

          <div className="mt-2.5 flex flex-wrap items-center gap-3">
            <span className="text-[12px] text-tertiary">加入共享空间（可选）</span>
            {spaces.filter((w) => !w.is_personal).map((w) => (
              <label key={w.id} className="flex items-center gap-1.5 text-[12.5px] text-secondary">
                <input
                  type="checkbox"
                  checked={draft.workspaces.includes(w.id)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      workspaces: e.target.checked
                        ? [...draft.workspaces, w.id]
                        : draft.workspaces.filter((id) => id !== w.id),
                    })
                  }
                />
                {w.name}
              </label>
            ))}
          </div>

          {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
          {notice && <p className="mt-2 text-[12px] text-success">{notice}</p>}

          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAdding(false)}>
              取消
            </Button>
            <Button onClick={() => void create()} disabled={!draft.username || draft.password.length < 6}>
              创建
            </Button>
          </div>
        </div>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[560px] text-xs">
          <thead>
            <tr className="text-left text-tertiary">
              <th className="whitespace-nowrap pb-1.5 font-normal">账号</th>
              <th className="whitespace-nowrap pb-1.5 font-normal">角色</th>
              <th className="whitespace-nowrap pb-1.5 font-normal">工作区</th>
              <th className="whitespace-nowrap pb-1.5 font-normal">最近登录</th>
              <th className="whitespace-nowrap pb-1.5 font-normal">状态</th>
              <th className="whitespace-nowrap pb-1.5 text-right font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-t border-line">
                <td className="py-2">
                  <p className="text-secondary">{m.display_name}</p>
                  <p className="text-tertiary">{m.username}</p>
                </td>
                <td className="py-2 text-secondary">{m.role_label}</td>
                <td className="max-w-[180px] truncate py-2 text-tertiary" title={m.workspaces.join(", ")}>
                  {m.workspaces.length} 个
                </td>
                <td className="py-2 tabular-nums text-tertiary">
                  {m.last_login_at ? shortTime(m.last_login_at) : "从未登录"}
                </td>
                <td className="py-2">
                  <span className={cn(m.disabled ? "text-danger" : "text-success")}>
                    {m.disabled ? "已禁用" : "正常"}
                  </span>
                </td>
                <td className="py-2 text-right">
                  <div className="inline-flex items-center gap-1">
                    <button
                      onClick={() => void resetPassword(m)}
                      className="rounded-md px-2 py-1.5 text-[12.5px] text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
                    >
                      改密码
                    </button>
                    <button
                      onClick={() => void toggleDisabled(m)}
                      disabled={m.id === user?.id}
                      className="rounded-md px-2 py-1.5 text-[12.5px] text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary disabled:opacity-40"
                    >
                      {m.disabled ? "启用" : "禁用"}
                    </button>
                    <button
                      onClick={() => void remove(m)}
                      disabled={m.id === user?.id}
                      className="rounded-md px-2 py-1.5 text-[12.5px] text-danger/80 transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-40"
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {members.length === 0 && (
              <tr>
                <td colSpan={6} className="py-4 text-center text-tertiary">
                  暂无成员
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

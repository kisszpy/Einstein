import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface Invite {
  code: string;
  ws_id: string;
  role: string;
  uses: number;
  max_uses: number;
  created_at: string;
}

const ROLE_LABEL: Record<string, string> = {
  member: "成员",
  viewer: "只读",
  admin: "管理员",
};

/** 邀请码管理：生成 → 复制发给同事 → 对方在登录后凭码加入工作区。 */
export function InvitesCard() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/workspaces/default/invites");
      if (res.ok) setInvites((await res.json()) as Invite[]);
    } catch {
      /* 列表加载失败不阻塞页面 */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/workspaces/default/invites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, max_uses: 10 }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? `生成失败（HTTP ${res.status}）`);
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      /* 剪贴板不可用就忽略，码本身仍可见可手动复制 */
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-medium">邀请码</h3>
        <div className="flex items-center gap-2">
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="h-8 rounded-lg border border-line bg-surface-1 px-2 text-xs outline-none focus:border-brand"
          >
            <option value="member">成员</option>
            <option value="viewer">只读</option>
          </select>
          <Button disabled={busy} onClick={() => void create()}>
            生成邀请码
          </Button>
        </div>
      </div>
      <p className="mt-1.5 text-xs text-tertiary">
        新同事注册登录后，在「我的」页凭邀请码加入本工作区；码可重复使用，直到用尽次数。
      </p>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      {invites.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {invites.slice(0, 8).map((inv) => (
            <li key={inv.code} className="flex items-center justify-between gap-2 text-xs">
              <button
                onClick={() => void copy(inv.code)}
                title="点击复制"
                className="truncate rounded bg-surface-3 px-2 py-1 font-mono text-secondary transition-colors hover:text-primary"
              >
                {copied === inv.code ? "已复制" : inv.code}
              </button>
              <span className="shrink-0 text-tertiary">
                {ROLE_LABEL[inv.role] ?? inv.role} · {inv.uses}/{inv.max_uses} 已用
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { logout, useAuth } from "@/lib/auth";

export function UserMenu() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [joinOpen, setJoinOpen] = useState(false);
  const [code, setCode] = useState("");
  const [joinError, setJoinError] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  if (!user) return null;

  const initial = (user.display_name || user.username).slice(0, 1).toUpperCase();

  const join = async () => {
    const trimmed = code.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setJoinError("");
    const res = await fetch("/api/auth/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: trimmed }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setJoinError(data.detail ?? "加入失败");
      return;
    }
    // 加入后空间列表变了，整页刷新最稳（各页面数据源不统一）
    window.location.reload();
  };

  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
      >
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-soft text-[11px] font-medium text-brand">
          {initial}
        </span>
        <span className="max-w-[7rem] truncate">{user.display_name}</span>
        <span className="text-tertiary">▾</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-64 rounded-xl border border-line bg-surface-1 p-2 shadow-lg">
          <div className="px-2 pb-2 pt-1">
            <p className="text-[13px] font-medium">{user.display_name}</p>
            <p className="mt-0.5 text-[12px] text-tertiary">
              {user.username} · {user.role_label}
            </p>
            <p className="mt-1.5 text-[11.5px] text-tertiary">
              可见空间 {user.workspaces.length} 个 · 你在各空间内的角色见左上角切换器
            </p>
          </div>

          <div className="border-t border-line pt-2">
            {joinOpen ? (
              <div className="px-1 pb-2">
                <input
                  autoFocus
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void join()}
                  placeholder="粘贴邀请码"
                  className="h-8 w-full rounded-lg border border-line bg-surface-2 px-2 text-[12.5px] outline-none focus:border-line-strong"
                />
                {joinError && <p className="mt-1 text-[11.5px] text-danger">{joinError}</p>}
                <div className="mt-1.5 flex gap-2">
                  <button
                    onClick={() => void join()}
                    disabled={busy || !code.trim()}
                    className="h-7 rounded-lg bg-brand px-2.5 text-[12px] text-white disabled:opacity-40"
                  >
                    加入
                  </button>
                  <button
                    onClick={() => {
                      setJoinOpen(false);
                      setJoinError("");
                    }}
                    className="h-7 rounded-lg px-2 text-[12px] text-tertiary hover:text-primary"
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setJoinOpen(true)}
                className="w-full rounded-lg px-2 py-1.5 text-left text-[13px] text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
              >
                用邀请码加入空间
              </button>
            )}
          </div>

          <div className="border-t border-line pt-2">
            <button
              onClick={() => void signOut()}
              disabled={busy}
              className="w-full rounded-lg px-2 py-1.5 text-left text-[13px] text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary disabled:opacity-50"
            >
              {busy ? "正在退出…" : "退出登录"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ThemeToggle } from "@/app/layout/ThemeToggle";
import { bootstrap, lastUsername, login, useAuth } from "@/lib/auth";

const FEATURES = [
  { title: "知识库 RAG", desc: "多格式解析、切片、向量与 BM25 混合召回" },
  { title: "智能体编排", desc: "LangGraph 驱动的工具调用与多步推理" },
  { title: "开放平台", desc: "/v1 兼容 OpenAI 协议，按工作区隔离与审计" },
];

function safeNext(search: string): string {
  const next = new URLSearchParams(search).get("next") ?? "";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/chat";
}

export function LoginPage() {
  const nav = useNavigate();
  const location = useLocation();
  const { status } = useAuth();

  const [username, setUsername] = useState(lastUsername);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);

  const next = safeNext(location.search);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    let alive = true;
    fetch("/api/health")
      .then((res) => alive && setOnline(res.ok))
      .catch(() => alive && setOnline(false));
    return () => {
      alive = false;
    };
  }, []);

  if (status === "authed") return <Navigate to={next} replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (!username.trim() || !password) {
      setError("请输入用户名与口令");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      nav(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-page">
      {/* 品牌区：只在大屏出现，避免小屏挤压表单 */}
      <aside className="relative hidden w-[46%] max-w-[620px] shrink-0 flex-col justify-between overflow-hidden border-r border-line bg-surface-2 p-12 lg:flex">
        <div
          aria-hidden
          className="pointer-events-none absolute -left-24 -top-32 h-[420px] w-[420px] rounded-full opacity-70 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--brand-soft), transparent 65%)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 right-0 h-[380px] w-[380px] rounded-full opacity-60 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--brand-soft), transparent 70%)" }}
        />

        <div className="relative flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-brand text-[13px] font-semibold text-brand-fg">
            AI
          </span>
          <span className="text-[15px] font-medium tracking-wide">AI 平台</span>
        </div>

        <div className="relative max-w-[420px]">
          <h2 className="text-[26px] font-medium leading-[1.35] tracking-tight">
            把散落的资料，
            <br />
            变成可以调用的能力。
          </h2>
          <p className="mt-3 text-[13px] leading-6 text-secondary">
            从知识库到智能体，再到可计量的开放接口——同一套底座，支撑三种使用方式。
          </p>

          <ul className="mt-10 space-y-5">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <div>
                  <p className="text-[13px] font-medium">{f.title}</p>
                  <p className="mt-0.5 text-[12.5px] leading-5 text-secondary">{f.desc}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="relative flex items-center gap-2 text-[12px] text-tertiary">
          <span
            className={
              online === false
                ? "h-1.5 w-1.5 rounded-full bg-danger"
                : online
                  ? "h-1.5 w-1.5 rounded-full bg-success"
                  : "h-1.5 w-1.5 rounded-full bg-tertiary"
            }
          />
          {online === null ? "正在检测服务状态" : online ? "服务运行中" : "服务未连接"}
          <span className="mx-1 text-line-strong">·</span>
          v0.1.0
        </div>
      </aside>

      {/* 表单区 */}
      <main className="relative flex flex-1 items-center justify-center px-6 py-14">
        <div className="absolute right-6 top-6">
          <ThemeToggle />
        </div>

        <form onSubmit={submit} className="w-full max-w-[352px]">
          <div className="mb-8 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-brand text-[13px] font-semibold text-brand-fg">
              AI
            </span>
          </div>

          <h1 className="text-[22px] font-medium tracking-tight">登录控制台</h1>
          <p className="mt-1.5 text-[13px] text-secondary">使用平台账号继续，未授权访问将被拒绝。</p>

          <div className="mt-8 space-y-4">
            <div>
              <label htmlFor="username" className="mb-1.5 block text-[12.5px] text-secondary">
                用户名
              </label>
              <input
                id="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                className="h-10 w-full rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none transition-colors duration-150 placeholder:text-tertiary focus:border-brand"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1.5 block text-[12.5px] text-secondary">
                口令
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={show ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入口令"
                  className="h-10 w-full rounded-lg border border-line bg-surface-1 pl-3 pr-14 text-[13px] outline-none transition-colors duration-150 placeholder:text-tertiary focus:border-brand"
                />
                <button
                  type="button"
                  onClick={() => setShow((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-1 text-[12px] text-tertiary transition-colors duration-150 hover:text-secondary"
                >
                  {show ? "隐藏" : "显示"}
                </button>
              </div>
            </div>
          </div>

          {error && (
            <p className="mt-4 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[12.5px] text-danger">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-6 h-10 w-full rounded-lg bg-brand text-[13px] font-medium text-brand-fg transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "正在验证…" : "登录"}
          </button>

          <div className="mt-6 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
            <p className="text-[12px] text-tertiary">
              初始账号 <span className="text-secondary">admin / admin123</span>，登录后请在「系统管理 · 成员」中修改。
            </p>
            <button
              type="button"
              onClick={() => {
                setUsername("admin");
                setPassword("admin123");
                setError("");
              }}
              className="mt-1 text-[12px] text-brand transition-opacity duration-150 hover:opacity-80"
            >
              填入初始账号
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}

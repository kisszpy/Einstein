import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowRight,
  Check,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Moon,
  Sun,
  User,
} from "lucide-react";
import { useTheme } from "@/app/layout/useTheme";
import { bootstrap, lastUsername, login, useAuth } from "@/lib/auth";

const FEATURES = [
  { no: "01", title: "知识库 RAG", desc: "多格式解析、切片、向量与 BM25 混合召回" },
  { no: "02", title: "智能体编排", desc: "LangGraph 驱动的工具调用与多步推理" },
  { no: "03", title: "开放平台", desc: "/v1 兼容 OpenAI 协议，按工作区隔离与审计" },
];

function safeNext(search: string): string {
  const next = new URLSearchParams(search).get("next") ?? "";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/chat";
}

const FIELD =
  "h-11 w-full rounded-[10px] border border-line bg-surface-1 pl-10 pr-3 text-[13.5px] text-primary " +
  "outline-none transition-all duration-200 " +
  "placeholder:text-placeholder hover:border-line-strong focus:border-brand focus:shadow-[0_0_0_4px_var(--brand-soft)]";

const LABEL = "mb-2 block text-[11.5px] font-medium tracking-[0.06em] text-tertiary";

export function LoginPage() {
  const nav = useNavigate();
  const location = useLocation();
  const { status } = useAuth();
  const { theme, toggle } = useTheme();

  const [username, setUsername] = useState(lastUsername);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
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

  // 登录成功后先停在成功态上做完过渡，再跳转
  if (status === "authed" && !done) return <Navigate to={next} replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || done) return;
    if (!username.trim() || !password) {
      setError("请输入用户名与密码");
      setNonce((n) => n + 1);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      setDone(true);
      window.setTimeout(() => nav(next, { replace: true }), 640);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后再试");
      setNonce((n) => n + 1);
      setBusy(false);
    }
  };

  const trackCaps = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (typeof e.getModifierState === "function") setCaps(e.getModifierState("CapsLock"));
  };

  return (
    <div className="relative flex min-h-screen overflow-hidden bg-page">
      {/* 背景：极淡网格 + 两团缓慢漂移的品牌光晕 */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(to right, var(--login-grid) 1px, transparent 1px), linear-gradient(to bottom, var(--login-grid) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
            maskImage: "radial-gradient(ellipse 70% 55% at 50% 0%, black, transparent 78%)",
            WebkitMaskImage: "radial-gradient(ellipse 70% 55% at 50% 0%, black, transparent 78%)",
          }}
        />
        <div
          className="login-drift absolute -left-[16%] -top-[26%] h-[640px] w-[640px] rounded-full blur-[130px]"
          style={{ background: "radial-gradient(circle, var(--login-glow), transparent 70%)" }}
        />
        <div
          className="login-drift absolute -bottom-[30%] right-[-12%] h-[560px] w-[560px] rounded-full blur-[120px]"
          style={{
            background: "radial-gradient(circle, var(--login-glow-alt), transparent 72%)",
            animationDelay: "-9s",
          }}
        />
      </div>

      <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-[1240px] flex-col px-5 sm:px-8 lg:flex-row lg:px-10">
        {/* 品牌叙事区 */}
        <aside className="hidden w-[54%] shrink-0 flex-col justify-between py-14 pr-10 lg:flex xl:pr-16">
          <div className="login-fade flex items-center gap-3">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-[11px] text-[14px] font-semibold text-brand-fg shadow-[0_8px_20px_-8px_var(--brand)]"
              style={{ background: "linear-gradient(140deg, var(--brand), var(--brand-deep))" }}
            >
              E
            </span>
            <span className="flex items-baseline gap-2">
              <span className="text-[15px] font-medium tracking-[0.01em]">Einstein</span>
              <span className="text-[11px] tracking-[0.14em] text-tertiary">AI PLATFORM</span>
            </span>
          </div>

          <div className="login-rise max-w-[460px]">
            <h2 className="text-[34px] font-medium leading-[1.28] tracking-[-0.02em] xl:text-[38px]">
              把散落的资料，
              <br />
              变成可以调用的
              <span className="relative whitespace-nowrap text-brand">
                能力
                <span
                  aria-hidden
                  className="absolute -bottom-1 left-0 h-[2px] w-full rounded-full opacity-40"
                  style={{ background: "linear-gradient(90deg, var(--brand), transparent)" }}
                />
              </span>
              。
            </h2>
            <p className="mt-5 max-w-[400px] text-[13.5px] leading-[1.85] text-secondary">
              从知识库到智能体，再到可计量的开放接口——同一套底座，支撑三种使用方式。
            </p>

            <ul className="mt-11 space-y-0">
              {FEATURES.map((f, i) => (
                <li
                  key={f.title}
                  className={`group flex gap-4 py-3.5 ${i === 0 ? "" : "border-t border-line"}`}
                >
                  <span className="mt-0.5 text-[11px] tabular-nums tracking-[0.1em] text-brand/70 transition-colors duration-200 group-hover:text-brand">
                    {f.no}
                  </span>
                  <div>
                    <p className="text-[13px] font-medium tracking-[0.01em]">{f.title}</p>
                    <p className="mt-1 text-[12.5px] leading-5 text-tertiary">{f.desc}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="login-fade flex items-center gap-2.5 text-[11.5px] text-tertiary">
            <span className="relative flex h-1.5 w-1.5">
              {online && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
              )}
              <span
                className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                  online === false ? "bg-danger" : online ? "bg-success" : "bg-tertiary"
                }`}
              />
            </span>
            {online === null ? "正在检测服务状态" : online ? "服务运行中" : "服务未连接"}
            <span className="mx-0.5 h-3 w-px bg-line-strong" />
            <span className="tabular-nums">v{__APP_VERSION__}</span>
          </div>
        </aside>

        {/* 表单区 */}
        <main className="relative flex flex-1 items-center justify-center py-12 lg:py-14">
          <button
            type="button"
            onClick={toggle}
            aria-label={theme === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
            className="absolute right-0 top-6 flex h-9 w-9 items-center justify-center rounded-full border border-line bg-surface-1/70 text-secondary backdrop-blur transition-all duration-200 hover:border-line-strong hover:text-primary lg:top-8"
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>

          <div className="login-rise w-full max-w-[404px]">
            {/* 小屏品牌标识 */}
            <div className="mb-8 flex items-center gap-2.5 lg:hidden">
              <span
                className="flex h-9 w-9 items-center justify-center rounded-[11px] text-[14px] font-semibold text-brand-fg"
                style={{ background: "linear-gradient(140deg, var(--brand), var(--brand-deep))" }}
              >
                E
              </span>
              <span className="text-[14px] font-medium">Einstein</span>
            </div>

            <div className="relative overflow-hidden rounded-[18px] border border-line bg-surface-1/85 p-7 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_28px_70px_-30px_rgba(15,23,42,0.35)] backdrop-blur-xl sm:p-8">
              <div
                aria-hidden
                className="absolute inset-x-10 top-0 h-px"
                style={{
                  background: "linear-gradient(90deg, transparent, var(--brand), transparent)",
                  opacity: 0.55,
                }}
              />

              <header>
                <h1 className="text-[20px] font-medium tracking-[-0.01em]">登录控制台</h1>
                <p className="mt-2 text-[12.5px] leading-5 text-tertiary">
                  使用平台账号继续，未授权访问将被拒绝。
                </p>
              </header>

              <form onSubmit={submit} className="mt-7">
                <div className="space-y-4">
                  <div>
                    <label htmlFor="username" className={LABEL}>
                      用户名
                    </label>
                    <div className="group relative">
                      <User
                        size={15}
                        strokeWidth={1.7}
                        className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-tertiary transition-colors duration-200 group-focus-within:text-brand"
                      />
                      <input
                        id="username"
                        autoComplete="username"
                        autoFocus
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        placeholder="请输入用户名"
                        className={FIELD}
                      />
                    </div>
                  </div>

                  <div>
                    <label htmlFor="password" className={LABEL}>
                      密码
                    </label>
                    <div className="group relative">
                      <Lock
                        size={15}
                        strokeWidth={1.7}
                        className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-tertiary transition-colors duration-200 group-focus-within:text-brand"
                      />
                      <input
                        id="password"
                        type={show ? "text" : "password"}
                        autoComplete="current-password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        onKeyUp={trackCaps}
                        onKeyDown={trackCaps}
                        onBlur={() => setCaps(false)}
                        placeholder="请输入密码"
                        className={`${FIELD} pr-11`}
                      />
                      <button
                        type="button"
                        onClick={() => setShow((v) => !v)}
                        aria-label={show ? "隐藏密码" : "显示密码"}
                        className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-tertiary transition-colors duration-200 hover:bg-hover hover:text-secondary"
                      >
                        {show ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                    {caps && (
                      <p className="login-fade mt-2 flex items-center gap-1.5 text-[11.5px] text-warning">
                        <AlertCircle size={12} />
                        大写锁定已开启
                      </p>
                    )}
                  </div>
                </div>

                {error && (
                  <div
                    key={nonce}
                    role="alert"
                    className="login-shake mt-4 flex items-start gap-2 rounded-[10px] border border-danger/25 bg-danger/5 px-3 py-2.5 text-[12.5px] leading-5 text-danger"
                  >
                    <AlertCircle size={14} className="mt-[3px] shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <button
                  type="submit"
                  disabled={busy || done}
                  className="login-shine group/btn mt-6 flex h-11 w-full items-center justify-center gap-2 rounded-[10px] text-[13.5px] font-medium text-brand-fg transition-all duration-200 hover:-translate-y-px hover:shadow-[0_10px_28px_-10px_var(--brand)] active:translate-y-0 disabled:cursor-default disabled:translate-y-0 disabled:opacity-90"
                  style={{ background: "linear-gradient(135deg, var(--brand), var(--brand-deep))" }}
                >
                  {done ? (
                    <>
                      <Check size={16} strokeWidth={2.4} />
                      验证通过，正在进入
                    </>
                  ) : busy ? (
                    <>
                      <Loader2 size={15} className="animate-spin" />
                      正在验证
                    </>
                  ) : (
                    <>
                      登录
                      <ArrowRight
                        size={15}
                        className="transition-transform duration-200 group-hover/btn:translate-x-0.5"
                      />
                    </>
                  )}
                </button>
              </form>

              <div className="mt-6 flex items-center justify-between gap-3 border-t border-line pt-4">
                <p className="text-[11.5px] leading-5 text-tertiary">
                  初始账号 <span className="tabular-nums text-secondary">admin / admin123</span>
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setUsername("admin");
                    setPassword("admin123");
                    setError("");
                  }}
                  className="shrink-0 rounded-md px-2 py-1 text-[11.5px] text-brand transition-colors duration-150 hover:bg-brand-soft"
                >
                  一键填入
                </button>
              </div>
            </div>

            <p className="mt-5 text-center text-[11px] tracking-[0.02em] text-tertiary">
              登录后请在「系统管理 · 成员」中修改初始密码
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}

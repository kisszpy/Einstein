import { useEffect, useMemo, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Braces,
  Check,
  Database,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Moon,
  Sun,
  User,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "@/app/layout/useTheme";
import { BrandMark } from "@/components/BrandMark";
import { bootstrap, lastUsername, login, useAuth } from "@/lib/auth";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import { NeuralField } from "./NeuralField";

const FEATURES: { icon: LucideIcon; label: string }[] = [
  { icon: Database, label: "知识库 RAG" },
  { icon: Bot, label: "智能体编排" },
  { icon: Braces, label: "OpenAI 兼容接口" },
];

/** 卡片下方的打字机行：把三种能力轮播出来，替代原来左侧的说明列表 */
const TICKER = [
  "rag.search(query) → 混合召回",
  "agent.run(task) → 多步推理 + 工具调用",
  "openai.chat(model) → /v1 兼容",
];

const CARD_RING =
  "conic-gradient(from 0deg, transparent 0deg, var(--brand) 62deg, rgba(255,255,255,0.9) 88deg, " +
  "var(--brand) 114deg, transparent 176deg, transparent 214deg, var(--login-accent) 268deg, transparent 330deg)";

const FIELD =
  "peer h-11 w-full rounded-xl border border-line bg-surface-2/60 pl-10 pr-3 text-[13.5px] text-primary " +
  "outline-none transition-all duration-300 placeholder:text-placeholder hover:border-line-strong " +
  "focus:border-brand/70 focus:bg-surface-1 focus:shadow-[0_0_0_4px_var(--brand-soft)]";

const LABEL = "mb-2 flex items-center gap-1.5 text-[12px] font-medium tracking-[0.08em] text-tertiary";

function safeNext(search: string): string {
  const next = new URLSearchParams(search).get("next") ?? "";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/chat";
}

function Ticker() {
  const reduce = usePrefersReducedMotion();
  const [index, setIndex] = useState(0);
  const [len, setLen] = useState(0);
  const line = TICKER[index] ?? "";

  useEffect(() => {
    if (reduce) return;
    if (len < line.length) {
      const t = window.setTimeout(() => setLen(len + 1), 26);
      return () => window.clearTimeout(t);
    }
    const t = window.setTimeout(() => {
      setLen(0);
      setIndex((i) => (i + 1) % TICKER.length);
    }, 2200);
    return () => window.clearTimeout(t);
  }, [index, len, line, reduce]);

  const text = reduce ? line : line.slice(0, len);

  return (
    <p
      aria-hidden
      className="mt-4 flex h-[18px] items-center justify-center gap-1.5 font-mono text-[12px] tracking-[0.02em] text-tertiary"
    >
      <span className="text-brand-ink">›</span>
      <span className="tabular-nums">{text}</span>
      {!reduce && <span className="login-caret inline-block h-3 w-[1.5px] bg-brand" />}
    </p>
  );
}

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
  const reduce = usePrefersReducedMotion();
  const statusText = useMemo(
    () => (online === null ? "正在检测服务状态" : online ? "服务运行中" : "服务未连接"),
    [online],
  );

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
      // 短暂停留展示"验证通过"反馈，但不再拖 640ms 的人为延迟
      window.setTimeout(() => nav(next, { replace: true }), 400);
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
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-page">
      {/* ---------- 背景：网格 + 神经网络 + 极光 ---------- */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(to right, var(--login-grid) 1px, transparent 1px), linear-gradient(to bottom, var(--login-grid) 1px, transparent 1px)",
            backgroundSize: "58px 58px",
            maskImage: "radial-gradient(ellipse 78% 62% at 50% 38%, black, transparent 76%)",
            WebkitMaskImage: "radial-gradient(ellipse 78% 62% at 50% 38%, black, transparent 76%)",
          }}
        />
        <NeuralField className="absolute inset-0 h-full w-full opacity-70" />
        <div
          className="login-drift absolute -left-[18%] -top-[30%] h-[680px] w-[680px] rounded-full blur-[110px]"
          style={{ background: "radial-gradient(circle, var(--login-glow), transparent 70%)" }}
        />
        <div
          className="login-drift absolute -bottom-[34%] right-[-14%] h-[620px] w-[620px] rounded-full blur-[100px]"
          style={{
            background: "radial-gradient(circle, var(--login-glow-alt), transparent 72%)",
            animationDelay: "-9s",
          }}
        />
        {/* 顶部一道极细的扫描光，暗示「系统在线」 */}
        <div
          className="absolute inset-x-0 top-0 h-px"
          style={{
            background: "linear-gradient(90deg, transparent, var(--brand), transparent)",
            opacity: 0.35,
          }}
        />
      </div>

      {/* ---------- 顶栏 ---------- */}
      <header className="relative z-10 mx-auto flex w-full max-w-[1180px] items-center justify-between px-5 pt-6 sm:px-8">
        <div className="login-fade flex items-center gap-2.5">
          <BrandMark size={30} className="shadow-[0_8px_20px_-10px_var(--brand)]" />
          <span className="leading-none">
            <span className="block text-[13.5px] font-medium">Einstein</span>
            <span className="mt-1 block text-[10px] tracking-[0.18em] text-tertiary">
              AI PLATFORM
            </span>
          </span>
        </div>

        <button
          type="button"
          onClick={toggle}
          aria-label={theme === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-line bg-surface-1/60 text-secondary backdrop-blur transition-all duration-300 hover:rotate-12 hover:border-line-strong hover:text-primary"
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </header>

      {/* ---------- 主体 ---------- */}
      <main className="relative z-10 flex flex-1 items-center justify-center px-4 py-8">
        <div className="w-full max-w-[430px]">
          <div className="relative">
            <div
              aria-hidden
              className="login-card-glow pointer-events-none absolute inset-x-3 -top-8 bottom-2 rounded-[52px] blur-3xl"
            />

            <div className="login-rise login-frame relative overflow-hidden rounded-[26px] shadow-[0_1px_2px_rgba(0,0,0,0.04),0_44px_90px_-42px_rgba(15,23,42,0.55)]">
              {/* 缓慢旋转的锥形渐变，被内层卡片遮出 1px 流动描边 */}
              <div
                aria-hidden
                className="login-frame-spin absolute -inset-[45%] opacity-80"
                style={{ background: CARD_RING }}
              />

              <div className="relative m-px overflow-hidden rounded-[25px] bg-surface-1/95 px-7 pb-6 pt-8 backdrop-blur-2xl sm:px-8">
                {/* 顶部游走的一小段高光 */}
                <span
                  aria-hidden
                  className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden"
                >
                  <span
                    className="login-trace absolute top-0 h-px w-16"
                    style={{
                      background: "linear-gradient(90deg, transparent, var(--brand), transparent)",
                    }}
                  />
                </span>

                {/* 品牌标识：双层轨道 + 光晕 */}
                <div className="relative mx-auto mb-5 flex h-[76px] w-[76px] items-center justify-center">
                  <span
                    aria-hidden
                    className="login-halo absolute inset-1 rounded-full"
                    style={{
                      background: "radial-gradient(circle, var(--brand-soft), transparent 70%)",
                    }}
                  />
                  <span
                    aria-hidden
                    className="login-orbit absolute inset-0 rounded-full border border-dashed border-brand/25"
                  >
                    <span className="absolute left-1/2 top-0 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand shadow-[0_0_10px_2px_var(--brand)]" />
                  </span>
                  <span
                    aria-hidden
                    className="login-orbit-rev absolute -inset-2.5 rounded-full border border-line"
                  >
                    <span
                      className="absolute bottom-0 left-1/2 h-1 w-1 -translate-x-1/2 translate-y-1/2 rounded-full"
                      style={{
                        background: "var(--login-accent)",
                        boxShadow: "0 0 9px 2px var(--login-accent)",
                      }}
                    />
                  </span>
                  <BrandMark
                    size={46}
                    title="Einstein"
                    className="relative rounded-[11px] shadow-[0_12px_28px_-12px_var(--brand)]"
                  />
                </div>

                <h1 className="text-center text-[21px] font-semibold">进入智能工作台</h1>
                <p className="mt-2 text-center text-[12.5px] leading-5 text-tertiary">
                  一套底座，三种用法——检索、执行、接入。
                </p>
                <Ticker />

                <form onSubmit={submit} className="mt-6">
                  <div className="space-y-4">
                    <div>
                      <label htmlFor="username" className={LABEL}>
                        用户名
                      </label>
                      <div className="group relative">
                        <User
                          size={15}
                          strokeWidth={1.7}
                          className="pointer-events-none absolute left-3.5 top-1/2 z-10 -translate-y-1/2 text-tertiary transition-all duration-300 group-focus-within:scale-110 group-focus-within:text-brand-ink"
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
                        <span
                          aria-hidden
                          className="pointer-events-none absolute inset-x-3 bottom-0 h-px origin-left scale-x-0 bg-gradient-to-r from-transparent via-brand to-transparent transition-transform duration-500 peer-focus:scale-x-100"
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
                          className="pointer-events-none absolute left-3.5 top-1/2 z-10 -translate-y-1/2 text-tertiary transition-all duration-300 group-focus-within:scale-110 group-focus-within:text-brand-ink"
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
                        <span
                          aria-hidden
                          className="pointer-events-none absolute inset-x-3 bottom-0 h-px origin-left scale-x-0 bg-gradient-to-r from-transparent via-brand to-transparent transition-transform duration-500 peer-focus:scale-x-100"
                        />
                        <button
                          type="button"
                          onClick={() => setShow((v) => !v)}
                          aria-label={show ? "隐藏密码" : "显示密码"}
                          className="absolute right-2 top-1/2 z-10 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-tertiary transition-colors duration-200 hover:bg-hover hover:text-secondary"
                        >
                          {show ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                      {caps && (
                        <p className="login-fade mt-2 flex items-center gap-1.5 text-[12px] text-warning">
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
                      className="login-shake mt-4 flex items-start gap-2 rounded-xl border border-danger/25 bg-danger/5 px-3 py-2.5 text-[12.5px] leading-5 text-danger"
                    >
                      <AlertCircle size={14} className="mt-[3px] shrink-0" />
                      <span>{error}</span>
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={busy || done}
                    className="login-shine group/btn relative mt-6 flex h-11 w-full items-center justify-center gap-2 overflow-hidden rounded-xl text-[13.5px] font-medium text-brand-fg shadow-[0_12px_30px_-16px_var(--brand)] transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_18px_38px_-16px_var(--brand)] active:translate-y-0 disabled:cursor-default disabled:hover:translate-y-0"
                  >
                    <span
                      aria-hidden
                      className="absolute inset-0"
                      style={{
                        background:
                          "linear-gradient(120deg, var(--brand), var(--brand-deep) 58%, var(--brand))",
                      }}
                    />
                    {done && (
                      <span
                        aria-hidden
                        className="login-done-ring absolute left-1/2 top-1/2 h-11 w-11 rounded-full border-2 border-brand"
                      />
                    )}
                    <span className="relative flex items-center gap-2">
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
                            className="transition-transform duration-300 group-hover/btn:translate-x-0.5"
                          />
                        </>
                      )}
                    </span>
                  </button>
                </form>

                {/* 初始账号提示只在开发构建展示：生产环境不该把默认凭据印在登录页上 */}
                {import.meta.env.DEV && (
                <div className="mt-6 flex items-center justify-between gap-3 rounded-xl border border-dashed border-line bg-surface-2/50 px-3 py-2.5">
                  <p className="text-[12px] leading-5 text-tertiary">
                    初始账号{" "}
                    <span className="tabular-nums text-secondary">admin / admin123</span>
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setUsername("admin");
                      setPassword("admin123");
                      setError("");
                    }}
                    className="shrink-0 rounded-md px-2 py-1 text-[12px] text-brand-ink transition-colors duration-150 hover:bg-brand-soft "
                  >
                    一键填入
                  </button>
                </div>
                )}
              </div>
            </div>
          </div>

          {/* 能力标签：原左侧列表的三条，收成胶囊 */}
          <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
            {FEATURES.map((f, i) => (
              <span
                key={f.label}
                className="login-pop flex items-center gap-1.5 rounded-full border border-line bg-surface-1/55 px-3 py-1.5 text-[12px] text-secondary backdrop-blur transition-colors duration-200 hover:border-brand/30 hover:text-primary"
                style={{ animationDelay: `${340 + i * 90}ms` }}
              >
                <f.icon size={12} strokeWidth={1.8} className="text-brand-ink"/>
                {f.label}
              </span>
            ))}
          </div>
        </div>
      </main>

      {/* ---------- 底栏 ---------- */}
      <footer className="relative z-10 mx-auto flex w-full max-w-[1180px] items-center justify-between gap-3 px-5 py-5 text-[12px] text-tertiary sm:px-8">
        <span className="flex items-center gap-2">
          <span className="relative flex h-1.5 w-1.5">
            {online && !reduce && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
            )}
            <span
              className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                online === false ? "bg-danger" : online ? "bg-success" : "bg-tertiary"
              }`}
            />
          </span>
          {statusText}
          <span className="mx-0.5 hidden h-3 w-px bg-line-strong sm:block" />
          <span className="hidden tabular-nums sm:block">v{__APP_VERSION__}</span>
        </span>
        <span className="hidden text-right sm:block">
          登录后请在「系统管理 · 成员」中修改初始密码
        </span>
      </footer>
    </div>
  );
}

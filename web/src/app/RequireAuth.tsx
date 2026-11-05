import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { bootstrap, useAuth } from "@/lib/auth";

function BootScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-page">
      <div className="flex items-center gap-2.5 text-[13px] text-tertiary">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-brand" />
        正在校验登录状态
      </div>
    </div>
  );
}

/** 控制台路由守卫：未登录或会话过期一律回到登录页，并记住原目标。 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (status === "idle" || status === "loading") void bootstrap();
  }, [status]);

  if (status === "idle" || status === "loading") return <BootScreen />;

  if (status !== "authed") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  return <>{children}</>;
}

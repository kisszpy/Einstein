import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { BootScreen, RequireAuth } from "./RequireAuth";
import { LoginPage } from "@/features/auth/LoginPage";

/** 路由级代码分割：每个功能页一个 chunk，首屏不再打包 SlideCanvas/DeckEditor
 *  等重模块（单 chunk 曾达 815KB / gzip 236KB，Vite 已发 >500KB 警告）。 */
const ChatPage = lazy(() =>
  import("@/features/chat/ChatPage").then((m) => ({ default: m.ChatPage })),
);
const BrowserPage = lazy(() =>
  import("@/features/browser/BrowserPage").then((m) => ({ default: m.BrowserPage })),
);
const KbPage = lazy(() => import("@/features/kb/KbPage").then((m) => ({ default: m.KbPage })));
const HubPage = lazy(() => import("@/features/hub/HubPage").then((m) => ({ default: m.HubPage })));
const AgentsPage = lazy(() =>
  import("@/features/agents/AgentsPage").then((m) => ({ default: m.AgentsPage })),
);
const AdminPage = lazy(() =>
  import("@/features/admin/AdminPage").then((m) => ({ default: m.AdminPage })),
);
const EvalPage = lazy(() =>
  import("@/features/eval/EvalPage").then((m) => ({ default: m.EvalPage })),
);
const PlatformPage = lazy(() =>
  import("@/features/platform/PlatformPage").then((m) => ({ default: m.PlatformPage })),
);
const OpsPage = lazy(() => import("@/features/ops/OpsPage").then((m) => ({ default: m.OpsPage })));
const ModelsPage = lazy(() =>
  import("@/features/models/ModelsPage").then((m) => ({ default: m.ModelsPage })),
);
const PublicPage = lazy(() =>
  import("@/features/share/PublicPage").then((m) => ({ default: m.PublicPage })),
);

/** 懒加载页共用 fallback：和首屏启动态同一套 spinner。 */
function LazyPage({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<BootScreen />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // 分享出去的只读页：鉴权之外，URL 里的 token 就是全部凭据
  {
    path: "/s/:token",
    element: (
      <LazyPage>
        <PublicPage />
      </LazyPage>
    ),
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      {
        path: "chat",
        element: (
          <LazyPage>
            <ChatPage />
          </LazyPage>
        ),
      },
      {
        path: "browser",
        element: (
          <LazyPage>
            <BrowserPage />
          </LazyPage>
        ),
      },
      {
        path: "kb",
        element: (
          <LazyPage>
            <KbPage />
          </LazyPage>
        ),
      },
      {
        path: "experts",
        element: (
          <LazyPage>
            <HubPage />
          </LazyPage>
        ),
      },
      {
        path: "skills",
        element: (
          <LazyPage>
            <HubPage />
          </LazyPage>
        ),
      },
      {
        path: "agents",
        element: (
          <LazyPage>
            <AgentsPage />
          </LazyPage>
        ),
      },
      {
        path: "models",
        element: (
          <LazyPage>
            <ModelsPage />
          </LazyPage>
        ),
      },
      {
        path: "mcp",
        element: (
          <LazyPage>
            <HubPage />
          </LazyPage>
        ),
      },
      {
        path: "eval",
        element: (
          <LazyPage>
            <EvalPage />
          </LazyPage>
        ),
      },
      {
        path: "platform",
        element: (
          <LazyPage>
            <PlatformPage />
          </LazyPage>
        ),
      },
      {
        path: "ops",
        element: (
          <LazyPage>
            <OpsPage />
          </LazyPage>
        ),
      },
      {
        path: "admin",
        element: (
          <LazyPage>
            <AdminPage />
          </LazyPage>
        ),
      },
    ],
  },
]);

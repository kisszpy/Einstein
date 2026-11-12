import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AppLayout } from "./layout/AppLayout";
import { NotFoundPage } from "./NotFoundPage";
import { BootScreen, RequireAuth } from "./RequireAuth";
import { LoginPage } from "@/features/auth/LoginPage";

/** 路由级代码分割：每个功能页一个 chunk，首屏不再打包 SlideCanvas/DeckEditor
 *  等重模块（单 chunk 曾达 815KB / gzip 236KB，Vite 已发 >500KB 警告）。 */
const ChatPage = lazy(() =>
  import("@/features/chat/ChatPage").then((m) => ({ default: m.ChatPage })),
);
const PptStudioPage = lazy(() =>
  import("@/features/ppt-studio/PptStudioPage").then((m) => ({ default: m.PptStudioPage })),
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
const DeckCraftPage = lazy(() =>
  import("@/features/deck-editor/DeckCraftPage").then((m) => ({ default: m.DeckCraftPage })),
);

/** 懒加载页共用 fallback：和首屏启动态同一套 spinner。
 *  外面套一层错误边界：单页渲染崩了只废这一页，侧栏还在、能换页；
 *  key=pathname 让换页时自动重置，不会一直卡在上一页的报错上。 */
function LazyPage({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary key={pathname}>
      <Suspense fallback={<BootScreen />}>{children}</Suspense>
    </ErrorBoundary>
  );
}

/** 旧入口：/browser → /assets。查询参数（tab/project/path/url）是深链的载体，必须带过去。 */
function LegacyAssetsRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/assets${search}`} replace />;
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
        path: "ppt-studio",
        element: (
          <LazyPage>
            <PptStudioPage />
          </LazyPage>
        ),
      },
      {
        path: "assets",
        element: (
          <LazyPage>
            <BrowserPage />
          </LazyPage>
        ),
      },
      // 旧入口兼容：/browser 已改名为「资产」，深链的 tab/project/path/url 要一并带过去
      { path: "browser", element: <LegacyAssetsRedirect /> },
      // 旧入口兼容：/deck 曾是 PPTist 编辑器，已整体下线，老书签/老链接落到 DeckCraft
      { path: "deck", element: <Navigate to="/deckcraft" replace /> },
      {
        path: "deckcraft",
        element: (
          <LazyPage>
            <DeckCraftPage />
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
  // catch-all：没匹配上的路径给出产品化的 404，而不是 React Router 的开发者提示页
  { path: "*", element: <NotFoundPage /> },
]);

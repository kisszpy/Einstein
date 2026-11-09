import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { RequireAuth } from "./RequireAuth";
import { LoginPage } from "@/features/auth/LoginPage";
import { ChatPage } from "@/features/chat/ChatPage";
import { BrowserPage } from "@/features/browser/BrowserPage";
import { KbPage } from "@/features/kb/KbPage";
import { HubPage } from "@/features/hub/HubPage";
import { AgentsPage } from "@/features/agents/AgentsPage";
import { AdminPage } from "@/features/admin/AdminPage";
import { EvalPage } from "@/features/eval/EvalPage";
import { PlatformPage } from "@/features/platform/PlatformPage";
import { OpsPage } from "@/features/ops/OpsPage";
import { ModelsPage } from "@/features/models/ModelsPage";
import { PublicPage } from "@/features/share/PublicPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // 分享出去的只读页：鉴权之外，URL 里的 token 就是全部凭据
  { path: "/s/:token", element: <PublicPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "browser", element: <BrowserPage /> },
      { path: "kb", element: <KbPage /> },
      { path: "experts", element: <HubPage /> },
      { path: "skills", element: <HubPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "mcp", element: <HubPage /> },
      { path: "eval", element: <EvalPage /> },
      { path: "platform", element: <PlatformPage /> },
      { path: "ops", element: <OpsPage /> },
      { path: "admin", element: <AdminPage /> },
    ],
  },
]);

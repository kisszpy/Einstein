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
import { ModelsPage } from "@/features/models/ModelsPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
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
      { path: "admin", element: <AdminPage /> },
    ],
  },
]);

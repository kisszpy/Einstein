import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { RequireAuth } from "./RequireAuth";
import { LoginPage } from "@/features/auth/LoginPage";
import { ChatPage } from "@/features/chat/ChatPage";
import { BrowserPage } from "@/features/browser/BrowserPage";
import { KbPage } from "@/features/kb/KbPage";
import { SkillsPage } from "@/features/skills/SkillsPage";
import { ExpertsPage } from "@/features/experts/ExpertsPage";
import { AgentsPage } from "@/features/agents/AgentsPage";
import { McpPage } from "@/features/mcp/McpPage";
import { AdminPage } from "@/features/admin/AdminPage";
import { EvalPage } from "@/features/eval/EvalPage";
import { PlatformPage } from "@/features/platform/PlatformPage";

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
      { path: "experts", element: <ExpertsPage /> },
      { path: "skills", element: <SkillsPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "mcp", element: <McpPage /> },
      { path: "eval", element: <EvalPage /> },
      { path: "platform", element: <PlatformPage /> },
      { path: "admin", element: <AdminPage /> },
    ],
  },
]);

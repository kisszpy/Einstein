import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { ChatPage } from "@/features/chat/ChatPage";
import { KbPage } from "@/features/kb/KbPage";
import { SkillsPage } from "@/features/skills/SkillsPage";
import { ExpertsPage } from "@/features/experts/ExpertsPage";
import { AgentsPage } from "@/features/agents/AgentsPage";
import { McpPage } from "@/features/mcp/McpPage";
import { AdminPage } from "@/features/admin/AdminPage";
import { EvalPage } from "@/features/eval/EvalPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "kb", element: <KbPage /> },
      { path: "experts", element: <ExpertsPage /> },
      { path: "skills", element: <SkillsPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "mcp", element: <McpPage /> },
      { path: "eval", element: <EvalPage /> },
      { path: "admin", element: <AdminPage /> },
    ],
  },
]);

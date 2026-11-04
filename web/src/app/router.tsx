import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { ChatPage } from "@/features/chat/ChatPage";
import { PlaceholderPage } from "./PlaceholderPage";
import { KbPage } from "@/features/kb/KbPage";
import { SkillsPage } from "@/features/skills/SkillsPage";
import { ExpertsPage } from "@/features/experts/ExpertsPage";
import { AgentsPage } from "@/features/agents/AgentsPage";

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
      { path: "mcp", element: <PlaceholderPage title="MCP" desc="Server 注册、工具发现与审核" /> },
      { path: "admin", element: <PlaceholderPage title="系统管理" desc="用户权限 · 模型接入 · 观测 · 审计" /> },
    ],
  },
]);

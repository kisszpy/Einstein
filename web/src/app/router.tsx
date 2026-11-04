import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./layout/AppLayout";
import { ChatPage } from "@/features/chat/ChatPage";
import { PlaceholderPage } from "./PlaceholderPage";
import { KbPage } from "@/features/kb/KbPage";
import { AgentsPage } from "@/features/agents/AgentsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "kb", element: <KbPage /> },
      { path: "experts", element: <PlaceholderPage title="专家" desc="分步编辑器：人设 → 技能 → 知识库 → 模型参数" /> },
      { path: "skills", element: <PlaceholderPage title="技能" desc="技能包管理与沙箱调试" /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "mcp", element: <PlaceholderPage title="MCP" desc="Server 注册、工具发现与审核" /> },
      { path: "admin", element: <PlaceholderPage title="系统管理" desc="用户权限 · 模型接入 · 观测 · 审计" /> },
    ],
  },
]);

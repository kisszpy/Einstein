import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { router } from "./app/router";
import { bootstrap, installAuthHeader } from "./lib/auth";
import { installWorkspaceHeader } from "./lib/workspace";
import "./styles/globals.css";

installAuthHeader();
installWorkspaceHeader();
void bootstrap();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* 最外层兜底：路由/布局自身崩了也不至于白屏，至少给个刷新入口 */}
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);

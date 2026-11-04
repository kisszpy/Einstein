import { NavLink, Outlet } from "react-router-dom";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/chat", label: "对话" },
  { to: "/kb", label: "知识库" },
  { to: "/experts", label: "专家" },
  { to: "/skills", label: "技能" },
  { to: "/agents", label: "智能体" },
  { to: "/mcp", label: "MCP" },
];

const NAV_ADMIN = [{ to: "/admin", label: "系统管理" }];

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "block rounded-lg px-3 py-2 text-[13px] transition-colors duration-150",
          isActive
            ? "bg-brand-soft font-medium text-brand"
            : "text-secondary hover:bg-surface-3 hover:text-primary",
        )
      }
    >
      {label}
    </NavLink>
  );
}

export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* 侧导航 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface-2">
        <div className="flex h-14 items-center px-5">
          <span className="text-[15px] font-medium tracking-wide">AI 平台</span>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {NAV.map((item) => (
            <NavItem key={item.to} {...item} />
          ))}
          <div className="my-3 border-t border-line" />
          {NAV_ADMIN.map((item) => (
            <NavItem key={item.to} {...item} />
          ))}
        </nav>
        <div className="px-3 pb-4 text-xs text-tertiary">v0.1.0 · M0</div>
      </aside>

      {/* 主区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-end gap-3 border-b border-line px-6">
          <ThemeToggle />
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

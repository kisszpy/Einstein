import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Blocks,
  Bot,
  Cpu,
  Database,
  FlaskConical,
  Globe,
  KeyRound,
  MessagesSquare,
  Settings2,
  type LucideIcon,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface NavEntry {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 命中这些路径都算激活（聚合入口用，如 专家&技能&MCP） */
  match?: string[];
}

const NAV: { title: string; items: NavEntry[] }[] = [
  {
    title: "工作台",
    items: [
      { to: "/chat", label: "对话", icon: MessagesSquare },
      { to: "/browser", label: "浏览器", icon: Globe },
      { to: "/kb", label: "知识库", icon: Database },
      {
        to: "/experts",
        match: ["/experts", "/skills", "/mcp"],
        label: "专家 & 技能 & MCP",
        icon: Blocks,
      },
      { to: "/agents", label: "智能体", icon: Bot },
      { to: "/models", label: "模型", icon: Cpu },
    ],
  },
  {
    title: "平台管理",
    items: [
      { to: "/eval", label: "评测", icon: FlaskConical },
      { to: "/platform", label: "开放平台", icon: KeyRound },
      { to: "/admin", label: "系统管理", icon: Settings2 },
    ],
  },
];

function NavItem({ to, label, icon: Icon, match }: NavEntry) {
  const { pathname } = useLocation();
  const isActive = (match ?? [to]).some((p) => pathname === p || pathname.startsWith(`${p}/`));
  return (
    <Link
      to={to}
      className={cn(
        "group relative flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] transition-colors duration-150",
        isActive
          ? "bg-brand-soft font-medium text-brand"
          : "text-secondary hover:bg-surface-3 hover:text-primary",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-1/2 h-3.5 w-[2.5px] -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-150",
          isActive ? "opacity-100" : "opacity-0",
        )}
      />
      <Icon
        size={15}
        strokeWidth={1.75}
        className={cn(
          "shrink-0 transition-colors duration-150",
          isActive ? "text-brand" : "text-tertiary group-hover:text-secondary",
        )}
      />
      <span className="truncate">{label}</span>
    </Link>
  );
}

export function AppLayout() {
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const groups = NAV.map((group) =>
    isAdmin
      ? group
      : { ...group, items: group.items.filter((item) => item.to !== "/admin") },
  );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* 侧导航 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface-2">
        <div className="flex h-14 items-center gap-2.5 px-4">
          <span className="flex h-6 w-6 items-center justify-center rounded-[7px] bg-brand text-[11px] font-semibold tracking-tight text-brand-fg">
            E
          </span>
          <span className="text-[15px] font-medium tracking-wide">Einstein</span>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 pb-4">
          {groups.map((group) => (
            <div key={group.title}>
              <p className="px-2.5 pb-1.5 text-[11px] tracking-wide text-tertiary">
                {group.title}
              </p>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavItem key={item.to} {...item} />
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="flex items-center gap-1.5 border-t border-line px-4 py-3 text-[11.5px] text-tertiary">
          <span className="h-1 w-1 rounded-full bg-tertiary" />
          v{__APP_VERSION__} · M14
        </div>
      </aside>

      {/* 主区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line px-6">
          <WorkspaceSwitcher />
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <span className="h-4 w-px bg-line-strong" />
            <UserMenu />
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

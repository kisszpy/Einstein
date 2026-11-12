import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Activity,
  Archive,
  Blocks,
  Bot,
  Cpu,
  Database,
  FlaskConical,
  KeyRound,
  MessagesSquare,
  Menu,
  Presentation,
  Projector,
  Settings2,
  X,
  type LucideIcon,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { DialogHost } from "@/lib/confirm";
import { UserMenu } from "./UserMenu";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { useAuth } from "@/lib/auth";
import { BrandMark } from "@/components/BrandMark";
import { cn } from "@/lib/utils";

interface NavEntry {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 命中这些路径都算激活（聚合入口用，如 专家 · 技能 · MCP） */
  match?: string[];
}

const NAV: { title: string; items: NavEntry[] }[] = [
  {
    title: "工作台",
    items: [
      { to: "/chat", label: "对话", icon: MessagesSquare },
      { to: "/ppt-studio", label: "AI PPT", icon: Presentation },
      { to: "/assets", label: "资产", icon: Archive },
      { to: "/deckcraft", label: "放映级编辑", icon: Projector },
      { to: "/kb", label: "知识库", icon: Database },
      {
        to: "/experts",
        match: ["/experts", "/skills", "/mcp"],
        label: "专家 · 技能 · MCP",
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
      { to: "/ops", label: "运营", icon: Activity },
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
        "group relative flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] transition-colors duration-[var(--dur-fast)]",
        isActive
          ? "bg-brand-soft font-medium text-brand-ink"
          : "text-secondary hover:bg-surface-3 hover:text-primary",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-1/2 h-4 w-[2.5px] -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-[var(--dur-fast)]",
          isActive ? "opacity-100" : "opacity-0",
        )}
      />
      <Icon
        size={15}
        strokeWidth={1.75}
        className={cn(
          "shrink-0 transition-colors duration-[var(--dur-fast)]",
          isActive ? "text-brand-ink" : "text-tertiary group-hover:text-secondary",
        )}
      />
      <span className="truncate">{label}</span>
    </Link>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const groups = NAV.map((group) =>
    isAdmin
      ? group
      : { ...group, items: group.items.filter((item) => item.to !== "/admin") },
  );

  return (
    <>
      <div className="flex h-14 items-center gap-2.5 px-3.5">
        <BrandMark size={26} />
        <span className="text-[15px] font-semibold tracking-tight">Einstein</span>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-2.5 pb-4 pt-1" onClick={onNavigate}>
        {groups.map((group) => (
          <div key={group.title}>
            <p className="px-2.5 pb-1.5 text-[11px] font-medium tracking-[0.06em] text-tertiary">
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
        <span className="h-1.5 w-1.5 rounded-full bg-success" title="服务正常" />
        <span className="tabular-nums">v{__APP_VERSION__}</span>
      </div>
    </>
  );
}

export function AppLayout() {
  const [drawer, setDrawer] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => {
    setDrawer(false);
  }, [pathname]);

  useEffect(() => {
    if (!drawer) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, [drawer]);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* 侧导航：≥lg 常驻；<lg 收为抽屉，避免窄屏直接丢掉全部导航 */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface-2 lg:flex">
        <SidebarBody />
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setDrawer(false)} />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-line bg-surface-2 shadow-[var(--shadow-pop)] transition-transform duration-[var(--dur-base)] lg:hidden",
          drawer ? "translate-x-0" : "-translate-x-full",
        )}
        aria-hidden={!drawer}
      >
        <button
          type="button"
          aria-label="关闭导航"
          onClick={() => setDrawer(false)}
          className="absolute right-2.5 top-3.5 rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-3 hover:text-primary"
        >
          <X size={16} />
        </button>
        <SidebarBody onNavigate={() => setDrawer(false)} />
      </aside>

      {/* 主区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line px-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              aria-label="打开导航"
              onClick={() => setDrawer(true)}
              className="-ml-1 rounded-md p-1.5 text-secondary transition-colors hover:bg-surface-3 hover:text-primary lg:hidden"
            >
              <Menu size={18} />
            </button>
            <BrandMark size={22} className="hidden sm:block lg:hidden" />
            <WorkspaceSwitcher />
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <span className="hidden h-4 w-px bg-line-strong sm:block" />
            <UserMenu />
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      {/* 全站命令式 Confirm/Prompt 对话框宿主（替代 window.confirm/alert/prompt） */}
      <DialogHost />
    </div>
  );
}

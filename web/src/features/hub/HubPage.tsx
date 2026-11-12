/**
 * 专家 · 技能 · MCP 聚合页：一个入口，三个页签。
 * /experts、/skills、/mcp 三个路由都落到这里，按路径高亮对应页签，
 * 旧的深链（如收藏夹里的 /skills）不用改。
 */

import { Link, useLocation } from "react-router-dom";
import { ExpertsPage } from "@/features/experts/ExpertsPage";
import { SkillsPage } from "@/features/skills/SkillsPage";
import { McpPage } from "@/features/mcp/McpPage";
import { cn } from "@/lib/utils";

const TABS = [
  { path: "/experts", label: "专家" },
  { path: "/skills", label: "技能" },
  { path: "/mcp", label: "MCP" },
] as const;

export function HubPage() {
  const { pathname } = useLocation();
  const active = TABS.find((t) => pathname.startsWith(t.path)) ?? TABS[0];

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 页签栏 */}
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line px-4">
        {TABS.map((tab) => {
          const on = tab.path === active.path;
          return (
            <Link
              key={tab.path}
              to={tab.path}
              className={cn(
                "relative rounded-lg px-3 py-1.5 text-[13px] transition-colors duration-150",
                on ? "font-medium text-primary" : "text-secondary hover:bg-hover hover:text-primary",
              )}
            >
              {tab.label}
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-3 -bottom-[9px] h-[2px] rounded-full bg-brand transition-opacity duration-150",
                  on ? "opacity-100" : "opacity-0",
                )}
              />
            </Link>
          );
        })}
      </div>

      <div className="min-h-0 flex-1">
        {active.path === "/experts" && <ExpertsPage />}
        {active.path === "/skills" && <SkillsPage />}
        {active.path === "/mcp" && <McpPage />}
      </div>
    </div>
  );
}

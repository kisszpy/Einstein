import { Link, useLocation } from "react-router-dom";

import { cn } from "@/lib/utils";

/**
 * 404 / 路由级兜底页。
 *
 * 之前没有 catch-all，路径写错时 React Router 会直接抛出它自带的
 * 「404 Not Found / Hey developer 👋」开发提示页，用户看到的是框架文案。
 * 这里给一套产品化的外观，并把当前路径回显出来，方便定位是链接错了还是路由没注册。
 */
export function NotFoundPage() {
  const { pathname } = useLocation();

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="font-mono text-[44px] leading-none text-tertiary">404</div>
      <div className="space-y-1">
        <h1 className="text-[15px] font-medium text-primary">这个路径不存在</h1>
        <p className="text-[12.5px] text-secondary">
          没有匹配到 <code className="rounded bg-surface-3 px-1.5 py-0.5 font-mono">{pathname}</code>
          对应的页面。
        </p>
      </div>
      <Link
        to="/chat"
        className={cn(
          "rounded-md border border-line bg-surface-1 px-3 py-1.5 text-[12.5px] text-primary",
          "transition-colors hover:bg-surface-2",
        )}
      >
        回到对话
      </Link>
    </div>
  );
}

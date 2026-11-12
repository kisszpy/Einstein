import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * 页面头：标题 + 说明 + 右侧操作。
 * 之前各业务页各写一套 h1 尺寸和内边距，扫读时层级不齐。
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("page-head", className)}>
      <div className="min-w-0 flex-1">
        <h1 className="page-title">{title}</h1>
        {description && <p className="page-desc">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** 业务页内容容器：统一左右留白与最大宽度 */
export function PageBody({
  children,
  className,
  width = "wide",
}: {
  children: ReactNode;
  className?: string;
  width?: "wide" | "narrow";
}) {
  return (
    <div
      className={cn(
        "mx-auto px-5 py-7 sm:px-8",
        width === "narrow" ? "max-w-[880px]" : "max-w-[1240px]",
        className,
      )}
    >
      {children}
    </div>
  );
}

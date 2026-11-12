import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 空状态：图标 + 标题 + 说明 + 可选操作。
 * 之前空状态都是「一句 12px 灰字塞进空卡片」，是页面未完成感的主要来源。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
  compact,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-card border border-dashed border-line-control bg-surface-3/50 text-center",
        compact ? "px-6 py-8" : "px-6 py-12",
        className,
      )}
    >
      {Icon && (
        <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-brand-soft text-brand-ink">
          <Icon size={18} strokeWidth={1.75} />
        </span>
      )}
      <p className="text-[14px] font-medium text-primary">{title}</p>
      {description && (
        <p className="mt-1.5 max-w-[360px] text-[12.5px] leading-5 text-secondary">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

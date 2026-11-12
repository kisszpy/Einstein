import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** 指标卡：标签 + 主数值 + 辅助说明，数据页顶部一排用 */
export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: "default" | "brand" | "warning";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-card border border-line bg-surface-1 p-4 shadow-card",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] text-tertiary">{label}</p>
        {Icon && <Icon size={14} strokeWidth={1.75} className="text-tertiary" />}
      </div>
      <p
        className={cn(
          "mt-2 text-[20px] font-semibold leading-tight tabular-nums",
          tone === "brand" && "text-brand-ink",
          tone === "warning" && "text-warning",
          tone === "default" && "text-primary",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-[11.5px] text-tertiary">{hint}</p>}
    </div>
  );
}

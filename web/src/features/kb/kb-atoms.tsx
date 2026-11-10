import { cn } from "@/lib/utils";

/** 只读徽标：样式风格对齐 VisibilityBadge，但只读属于权限态，单独定义。 */
export function ReadonlyBadge({ className }: { className?: string }) {
  return (
    <span
      title="只有查看权限，无法修改"
      className={cn(
        "shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-tertiary",
        className,
      )}
    >
      只读
    </span>
  );
}

export function LinkIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

import { cn } from "@/lib/utils";

/** 三档可见性：private 仅自己 / workspace 同工作区可读 / shared 跨工作区可读 */
export type Visibility = "private" | "workspace" | "shared";

export const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "私有" },
  { value: "workspace", label: "工作区" },
  { value: "shared", label: "公开" },
];

export const VISIBILITY_LABEL: Record<Visibility, string> = {
  private: "私有",
  workspace: "工作区",
  shared: "公开",
};

export const VISIBILITY_HINT: Record<Visibility, string> = {
  private: "仅自己可见",
  workspace: "同工作区成员可读",
  shared: "跨工作区可读",
};

const BADGE_STYLE: Record<Visibility, string> = {
  private: "bg-surface-3 text-tertiary",
  workspace: "bg-brand-soft text-brand",
  shared: "bg-warning/15 text-warning",
};

export function VisibilityBadge({
  value,
  className,
}: {
  value?: string | null;
  className?: string;
}) {
  const v = (value ?? "private") as Visibility;
  const label = VISIBILITY_LABEL[v] ?? value ?? "私有";
  return (
    <span
      title={VISIBILITY_HINT[v] ?? label}
      className={cn("rounded px-1.5 py-0.5 text-[11px]", BADGE_STYLE[v] ?? BADGE_STYLE.private, className)}
    >
      {label}
    </span>
  );
}

export function VisibilitySelect({
  value,
  onChange,
  disabled,
  className,
}: {
  value: string;
  onChange: (value: Visibility) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as Visibility)}
      className={cn(
        "h-9 rounded-lg border border-line bg-surface-1 px-2 text-[12.5px] text-primary outline-none focus:border-line-strong disabled:opacity-50 [&>option]:bg-surface-1",
        className,
      )}
    >
      {VISIBILITY_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

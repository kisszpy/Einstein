import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "ghost" | "outline";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-brand-fg hover:opacity-90",
  ghost: "text-secondary hover:bg-surface-3 hover:text-primary",
  outline: "border border-line text-primary hover:border-line-strong",
};

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={cn(
        "inline-flex h-9 items-center justify-center rounded-lg px-4 text-[13px] transition-all duration-150 disabled:pointer-events-none disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

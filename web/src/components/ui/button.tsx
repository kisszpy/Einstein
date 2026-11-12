import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "soft" | "ghost" | "outline" | "danger";

const VARIANTS: Record<Variant, string> = {
  // 主按钮用品牌色纵向渐变：大面积实心的纯 #00c29a 明度太高、太扎眼，
  // 渐变到深一档后视觉重心沉下去（登录页按钮一直是这个做法，全站对齐）。
  primary:
    "bg-linear-to-b from-brand to-brand-strong text-brand-fg shadow-[var(--shadow-btn)] hover:brightness-[1.04] active:brightness-[0.97]",
  // soft：轻量主操作（侧栏「新会话」这类），保留品牌识别但不抢正文
  soft: "bg-brand-soft text-brand-ink  hover:bg-brand/20 ",
  ghost: "text-secondary hover:bg-surface-3 hover:text-primary",
  outline: "border border-line text-primary hover:border-line-strong hover:bg-surface-3",
  // 破坏性操作：常驻可见但只在 hover 时变红，避免页面里到处是红色按钮
  danger: "border border-line text-secondary hover:border-danger/50 hover:bg-danger/10 hover:text-danger",
};

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={cn(
        "inline-flex h-9 shrink-0 items-center justify-center whitespace-nowrap rounded-lg px-4 text-[13px] transition-all duration-150 disabled:pointer-events-none disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

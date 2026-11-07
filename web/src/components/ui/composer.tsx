/**
 * 对话 / 智能体共用的输入器外壳。
 *
 * 两个页面必须长得一样、宽度一样：外壳圆角、边框、聚焦态、自动增高、工具行布局
 * 全部收在这里，页面只负责往 toolbar 里放自己的按钮，避免各自长歪。
 */

import { useLayoutEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { cn } from "@/lib/utils";

interface FrameProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

/** 圆角容器：聚焦时边框加深，和 WorkBuddy 的输入器一致。 */
export function ComposerFrame({ className, children, ...rest }: FrameProps) {
  return (
    <div
      className={cn(
        "rounded-[18px] border border-line bg-surface-1 shadow-input transition-colors duration-150 focus-within:border-line-strong",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

type InputProps = Omit<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "value" | "onChange" | "rows"
> & {
  value: string;
  onChange: (value: string) => void;
  /** 回车提交（Shift+Enter 换行，输入法组合中不提交） */
  onSubmit?: () => void;
  /** 自动增高的上限，超出后内部滚动 */
  maxHeight?: number;
};

/** 单行起步、随内容增高到上限的输入框。 */
export function ComposerInput({
  value,
  onChange,
  onSubmit,
  maxHeight = 200,
  className,
  ...rest
}: InputProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const autoFocus = rest.autoFocus;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, [value, maxHeight]);

  useLayoutEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  return (
    <textarea
      {...rest}
      ref={ref}
      rows={1}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey && !(e.nativeEvent as KeyboardEvent).isComposing) {
          e.preventDefault();
          onSubmit?.();
        }
      }}
      style={{ maxHeight }}
      className={cn(
        "w-full resize-none bg-transparent px-3.5 pb-1 pt-3 text-[14px] leading-[22px] text-primary outline-none placeholder:text-placeholder",
        className,
      )}
    />
  );
}

/** 输入框下方的工具行：左（能力/知识库/联网）右（发送） */
export function ComposerToolbar({
  left,
  right,
  className,
}: {
  left?: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1 px-2 pb-2 pt-0.5", className)}>
      {left}
      <div className="ml-auto flex items-center gap-1.5">{right}</div>
    </div>
  );
}

/** 圆形发送 / 停止按钮。 */
export function ComposerSendButton({
  running,
  disabled,
  onSend,
  onStop,
  labels,
}: {
  running: boolean;
  disabled: boolean;
  onSend: () => void;
  onStop: () => void;
  labels?: { send?: string; stop?: string };
}) {
  const sendLabel = labels?.send ?? "发送";
  const stopLabel = labels?.stop ?? "停止运行";
  return (
    <button
      type="button"
      onClick={running ? onStop : onSend}
      disabled={!running && disabled}
      aria-label={running ? stopLabel : sendLabel}
      title={running ? stopLabel : `${sendLabel}（Enter）`}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded-full transition-all duration-150",
        running
          ? "bg-primary text-surface-1 hover:opacity-90"
          : "bg-primary text-surface-1 enabled:hover:opacity-90 disabled:bg-surface-3 disabled:text-tertiary",
      )}
    >
      {running ? (
        <Square size={10} strokeWidth={2.5} fill="currentColor" />
      ) : (
        <ArrowUp size={15} strokeWidth={2.6} />
      )}
    </button>
  );
}

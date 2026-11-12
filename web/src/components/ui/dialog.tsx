/**
 * 弹窗三件套：Modal（Portal + Escape + 遮罩点击关闭）/ ConfirmDialog / PromptDialog。
 * 全站 22 处 window.confirm/alert/prompt 的统一替换件——原生弹窗无法统一样式、
 * 无法做 a11y，且 prompt 明文输入密码属于安全和可用性双输。
 *
 * 用法：
 *   <ConfirmDialog open={x} title="删除会话" message="…" danger onConfirm={} onClose={} />
 *   <PromptDialog open={x} title="改密码" placeholder="新密码" type="password" onConfirm={(v)=>} onClose={} />
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

function ModalShell({
  open,
  onClose,
  children,
  width = "max-w-sm",
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  width?: string;
}) {
  // Escape 关闭 + 打开期间锁定 body 滚动，遮罩下内容不再乱滚
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
      onMouseDown={(e) => {
        // 点遮罩关闭；mousedown 避免拖选文本到遮罩外松手误关
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          "w-full rounded-card border border-line bg-surface-1 p-4 shadow-float",
          width,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

const BUTTON_BASE =
  "rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors duration-150 disabled:opacity-40";

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger = false,
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 破坏性操作：确认键与边框用 danger 色，别让"删除"和"确定"长得一样 */
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <ModalShell open={open} onClose={onClose}>
      <h3 className="text-[14px] font-medium">{title}</h3>
      <div className="mt-1.5 text-[13px] leading-relaxed text-secondary">{message}</div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onClose} className={cn(BUTTON_BASE, "border border-line text-secondary hover:bg-surface-3")}>
          {cancelLabel}
        </button>
        <button
          type="button"
          autoFocus
          disabled={busy}
          onClick={onConfirm}
          className={cn(
            BUTTON_BASE,
            danger
              ? "bg-danger text-danger-fg hover:opacity-90"
              : "bg-primary text-surface-1 hover:opacity-90",
          )}
        >
          {confirmLabel}
        </button>
      </div>
    </ModalShell>
  );
}

export function PromptDialog({
  open,
  title,
  message,
  placeholder,
  defaultValue = "",
  type = "text",
  confirmLabel = "确认",
  busy = false,
  validate,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message?: React.ReactNode;
  placeholder?: string;
  defaultValue?: string;
  /** password：内置显隐切换，替代 prompt 明文输密码 */
  type?: "text" | "password";
  confirmLabel?: string;
  busy?: boolean;
  /** 返回错误文案表示校验失败，返回 null 放行 */
  validate?: (value: string) => string | null;
  onConfirm: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setValue(defaultValue);
      setError(null);
      setShow(false);
      // Portal 挂载后聚焦，键盘用户进来就能输入
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, defaultValue]);

  const submit = () => {
    const v = value.trim();
    const err = validate?.(v) ?? null;
    if (err) {
      setError(err);
      return;
    }
    onConfirm(v);
  };

  return (
    <ModalShell open={open} onClose={onClose}>
      <h3 className="text-[14px] font-medium">{title}</h3>
      {message && <div className="mt-1.5 text-[13px] leading-relaxed text-secondary">{message}</div>}
      <div className="relative mt-3">
        <input
          ref={inputRef}
          type={show ? "text" : type}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !(e.nativeEvent as KeyboardEvent).isComposing) submit();
          }}
          placeholder={placeholder}
          className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] text-primary outline-none transition-colors placeholder:text-placeholder focus-within:border-line-strong"
        />
        {type === "password" && (
          <button
            type="button"
            onClick={() => setShow((v) => !v)}
            title={show ? "隐藏密码" : "显示密码"}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-1 text-xs text-tertiary transition-colors hover:text-secondary"
          >
            {show ? "隐藏" : "显示"}
          </button>
        )}
      </div>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onClose} className={cn(BUTTON_BASE, "border border-line text-secondary hover:bg-surface-3")}>
          取消
        </button>
        <button
          type="button"
          disabled={busy || !value.trim()}
          onClick={submit}
          className={cn(BUTTON_BASE, "bg-primary text-surface-1 hover:opacity-90")}
        >
          {confirmLabel}
        </button>
      </div>
    </ModalShell>
  );
}

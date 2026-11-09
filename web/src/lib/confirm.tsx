/**
 * 命令式对话框服务：confirmDialog()/promptDialog() 返回 Promise，
 * 调用侧像 window.confirm 一样一行替换，但样式、a11y、密码显隐全部可控。
 *
 * AppLayout 需挂载 <DialogHost />（全局唯一实例）。
 */

import { useEffect, useState } from "react";
import { ConfirmDialog, PromptDialog } from "@/components/ui/dialog";

export interface ConfirmOptions {
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  /** 破坏性操作置 true：确认键变红色 */
  danger?: boolean;
}

interface ConfirmState extends ConfirmOptions {
  resolve: (v: boolean) => void;
}

interface PromptState {
  title: string;
  message?: React.ReactNode;
  placeholder?: string;
  defaultValue?: string;
  type?: "text" | "password";
  confirmLabel?: string;
  validate?: (value: string) => string | null;
  resolve: (v: string | null) => void;
}

let confirmListener: ((state: ConfirmState) => void) | null = null;
let promptListener: ((state: PromptState) => void) | null = null;

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    confirmListener?.({ ...opts, resolve });
  });
}

export function promptDialog(
  opts: Omit<PromptState, "resolve">,
): Promise<string | null> {
  return new Promise((resolve) => {
    promptListener?.({ ...opts, resolve });
  });
}

/** 全局对话框宿主：AppLayout 挂载一次。 */
export function DialogHost() {
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [promptState, setPromptState] = useState<PromptState | null>(null);
  useEffect(() => {
    confirmListener = (state) => setConfirmState(state);
    promptListener = (state) => setPromptState(state);
    return () => {
      confirmListener = null;
      promptListener = null;
    };
  }, []);

  return (
    <>
      <ConfirmDialog
        open={confirmState !== null}
        title={confirmState?.title ?? ""}
        message={confirmState?.message ?? ""}
        confirmLabel={confirmState?.confirmLabel}
        danger={confirmState?.danger}
        onClose={() => {
          confirmState?.resolve(false);
          setConfirmState(null);
        }}
        onConfirm={() => {
          if (!confirmState) return;
          const { resolve } = confirmState;
          setConfirmState(null);
          resolve(true);
        }}
      />
      <PromptDialog
        open={promptState !== null}
        title={promptState?.title ?? ""}
        message={promptState?.message}
        placeholder={promptState?.placeholder}
        defaultValue={promptState?.defaultValue}
        type={promptState?.type}
        confirmLabel={promptState?.confirmLabel}
        validate={promptState?.validate}
        onClose={() => {
          promptState?.resolve(null);
          setPromptState(null);
        }}
        onConfirm={(v) => {
          if (!promptState) return;
          const { resolve } = promptState;
          setPromptState(null);
          resolve(v);
        }}
      />
    </>
  );
}

/**
 * 右侧抽屉：复用资产页的 DeckCraftStage（放映级 wasm 编辑器）直接改刚生成的 pptx。
 * 宽度 min(720px, 92vw)，可切全屏；关闭前 confirmDialog 二次确认（编辑器里的
 * 未保存改动不归我们管，宁可多问一次）。
 */

import { useState } from "react";
import { Maximize2, Minimize2, X } from "lucide-react";
import { confirmDialog } from "@/lib/confirm";
import { DeckCraftStage } from "@/features/browser/components/DeckCraftStage";
import type { DeckPayload } from "../types";

interface Props {
  deck: DeckPayload | null;
  onClose: () => void;
  /** 编辑器写回成功后回调（上层可刷新产物列表）。 */
  onSaved?: () => void;
}

export function DeckDrawer({ deck, onClose, onSaved }: Props) {
  const [fullscreen, setFullscreen] = useState(false);

  if (!deck) return null;

  const handleClose = async () => {
    const ok = await confirmDialog({
      title: "关闭编辑器",
      message: "未保存的修改会留在编辑器里（可 Ctrl+S 写回项目）。确定关闭吗？",
      confirmLabel: "关闭",
    });
    if (ok) onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="演示编辑器">
      <div className="absolute inset-0 bg-black/40" onClick={() => void handleClose()} />
      <div
        className="relative flex min-w-0 flex-col border-l border-line bg-surface-1 shadow-2xl transition-[width] duration-200"
        style={{ width: fullscreen ? "100vw" : "min(720px, 92vw)" }}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
          <p className="min-w-0 flex-1 truncate text-[13px] font-medium text-primary" title={deck.name}>
            {deck.name}
          </p>
          <button
            type="button"
            onClick={() => setFullscreen((v) => !v)}
            title={fullscreen ? "退出全屏" : "全屏编辑"}
            className="rounded p-1.5 text-tertiary transition-colors hover:bg-hover hover:text-primary"
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <button
            type="button"
            onClick={() => void handleClose()}
            title="关闭编辑器"
            className="rounded p-1.5 text-tertiary transition-colors hover:bg-hover hover:text-primary"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1">
          <DeckCraftStage
            project={deck.project}
            path={deck.path}
            ticket={deck.ticket}
            onSaved={onSaved}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * 智能体页右侧的预览面板：与 /browser 独立页共用一套工作台，
 * 区别只是窄、可收起文件栏、编辑器换紧凑版式。
 */

import { Maximize2, X } from "lucide-react";
import { BrowserWorkbench } from "./BrowserWorkbench";
import type { BrowserState } from "./types";

interface Props {
  value: BrowserState;
  onChange: (patch: Partial<BrowserState>) => void;
  onClose: () => void;
  onMaximize: () => void;
  refreshToken?: number;
}

export function PreviewPanel({ value, onChange, onClose, onMaximize, refreshToken }: Props) {
  return (
    <BrowserWorkbench
      value={value}
      onChange={onChange}
      compact
      refreshToken={refreshToken}
      headerRight={
        <>
          <button
            onClick={onMaximize}
            title="在资产页打开"
            aria-label="在资产页打开"
            className="rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-3 hover:text-primary"
          >
            <Maximize2 size={13} strokeWidth={1.9} />
          </button>
          <button
            onClick={onClose}
            title="关闭预览"
            aria-label="关闭预览"
            className="rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-3 hover:text-primary"
          >
            <X size={14} strokeWidth={1.9} />
          </button>
        </>
      }
    />
  );
}

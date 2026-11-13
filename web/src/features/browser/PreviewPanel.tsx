/**
 * 智能体页右侧的预览面板：与 /browser 独立页共用一套工作台，
 * 区别只是窄、可收起文件栏、编辑器换紧凑版式。
 */

import { ExternalLink, Maximize2, Minimize2, X } from "lucide-react";
import { BrowserWorkbench } from "./BrowserWorkbench";
import type { BrowserState } from "./types";

interface Props {
  value: BrowserState;
  onChange: (patch: Partial<BrowserState>) => void;
  onClose: () => void;
  /** 是否已铺满整页（就地全屏，不跳页——跳页会丢掉当前任务） */
  maximized: boolean;
  onToggleMaximize: () => void;
  /** 窄屏下面板本身就是全幅，全屏按钮没有意义 */
  canMaximize?: boolean;
  /** 真正跳到独立资产页：会离开当前任务，所以只在全屏时露出一次 */
  onOpenInAssets?: () => void;
  refreshToken?: number;
}

const BTN =
  "rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-3 hover:text-primary";

export function PreviewPanel({
  value,
  onChange,
  onClose,
  maximized,
  onToggleMaximize,
  canMaximize = true,
  onOpenInAssets,
  refreshToken,
}: Props) {
  return (
    <BrowserWorkbench
      value={value}
      onChange={onChange}
      compact
      refreshToken={refreshToken}
      headerRight={
        <>
          {maximized && onOpenInAssets && (
            <button
              onClick={onOpenInAssets}
              title="在资产页打开（会离开当前任务）"
              aria-label="在资产页打开"
              className={BTN}
            >
              <ExternalLink size={13} strokeWidth={1.9} />
            </button>
          )}
          {canMaximize && (
            <button
              onClick={onToggleMaximize}
              title={maximized ? "退出全屏（Esc）" : "全屏展开"}
              aria-label={maximized ? "退出全屏" : "全屏展开"}
              aria-pressed={maximized}
              className={BTN}
            >
              {maximized ? (
                <Minimize2 size={13} strokeWidth={1.9} />
              ) : (
                <Maximize2 size={13} strokeWidth={1.9} />
              )}
            </button>
          )}
          <button onClick={onClose} title="关闭预览" aria-label="关闭预览" className={BTN}>
            <X size={14} strokeWidth={1.9} />
          </button>
        </>
      }
    />
  );
}

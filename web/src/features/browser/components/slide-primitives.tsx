import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/**
 * 幻灯片渲染的公共基元：单位换算 + 就地编辑。
 *
 * 这些从 SlideCanvas 里拆出来有两个理由：一是换算函数（磅→cqw、英寸→%）是
 * 与服务端 ppt.py 对齐的硬契约，混在 1300 行的版式 switch 里没人敢动；
 * 二是 Editable 有自己的输入法/焦点逻辑，值得单独测。
 */

/** 幻灯片宽 13.333 英寸（16:9），版式换算的基准。 */
export const SLIDE_W = 13.333;
/** 统一页边距，与服务端 PPT_TO_PX 保持一致。 */
export const MARGIN = 0.9;

/** 英寸 → 画布宽度百分比。 */
export const pct = (inch: number) => `${(inch / SLIDE_W) * 100}%`;
/** 磅 → 容器查询宽度单位（幻灯片宽 960pt，正好是容器的 100cqw）。 */
export const pt = (size: number) => `${(size / 960) * 100}cqw`;

/** 主题色存的是不带 # 的十六进制；空值退回 fallback。 */
export const hex = (value: string, fallback: string) => (value ? `#${value}` : fallback);

export interface EditableProps {
  value: string;
  onCommit?: (next: string) => void;
  style?: React.CSSProperties;
  className?: string;
  placeholder?: string;
  editable: boolean;
}

/**
 * contentEditable 只在失焦时回写：每次按键都 onChange 会让 React 重渲染、
 * 光标跳回开头，中文输入法下更会打断候选词。
 */
export function Editable({ value, onCommit, style, className, placeholder, editable }: EditableProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const focused = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (el && !focused.current && el.innerText !== value) el.innerText = value;
  }, [value]);

  if (!editable) {
    return (
      <div style={style} className={className}>
        {value}
      </div>
    );
  }
  return (
    <div
      ref={ref}
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      data-placeholder={placeholder}
      style={style}
      className={cn(className, "outline-none focus:ring-1 focus:ring-brand/60 rounded-[2px]")}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={(event) => {
        focused.current = false;
        // contentEditable 会把空格写成 &nbsp;，不回正就会在 pptx 里留一串方框
        onCommit?.(event.currentTarget.innerText.replace(/\u00a0/g, " ").trim());
      }}
      onKeyDown={(event) => {
        // 画布外面有全局快捷键（撤销/删除页），编辑文本时必须拦住
        event.stopPropagation();
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          event.currentTarget.blur();
        }
        if (event.key === "Escape") event.currentTarget.blur();
      }}
    />
  );
}

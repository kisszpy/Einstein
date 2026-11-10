import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Editable, hex, pct, pt } from "./slide-primitives";

/**
 * 画布基元是「所见即所得」的地基：
 * - 换算函数错一位，所有版式都会错位（且只有肉眼看 pptx 才发现）；
 * - Editable 的提交时机错一次，中文输入法就会被打断。
 */

describe("单位换算", () => {
  it("英寸按 13.333 英寸基准换算成百分比", () => {
    expect(pct(13.333)).toBe("100%");
    expect(pct(6.6665)).toBe("50%");
  });

  it("磅按 960pt 基准换算成 cqw", () => {
    expect(pt(960)).toBe("100cqw");
    expect(pt(48)).toBe("5cqw");
  });

  it("主题色空值退回 fallback，否则补 #", () => {
    expect(hex("00c29a", "#000")).toBe("#00c29a");
    expect(hex("", "#fff")).toBe("#fff");
  });
});

describe("Editable", () => {
  it("不可编辑时只渲染纯文本，不带 contenteditable", () => {
    const { container } = render(<Editable value="标题" editable={false} />);
    expect(screen.getByText("标题")).toBeInTheDocument();
    expect(container.querySelector("[contenteditable]")).toBeNull();
  });

  it("可编辑时失焦回写，并清掉 contentEditable 的 &nbsp;", () => {
    const onCommit = vi.fn();
    const { container } = render(<Editable value="旧标题" editable onCommit={onCommit} />);
    const el = container.querySelector("[contenteditable]") as HTMLDivElement;

    el.innerText = "  新标题\u00a0 ";
    fireEvent.blur(el);

    expect(onCommit).toHaveBeenCalledWith("新标题");
  });

  it("Enter 提交并失焦，Shift+Enter 留给换行", () => {
    const onCommit = vi.fn();
    const { container } = render(<Editable value="x" editable onCommit={onCommit} />);
    const el = container.querySelector("[contenteditable]") as HTMLDivElement;
    const blur = vi.spyOn(el, "blur");

    fireEvent.keyDown(el, { key: "Enter", shiftKey: true });
    expect(blur).not.toHaveBeenCalled();

    fireEvent.keyDown(el, { key: "Enter" });
    expect(blur).toHaveBeenCalled();
  });

  it("按键事件不外冒：画布外层有撤销/删页的全局快捷键", () => {
    const outer = vi.fn();
    const { container } = render(
      <div onKeyDown={outer}>
        <Editable value="x" editable onCommit={() => {}} />
      </div>,
    );
    const el = container.querySelector("[contenteditable]") as HTMLDivElement;

    fireEvent.keyDown(el, { key: "Delete" });
    expect(outer).not.toHaveBeenCalled();
  });
});

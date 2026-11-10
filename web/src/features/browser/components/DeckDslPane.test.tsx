import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DeckDslPane } from "./DeckDslPane";

/**
 * DSL 面板是「改一页 PPT」的唯一入口，它的诊断展示直接决定用户能不能
 * 看懂「为什么编译不过」。这里锁三件事：error 与 warning 分色、
 * 校验通过要有明确正反馈、没有 DSL 记录时两个按钮都得禁用。
 */

const base = {
  dsl: "<Slide><Text>hi</Text></Slide>",
  hasDsl: true,
  busy: false,
  diagnostics: null as null | Array<{ ruleId?: string; severity?: string; message?: string }>,
  error: "",
  onDslChange: () => {},
  onLint: () => {},
  onApply: () => {},
};

describe("DeckDslPane", () => {
  it("有 DSL 时给出可编辑提示与两个可用按钮", () => {
    render(<DeckDslPane {...base} />);
    expect(screen.getByText(/先校验，无 error 再编译保存/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /校验/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /编译并保存/ })).toBeEnabled();
  });

  it("没有 DSL 记录时禁用按钮并说明原因", () => {
    render(<DeckDslPane {...base} hasDsl={false} />);
    expect(screen.getByText(/没有 DSL 记录/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /校验/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /编译并保存/ })).toBeDisabled();
  });

  it("校验通过时给出正反馈（否则用户会以为按钮没反应）", () => {
    render(<DeckDslPane {...base} diagnostics={[]} />);
    expect(screen.getByText("校验通过，无诊断问题")).toBeInTheDocument();
  });

  it("error 级诊断标红，warning 不标红", () => {
    render(
      <DeckDslPane
        {...base}
        diagnostics={[
          { ruleId: "overflow", severity: "error", message: "第 1 页文字溢出" },
          { ruleId: "contrast", severity: "warning", message: "对比度偏低" },
        ]}
      />,
    );
    const errorItem = screen.getByText("第 1 页文字溢出").closest("li");
    const warnItem = screen.getByText("对比度偏低").closest("li");

    expect(errorItem?.className).toContain("text-danger");
    expect(warnItem?.className).not.toContain("text-danger");
    // 诊断要带上 ruleId，用户才能对上服务端的规则名
    expect(errorItem?.textContent).toContain("overflow");
  });

  it("接口报错时优先展示错误文本", () => {
    render(<DeckDslPane {...base} error="编译失败：转换器异常" />);
    expect(screen.getByText("编译失败：转换器异常")).toBeInTheDocument();
  });

  it("源码输入会回调到上层", async () => {
    const onDslChange = vi.fn();
    render(<DeckDslPane {...base} onDslChange={onDslChange} />);
    const box = screen.getByRole("textbox");

    await userEvent.clear(box);
    await userEvent.type(box, "x");

    expect(onDslChange).toHaveBeenCalled();
  });

  it("处理中时按钮禁用，避免重复提交", () => {
    render(<DeckDslPane {...base} busy />);
    expect(screen.getByRole("button", { name: /处理中/ })).toBeDisabled();
  });
});

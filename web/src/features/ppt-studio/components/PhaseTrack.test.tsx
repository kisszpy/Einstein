import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PhaseTrack } from "./PhaseTrack";

/**
 * 阶段条必须跟后端真实 phase 名对齐。粗粒度的 rendering（回落基础排版时只发它）
 * 也要能推进，否则用户会看到进度条走而步骤永远停在第一步。
 */

describe("PhaseTrack 成稿流水线", () => {
  it("按后端顺序展示六个步骤", () => {
    render(<PhaseTrack mode="generate" phase="deepening" progress={20} label="正在深化每页内容" />);
    for (const label of ["内容深化", "版式编排", "浏览器实测", "逐页体检", "写入 PPTX", "完成"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("20%")).toBeTruthy();
    expect(screen.getByText("正在深化每页内容")).toBeTruthy();
  });

  it("measuring 时前两步已完成、当前步高亮", () => {
    const { container } = render(
      <PhaseTrack mode="generate" phase="measuring" progress={55} label="浏览器实测" />,
    );
    const marks = Array.from(container.querySelectorAll("span.rounded-full"));
    const steps = marks.filter((el) => el.className.includes("h-4"));
    expect(steps[0]?.className).toContain("bg-brand");
    expect(steps[1]?.className).toContain("bg-brand");
    expect(steps[2]?.className).toContain("ring-2");
    expect(steps[3]?.className).toContain("bg-surface-3");
  });

  it("粗粒度 rendering 落到版式编排这一步", () => {
    const { container } = render(
      <PhaseTrack mode="generate" phase="rendering" progress={60} label="基础排版渲染" />,
    );
    const steps = Array.from(container.querySelectorAll("span.rounded-full")).filter((el) =>
      el.className.includes("h-4"),
    );
    expect(steps[0]?.className).toContain("bg-brand");
    expect(steps[1]?.className).toContain("ring-2");
  });

  it("100% 时全部步骤打勾", () => {
    const { container } = render(
      <PhaseTrack mode="generate" phase="done" progress={100} label="生成完成" />,
    );
    const steps = Array.from(container.querySelectorAll("span.rounded-full")).filter((el) =>
      el.className.includes("h-4"),
    );
    expect(steps).toHaveLength(7);
    expect(steps.every((el) => el.className.includes("bg-brand"))).toBe(true);
  });

  it("画面润色排在逐页体检与写入之间", () => {
    render(<PhaseTrack mode="generate" phase="polishing" progress={77} label="润色画面细节" />);
    expect(screen.getByText("画面润色")).toBeTruthy();
  });

  it("失败时进度条转红，不再显示已完成", () => {
    const { container } = render(
      <PhaseTrack mode="generate" phase="writing" progress={82} label="生成失败" failed />,
    );
    expect(container.querySelector(".bg-danger")).toBeTruthy();
    const steps = Array.from(container.querySelectorAll("span.rounded-full")).filter((el) =>
      el.className.includes("h-4"),
    );
    expect(steps.some((el) => el.className.includes("bg-brand"))).toBe(false);
  });
});

describe("PhaseTrack 取材流水线", () => {
  it("默认仍是四步取材流水线", () => {
    render(<PhaseTrack phase="kb" progress={30} label="检索知识库" />);
    for (const label of ["联网取材", "知识库", "规划大纲", "完成"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.queryByText("浏览器实测")).toBeNull();
  });
});